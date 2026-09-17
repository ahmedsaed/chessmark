/**
 * Whether this request needs Clerk loaded at all.
 *
 * **Clerk's core is 87 KiB and most visitors never sign in.** Removing the prebuilt UI took the
 * page from 619 KiB to 337; this is the rest of it. A signed-out reader on `/leaderboard` has no
 * identity to establish, nothing to show for one, and no reason to download a client for it.
 *
 * Two questions decide it, and neither requires Clerk:
 *
 * * **Is there a session?** `__client_uat` is Clerk's own signed-out marker — a timestamp when a
 *   session exists and `0` when it does not. It is set by `clerkMiddleware`, readable as an
 *   ordinary cookie, and documented as exactly this: a cheap way to know whether to bother.
 * * **Is this a route about identity?** Signing in, signing up, coming back from Google, or the
 *   profile page. Those need Clerk whether or not a session exists yet — that is the point of them.
 *
 * Anything else renders with no Clerk on the page at all, and the header draws a signed-out bar
 * from the same cookie rather than from a hook.
 */

/** Routes that mount Clerk regardless of session, because establishing one is what they are for. */
const IDENTITY_ROUTES = ["/sign-in", "/sign-up", "/sso-callback", "/profile"];

export function isIdentityRoute(pathname: string): boolean {
  return IDENTITY_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
}

/**
 * Whether Clerk's own cookie says a session exists.
 *
 * `"0"` is Clerk's explicit "signed out" and is the value a visitor who has signed out before will
 * carry, so it must be treated as absent rather than as "a cookie is present".
 */
export function hasSessionCookie(value: string | undefined): boolean {
  return Boolean(value) && value !== "0";
}

export function needsClerk(pathname: string, cookie: string | undefined): boolean {
  return isIdentityRoute(pathname) || hasSessionCookie(cookie);
}
