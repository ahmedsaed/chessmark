"""JWT verification, treated as adversarial input.

Every test here is an attack that has broken real systems. The suite generates its own RSA keypair
and serves its own JWKS from memory, so it never touches Clerk and never touches the network —
which is also the only way these attacks can be *written*: forging a token requires holding a key
Clerk would never give us.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chessmark.core.auth import (
    AuthError,
    ConfigurationError,
    Principal,
    TokenVerifier,
    bearer_token,
)

ISSUER = "https://clerk.chessmark.test"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(keypair: Any) -> dict[str, Any]:
    """The public half, in the shape Clerk publishes."""
    public = keypair.public_key()
    numbers = public.public_numbers()

    def b64(value: int) -> str:
        import base64

        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": b64(numbers.n),
                "e": b64(numbers.e),
            }
        ]
    }


@pytest.fixture
def verifier(jwks: dict[str, Any]) -> TokenVerifier:
    """A verifier backed by an in-memory JWKS, so nothing leaves the machine."""
    by_kid = {key["kid"]: jwt.PyJWK(key).key for key in jwks["keys"]}

    def resolve(token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in by_kid:
            raise jwt.PyJWKClientError(f"no key for kid {kid!r}")
        return by_kid[kid]

    return TokenVerifier(issuer=ISSUER, key_resolver=resolve)


def make_token(
    keypair: Any,
    *,
    subject: str = "user_abc123",
    expires_in: int = 300,
    issuer: str | None = ISSUER,
    algorithm: str = "RS256",
    kid: str | None = KID,
    key: Any = None,
    **extra: Any,
) -> str:
    now = dt.datetime.now(tz=dt.UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=expires_in)).timestamp()),
        **extra,
    }
    if issuer:
        claims["iss"] = issuer

    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key or keypair, algorithm=algorithm, headers=headers)


# ====================================================================== the happy path


def test_a_clerk_signed_token_is_accepted(verifier: TokenVerifier, keypair: Any) -> None:
    principal = verifier.verify(make_token(keypair, email="a@b.test", name="Ahmed"))

    assert principal.clerk_user_id == "user_abc123"
    assert principal.email == "a@b.test"
    assert principal.display_name == "Ahmed"


def test_optional_claims_may_be_absent(verifier: TokenVerifier, keypair: Any) -> None:
    """Clerk only includes email in the session token if the JWT template asks for it."""
    principal = verifier.verify(make_token(keypair))

    assert principal.clerk_user_id == "user_abc123"
    assert principal.email is None


# ====================================================================== forgery


def test_an_unsigned_token_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    """`alg: none`. The oldest JWT attack there is, and still worth a test — it works against any
    verifier that reads the algorithm out of the token it is trying to verify."""
    forged = jwt.encode({"sub": "attacker", "exp": 9999999999}, key="", algorithm="none")

    with pytest.raises(AuthError):
        verifier.verify(forged)


def test_the_algorithm_confusion_attack_is_rejected(
    verifier: TokenVerifier, jwks: dict[str, Any]
) -> None:
    """The dangerous one.

    An attacker takes the *public* key — which is public, by definition — and uses it as an HMAC
    secret to sign an HS256 token. A verifier that honours the header's `alg` will faithfully
    verify HS256 against the key it holds, and the key matches. Pinning the algorithm list is what
    stops it, so this test fails the moment someone adds an HMAC algorithm to `ALGORITHMS`.
    """
    forged = _forge_hs256(
        {"sub": "attacker", "exp": 9999999999, "iss": ISSUER},
        secret=_public_pem(jwks),
        kid=KID,
    )

    with pytest.raises(AuthError):
        verifier.verify(forged)


def test_algorithm_confusion_is_rejected_even_when_the_key_is_pem_bytes(
    jwks: dict[str, Any],
) -> None:
    """The same attack against the key format that makes it work elsewhere.

    In languages and libraries without a guard, handing the verifier PEM **bytes** is what turns
    algorithm confusion from a crash into an authentication bypass: those bytes are a perfectly
    valid HMAC secret, so the forged signature verifies and the attacker is whoever they claimed.

    PyJWT happens to defend this twice — our pin refuses HS256, and `HMACAlgorithm.prepare_key`
    independently refuses any PEM-shaped secret. So this test does **not** demonstrate a bypass if
    the pin were removed; PyJWT would still catch it. It is here to pin the *behaviour* rather than
    to prove the pin is load-bearing — the test above does that, and fails the moment `ALGORITHMS`
    admits an HMAC algorithm.

    Both are worth keeping. The second guard is PyJWT's to remove, not ours.
    """
    pem = _public_pem(jwks)
    verifier = TokenVerifier(issuer=ISSUER, key_resolver=lambda _token: pem)

    forged = _forge_hs256(
        {"sub": "attacker", "exp": 9999999999, "iss": ISSUER}, secret=pem, kid=KID
    )

    with pytest.raises(AuthError):
        verifier.verify(forged)


def test_a_token_signed_by_the_wrong_key_is_rejected(verifier: TokenVerifier) -> None:
    """Right shape, right claims, wrong issuer key — an attacker who ran the same code we did."""
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = make_token(attacker, subject="attacker", key=attacker)

    with pytest.raises(AuthError):
        verifier.verify(forged)


def test_a_tampered_payload_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    """Proves the signature is actually checked rather than merely parsed."""
    import base64

    token = make_token(keypair)
    header, payload, signature = token.split(".")

    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    decoded["sub"] = "user_someone_else"
    swapped = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()

    with pytest.raises(AuthError):
        verifier.verify(f"{header}.{swapped}.{signature}")


# ====================================================================== claim checks


def test_an_expired_token_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    with pytest.raises(AuthError):
        verifier.verify(make_token(keypair, expires_in=-3600))


def test_a_token_from_another_issuer_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    """A valid Clerk token from somebody else's Clerk instance is not a token for us."""
    with pytest.raises(AuthError):
        verifier.verify(make_token(keypair, issuer="https://clerk.someone-else.test"))


