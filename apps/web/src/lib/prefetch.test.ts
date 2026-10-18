import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Every `<Link>` to a model page turns prefetch off (FRONTEND.md).
 *
 * A visible link to a dynamic route prefetches in a loop: the payload comes back `no-store`, the
 * router keeps nothing, and asks again. Nothing looks wrong and nothing reaches the console. The
 * model page's head-to-head table shipped without the prop and asked for its first two opponents'
 * pages 1,607 times each while Lighthouse watched. `site.spec.ts` catches the behaviour in a
 * browser, on the pages its fixtures reach; this catches the cause on every page, in a second.
 *
 * Model links only. A tournament page is dynamic too, but measured on a production build its
 * links were asked for twice and settled, where a model link was asked for ~650 times in six
 * seconds — so the prop is the documented fix for one route, not a rule about every route.
 * `part.href` is `lib/prose`'s links, which can be either and so take the prop anyway.
 */

const ROOT = path.resolve(__dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === "node_modules" ? [] : sources(full);
    return full.endsWith(".tsx") && !full.endsWith(".test.tsx") ? [full] : [];
  });
}

/** Each `<Link …>` opening tag, read to the `>` that closes it outside any `{…}`. */
function linkTags(text: string): string[] {
  const tags: string[] = [];
  let from = 0;
  for (;;) {
    const start = text.indexOf("<Link", from);
    if (start < 0) return tags;
    let depth = 0;
    let end = start;
    for (; end < text.length; end++) {
      const c = text[end];
      if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    tags.push(text.slice(start, end + 1));
    from = end + 1;
  }
}

describe("links to dynamic pages", () => {
  it("turn prefetch off wherever they point at a model", () => {
    const offenders = sources(ROOT).flatMap((file) =>
      linkTags(readFileSync(file, "utf8"))
        .filter((tag) => /\/models\/\$\{|href=\{part\.href\}/.test(tag))
        .filter((tag) => !tag.includes("prefetch={false}"))
        .map((tag) => `${path.relative(ROOT, file)}: ${tag.split("\n")[0]}…`),
    );
    expect(offenders).toEqual([]);
  });
});
