# 0006. Clerk for authentication

**Status:** Accepted
**Date:** 2026-08-15

## Context

Chessmark is public. Watching games must need no account, but *starting* one spends real money, so
it must be authenticated and quota-limited. We need a Next.js frontend session and a way for the
FastAPI backend to verify identity on every protected request.

Auth is a place where building it yourself is a slow, high-risk way to acquire a liability.

## Decision

Clerk. The frontend uses Clerk's Next.js SDK; the backend verifies Clerk-issued JWTs against cached
JWKS on every protected request. Users are provisioned in our `users` table on first login via
webhook.

Auth0 was the owner's initial suggestion and would work identically at the architectural level;
Clerk was chosen for materially better Next.js ergonomics and a more generous free tier at our scale.

## Alternatives considered

- **Auth0.** Equally solid, and the JWKS verification pattern on the FastAPI side is the same. More
  configuration ceremony and a tighter free tier.
- **Supabase Auth.** Free, and bundles Postgres. But we already run our own Postgres, and its
  Next.js integration is rougher.
- **Better Auth / self-hosted.** No vendor and no per-user cost ever, but we'd own session security,
  email delivery, and account recovery — real work, on the critical path, in a domain where mistakes
  are expensive.

## Consequences

- The identity layer is a solved problem. Social login, MFA, and account management arrive for free.
- The backend stays stateless: verify a JWT via cached JWKS, no session store, no shared secret.
- Vendor dependency. Mitigation: `clerk_user_id` is the only Clerk-specific field in our schema, and
  all authorisation logic keys off our own `users.id`. Migrating providers would mean re-issuing
  identities, not rewriting the app.
- Clerk is a hard dependency for *starting* games. Spectating, replays, and the leaderboard must keep
  working if Clerk is down — that's a deliberate requirement (AUTH-02), not an accident.