def test_a_token_without_an_expiry_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    """A token that never expires cannot be revoked by waiting, so it must not be honoured."""
    forever = jwt.encode(
        {"sub": "x", "iss": ISSUER}, keypair, algorithm="RS256", headers={"kid": KID}
    )

    with pytest.raises(AuthError):
        verifier.verify(forever)


def test_a_token_without_a_subject_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    """There is nobody to attribute the spend to."""
    anonymous = jwt.encode(
        {"iss": ISSUER, "exp": 9999999999}, keypair, algorithm="RS256", headers={"kid": KID}
    )

    with pytest.raises(AuthError):
        verifier.verify(anonymous)


def test_an_unknown_signing_key_is_rejected(verifier: TokenVerifier, keypair: Any) -> None:
    with pytest.raises(AuthError):
        verifier.verify(make_token(keypair, kid="a-kid-clerk-never-published"))


@pytest.mark.parametrize("token", ["", "garbage", "a.b", "a.b.c.d", "....."])
def test_malformed_tokens_are_rejected(verifier: TokenVerifier, token: str) -> None:
    with pytest.raises(AuthError):
        verifier.verify(token)


# ====================================================================== configuration


def test_an_unconfigured_verifier_raises_a_configuration_error(keypair: Any) -> None:
    """Not an `AuthError`: a missing JWKS URL is our misconfiguration, and answering 401 would
    send every user to log in again to fix a problem on our side."""
    unconfigured = TokenVerifier(jwks_url="")

    assert not unconfigured.configured
    with pytest.raises(ConfigurationError):
        unconfigured.verify(make_token(keypair))


# ====================================================================== header parsing


def test_a_bearer_token_is_extracted() -> None:
    assert bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


def test_the_scheme_is_case_insensitive() -> None:
    """RFC 7235 says so. A client sending `bearer` is correct, not hostile."""
    assert bearer_token("bearer abc.def.ghi") == "abc.def.ghi"


@pytest.mark.parametrize(
    "header",
    [None, "", "abc.def.ghi", "Basic dXNlcjpwYXNz", "Bearer", "Bearer   "],
)
def test_anything_else_is_not_a_bearer_token(header: str | None) -> None:
    with pytest.raises(AuthError):
        bearer_token(header)


def test_a_principal_needs_a_subject() -> None:
    with pytest.raises(AuthError):
        Principal.from_claims({"email": "a@b.test"})


def _forge_hs256(claims: dict[str, Any], *, secret: bytes, kid: str) -> str:
    """Assemble an HS256 token by hand.

    PyJWT refuses to sign HMAC with a PEM — a guard on the *signing* side. An attacker is not using
    PyJWT, so the test must not either: the point is what our verifier does when handed the bytes.
    """
    import base64
    import hashlib
    import hmac

    def segment(payload: dict[str, Any]) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
            b"="
        )

    signing_input = b".".join(
        [segment({"alg": "HS256", "typ": "JWT", "kid": kid}), segment(claims)]
    )
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()

    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def _public_pem(jwks: dict[str, Any]) -> bytes:
    """Reconstruct the PEM an attacker would scrape from the published JWKS."""
    from cryptography.hazmat.primitives import serialization

    key = jwt.PyJWK(jwks["keys"][0]).key
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
