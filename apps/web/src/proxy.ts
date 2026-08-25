/**
 * Clerk's proxy (Next.js 16 renamed `middleware` to `proxy`).
 *
 * `clerkMiddleware()` only *populates* auth state — it protects nothing by itself, which is what
 * we want: every page here is public (AUTH-02), and the thing worth protecting lives behind the
 * FastAPI backend, which verifies the JWT itself. A check that runs only in the browser is not a
 * check.
 *
 * **It is skipped entirely when no publishable key is set.** `clerkMiddleware()` throws "Missing
 * publishableKey" on every request without one, and a proxy that throws takes the whole site with
 * it: a clone with no Clerk keys answered 500 on *every* route, including `/about` and the
 * leaderboard, which touch no auth at all. `AuthProvider` and `AccountBar` were each careful to
 * degrade to signed-out, and it made no difference, because nothing downstream of a throwing
 * proxy ever runs. Reading the same variable they read is what makes the documented
 * "left blank, the site runs signed-out" true.
 *
 * The `/__clerk/:path*` entry is not optional. It is Clerk's auto-proxy path, and without it in
 * the matcher Clerk's own flows have nowhere to route. Omitting it is how an earlier attempt at
 * this file ended up serving an empty 200 on every request.
 */

import { clerkMiddleware } from "@clerk/nextjs/server";

const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

export default clerkEnabled ? clerkMiddleware() : () => undefined;

export const config = {
  matcher: [
    // Skip Next.js internals and static files unless they appear in search params.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
