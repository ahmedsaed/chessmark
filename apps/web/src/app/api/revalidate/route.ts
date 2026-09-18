/**
 * The API's way of saying "that answer is stale now".
 *
 * **This is the mechanism the cached reads in `lib/api.ts` depend on.** Those reads carry a
 * `revalidate` measured in minutes, but the number is only a backstop: the real signal is this
 * endpoint, called by the worker the instant a game writes a `game_events` row (invariant 7). A
 * clock guesses when the leaderboard changed; the worker knows.
 *
 * `revalidateTag(tag, "max")` rather than the deprecated one-argument form, which expires the entry
 * outright and makes the next reader pay for a blocking refetch. `"max"` marks it stale and serves
 * the old answer while the new one is fetched behind it — so a game ending never puts a visitor in
 * a queue behind our own cache miss.
 *
 * Failure here is deliberately quiet on the caller's side and loud on ours: the worker treats a
 * failed POST the way it treats a failed Redis publish (best effort, after the commit), because a
 * cache that is briefly stale must never be able to fail a transaction that has already been
 * accepted. The fallback `revalidate` bounds how long that can matter.
 */

import { timingSafeEqual } from "node:crypto";

import { revalidateTag } from "next/cache";

import { isKnownTag } from "@/lib/cache-tags";

/**
 * Compared in constant time, because the obvious `===` leaks the secret one character at a time to
 * anything that can measure the response. The lengths are compared first and separately:
 * `timingSafeEqual` *throws* on a length mismatch rather than returning false, which would turn a
 * wrong-length guess into a 500 and tell the caller exactly that.
 */
function matches(provided: string, expected: string): boolean {
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(request: Request): Promise<Response> {
  const expected = process.env.REVALIDATE_SECRET;

  /* Unset is *refuse*, not *allow*. A deployment that forgot the variable would otherwise publish
     an unauthenticated cache-invalidation endpoint, and the failure mode of getting this backwards
     is that anybody on the internet can evict this site's cache in a loop. Local development
     leaves it unset and simply does without the notifications; the fallback revalidate covers it. */
  if (!expected) {
    return Response.json({ error: "revalidation is not configured" }, { status: 503 });
  }

  const provided = request.headers.get("authorization")?.replace(/^Bearer /, "") ?? "";
  if (!matches(provided, expected)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  let tags: unknown;
  try {
    tags = (await request.json())?.tags;
  } catch {
    return Response.json({ error: "expected a JSON body" }, { status: 400 });
  }

  if (!Array.isArray(tags)) {
    return Response.json({ error: "expected { tags: string[] }" }, { status: 400 });
  }

  /* Filtered against an allowlist rather than passed through. The names arrive from off this
     process, and `revalidateTag` will happily accept anything — a caller free to name any tag is a
     caller who can expire whatever it likes. Unknown names are dropped and reported back rather
     than failing the call, so one bad name in a batch does not discard the good ones. */
  const accepted = tags.filter((tag): tag is string => typeof tag === "string" && isKnownTag(tag));
  const rejected = tags.filter((tag) => !accepted.includes(tag as string));

  for (const tag of accepted) {
    revalidateTag(tag, "max");
  }

  return Response.json({ revalidated: accepted, ignored: rejected });
}
