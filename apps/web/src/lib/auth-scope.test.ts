/**
 * Whether a request gets Clerk at all.
 *
 * Two lines of logic guarding 87 KiB and, in the other direction, the entire signed-in experience.
 * Getting it wrong in one direction ships a client nobody needs; getting it wrong in the other
 * signs somebody out of a page that should know them. That asymmetry is why this is tested and why
 * the cases below are mostly about *not* skipping Clerk.
 */

import { describe, expect, it } from "vitest";

import { hasSessionCookie, isIdentityRoute, needsClerk } from "@/lib/auth-scope";

describe("hasSessionCookie", () => {
  it("reads a timestamp as a session", () => {
    expect(hasSessionCookie("1758124800")).toBe(true);
  });

  it("treats Clerk's explicit zero as signed out", () => {
    /* **`"0"` is the value a visitor who has signed out before carries**, not an absent cookie.
       Testing for presence alone would mount Clerk for every previously-signed-in reader for ever,
       which is most of the saving gone to a truthy string. */
    expect(hasSessionCookie("0")).toBe(false);
  });

  it("treats an absent or empty cookie as signed out", () => {
    expect(hasSessionCookie(undefined)).toBe(false);
    expect(hasSessionCookie("")).toBe(false);
  });
});

describe("isIdentityRoute", () => {
  it("claims the routes whose purpose is establishing a session", () => {
    for (const route of ["/sign-in", "/sign-up", "/sso-callback", "/profile"]) {
      expect(isIdentityRoute(route), route).toBe(true);
    }
  });

  it("claims their sub-paths too", () => {
    /* Clerk's own flows route *below* these — `/sign-in/factor-one`, `/sign-up/continue` — and a
       prefix that only matched exactly would drop the provider mid-sign-in. */
    expect(isIdentityRoute("/sign-in/factor-one")).toBe(true);
    expect(isIdentityRoute("/sign-up/verify-email-address")).toBe(true);
  });

  it("does not claim a route that merely starts with the same letters", () => {
    // `/sign-in-guide` would be a reading page, not an identity one.
    expect(isIdentityRoute("/sign-in-guide")).toBe(false);
    expect(isIdentityRoute("/profiles")).toBe(false);
  });

  it("leaves the reading surface alone", () => {
    for (const route of ["/", "/leaderboard", "/models", "/tournaments", "/about", "/play"]) {
      expect(isIdentityRoute(route), route).toBe(false);
    }
  });
});

describe("needsClerk", () => {
  it("mounts for a signed-in reader anywhere", () => {
    // The header has to show their credits and a way out, on every page.
    expect(needsClerk("/leaderboard", "1758124800")).toBe(true);
  });

  it("mounts on an identity route with no session yet", () => {
    // Signing in is exactly the case where there is no cookie and Clerk is still required.
    expect(needsClerk("/sign-in", undefined)).toBe(true);
    expect(needsClerk("/sign-up", "0")).toBe(true);
  });

  it("skips it for a signed-out reader on a reading page", () => {
    // The whole point: 87 KiB not downloaded by somebody with no identity to establish.
    expect(needsClerk("/leaderboard", undefined)).toBe(false);
    expect(needsClerk("/", "0")).toBe(false);
  });

  it("skips it on /play for a signed-out reader", () => {
    /* `/play` shows a sign-in prompt to a signed-out visitor, and the prompt is a link. It used to
       ask `<Show when="signed-out">`, which is a hook's answer to a question the cookie already
       settled — and which would have forced the client onto the page to be told it. */
    expect(needsClerk("/play", undefined)).toBe(false);
  });
});
