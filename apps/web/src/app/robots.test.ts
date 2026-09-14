import { describe, expect, it } from "vitest";

import robots from "@/app/robots";
import { siteUrl } from "@/lib/site";

describe("robots", () => {
  it("points crawlers at the sitemap", () => {
    expect(robots().sitemap).toBe(`${siteUrl}/sitemap.xml`);
  });

  /* Clerk's catch-all auth segments. Nothing there is worth indexing, and a sign-in page ranking
     for the site's own name is worse than nothing. */
  it("keeps the auth routes out of the index", () => {
    const rules = robots().rules as { disallow?: string | string[] };
    expect(rules.disallow).toContain("/sign-in");
    expect(rules.disallow).toContain("/sign-up");
  });

  it("allows everything else", () => {
    const rules = robots().rules as { allow?: string | string[] };
    expect(rules.allow).toBe("/");
  });
});
