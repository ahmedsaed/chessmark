"""Clerk JWT verification (AUTH-01).

The backend holds no session state. Every protected request carries a Clerk-issued JWT, which is
verified against Clerk's published JWKS — so the API tier stays stateless and horizontally
scalable (ADR-0006), and a Clerk outage cannot log anyone out mid-request.

**Everything here is written assuming the token is hostile.** The attacks this must survive are
well known and all of them are cheap to get wrong:

- `alg: none` — a token with no signature at all.
- **Algorithm confusion** — an attacker signs with HS256 using the *public* key as the HMAC secret.
  A verifier that trusts the header's `alg` accepts it, because the public key is public.
- **`kid` cache poisoning** — an unknown `kid` triggers a JWKS refetch, so an attacker who can
  spray unknown `kid`s turns our verifier into a request amplifier against Clerk.

The defence for the first two is the same one line: the accepted algorithm is fixed here and the
header's `alg` is never consulted. The third is a refetch cooldown.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

#: Resolves a token's `kid` to the key that should verify it. Injectable so tests can supply a
#: local JWKS — the suite must never reach Clerk, for the same reason it never reaches a provider.
KeyResolver = Callable[[str], Any]

#: Clerk signs with RS256. This is a fixed list, never read from the token — see the module
#: docstring. Widening it to include an HMAC algorithm would reintroduce algorithm confusion.
ALGORITHMS = ["RS256"]

#: How long an unknown `kid` is allowed to force a JWKS refetch. Clerk rotates keys rarely, so a
#: legitimate new `kid` tolerates a short wait; an attacker spraying `kid`s gets one fetch.
REFETCH_COOLDOWN_SECONDS = 60.0

#: Slack for clock drift between Clerk and us, in seconds. Small on purpose: a generous window
#: extends the life of every stolen token.
LEEWAY_SECONDS = 10


class AuthError(Exception):
    """A token was absent, malformed, expired, or not signed by Clerk."""


class ConfigurationError(Exception):
    """Auth is required but not configured. Distinct from `AuthError`: this is our fault, and it
    must surface as a 500 rather than a 401 that would send the user to log in again pointlessly."""


@dataclass(frozen=True, slots=True)
class Principal:
    """The verified caller.

    Deliberately thin. `clerk_user_id` is the *only* Clerk-specific value that travels into the
    application, and every authorisation decision keys off our own `users.id` — which is what keeps
    the vendor dependency shallow enough to escape (ADR-0006).
    """

    clerk_user_id: str
    email: str | None = None
    display_name: str | None = None

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> Principal:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthError("token has no subject")

        name = claims.get("name") or claims.get("full_name")
        return cls(
            clerk_user_id=subject,
            email=_optional_str(claims.get("email")),
            display_name=_optional_str(name),
        )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class TokenVerifier:
    """Verifies Clerk JWTs against cached JWKS.

    One instance per process. `PyJWKClient` does the key caching; this adds the algorithm pinning,
    the issuer check, and the refetch cooldown.
    """

    def __init__(
        self,
        *,
        jwks_url: str = "",
        issuer: str | None = None,
        audience: str | None = None,
        cooldown_seconds: float = REFETCH_COOLDOWN_SECONDS,
        key_resolver: KeyResolver | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._cooldown = cooldown_seconds
        self._last_refetch = 0.0
        self._client: PyJWKClient | None = None
        self._resolve = key_resolver

    @property
    def configured(self) -> bool:
        return bool(self._jwks_url) or self._resolve is not None

    def _keys(self) -> PyJWKClient:
        if not self._jwks_url:
            raise ConfigurationError("CLERK_JWKS_URL is not set")

        if self._client is None:
            # `lifespan` is the process lifetime; PyJWKClient caches keys in memory and refreshes
            # them itself when it meets a `kid` it does not hold.
            self._client = PyJWKClient(self._jwks_url, cache_keys=True, lifespan=3600)
        return self._client

    def verify(self, token: str) -> Principal:
        """Verify a bearer token and return the caller, or raise `AuthError`."""
        if not token or token.count(".") != 2:
            raise AuthError("malformed token")

        try:
            signing_key = self._signing_key(token)
        except ConfigurationError:
            raise
        except Exception as error:  # any key lookup failure is a rejection, not a crash
            raise AuthError(f"no usable signing key: {error}") from error

        try:
            claims = jwt.decode(
                token,
                signing_key,
                # Fixed, never taken from the token header. This single argument is what closes
                # both `alg: none` and the HS256-with-public-key confusion attack.
                algorithms=ALGORITHMS,
                issuer=self._issuer,
                audience=self._audience,
                leeway=LEEWAY_SECONDS,
                options={
                    "require": ["exp", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": self._issuer is not None,
                    # Clerk session tokens carry no `aud` unless one is configured, and demanding
                    # an absent claim would reject every valid token.
                    "verify_aud": self._audience is not None,
                },
            )
        except jwt.PyJWTError as error:
            raise AuthError(str(error)) from error

        return Principal.from_claims(claims)

    def _signing_key(self, token: str) -> Any:
        """The key for this token's `kid`, refetching at most once per cooldown.

        Without the cooldown, a stream of tokens bearing random `kid`s makes us issue a JWKS fetch
        per request — we would be the amplifier in an attack on Clerk, and on ourselves.
        """
        if self._resolve is not None:
            return self._resolve(token)

        client = self._keys()

        try:
            return client.get_signing_key_from_jwt(token).key
        except (jwt.PyJWKClientError, httpx.HTTPError):
            now = time.monotonic()
            if now - self._last_refetch < self._cooldown:
                raise
            self._last_refetch = now
            # A fresh client drops the cache, which is the only way to force PyJWKClient to refetch.
            self._client = None
            return self._keys().get_signing_key_from_jwt(token).key


def bearer_token(header: str | None) -> str:
    """Pull the token out of an `Authorization` header.

    The scheme comparison is case-insensitive because RFC 7235 says it is, and a client sending
    `bearer` in lower case is not an attacker — it is a correct client we would otherwise reject.
    """
    if not header:
        raise AuthError("missing Authorization header")

    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Authorization header is not a bearer token")

    return token.strip()
