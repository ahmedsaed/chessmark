/**
 * The allowlist is the security boundary, so it is what gets tested.
 *
 * `app/api/revalidate/route.ts` takes tag names from off the process and hands them to
 * `revalidateTag`. The secret stops a stranger calling it at all; this stops a caller that *has*
 * the secret from naming something it was never meant to reach. Both matter, and only one of them
 * is testable without a server.
 */

import { describe, expect, it } from "vitest";

import { FALLBACK_REVALIDATE, GAMES, KNOWN_TAGS, game, isKnownTag } from "@/lib/cache-tags";

describe("isKnownTag", () => {
  it("accepts every tag the reads actually attach", () => {
    for (const tag of KNOWN_TAGS) {
      expect(isKnownTag(tag), tag).toBe(true);
    }
  });

  it("accepts a per-game tag built by the helper that writes them", () => {
    expect(isKnownTag(game("3f2504e0-4f89-41d3-9a0c-0305e82c3301"))).toBe(true);
  });

  it("refuses a game tag that is not a uuid", () => {
    // The API composes these from a real `game_id`, so anything else arrived from somewhere else.
    expect(isKnownTag("game:*")).toBe(false);
    expect(isKnownTag("game:")).toBe(false);
    expect(isKnownTag("game:../../etc")).toBe(false);
  });

  it("refuses names nothing in this site tags with", () => {
    for (const tag of ["", "*", "ALL", "leaderboards", "game", "/leaderboard"]) {
      expect(isKnownTag(tag), tag).toBe(false);
    }
  });

  it("is case-sensitive on the fixed names, because `revalidateTag` is", () => {
    // A tag that differs only in case is a tag that silently invalidates nothing. Better refused
    // loudly here than accepted and quietly ineffective.
    expect(isKnownTag(GAMES)).toBe(true);
    expect(isKnownTag("Games")).toBe(false);
  });
});

describe("the fallback life", () => {
  it("is a real duration, because a zero would disable caching entirely", () => {
    // `next: { revalidate: 0 }` is Next's own spelling of "do not cache". If this ever became 0
    // the tags would still be attached, the site would still look correct, and every read would
    // silently go back to hitting the API — the exact regression this whole change removed.
    expect(FALLBACK_REVALIDATE).toBeGreaterThan(0);
  });
});
