import { describe, expect, it } from "vitest";

import {
  PAGE_SIZE,
  apiQuery,
  archiveHref,
  isCanonical,
  isFiltered,
  paginate,
  append,
  parseArchive,
  withFilter,
} from "@/lib/archive";

const A = "00000000-0000-4000-8000-00000000000a";

function rows(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
  }));
}

describe("parseArchive", () => {
  it("hides aborted games unless asked, and asks the API for every other status by name", () => {
    const query = apiQuery(parseArchive({}));
    expect(query.getAll("status")).toEqual(["pending", "running", "paused", "finished"]);
    expect(apiQuery(parseArchive({ show: "aborted" })).getAll("status")).toEqual(["aborted"]);
    expect(apiQuery(parseArchive({ show: "all" })).getAll("status")).toEqual([]);
  });

  it("drops what it does not recognise rather than passing it on", () => {
    const filter = parseArchive({
      show: "banana",
      result: "1-0",
      ending: "flag_fall",
      sort: "random",
      before: "not-a-uuid",
      model: "a model with spaces",
      junk: "x",
    });
    expect(archiveHref(filter)).toBe("/games");
  });

  it("trims and bounds the search", () => {
    expect(parseArchive({ q: "  claude  " }).q).toBe("claude");
    expect(parseArchive({ q: "x".repeat(300) }).q).toHaveLength(100);
    expect(parseArchive({ q: "   " }).q).toBeUndefined();
  });

  it("accepts a cursor only if it is a game id", () => {
    expect(parseArchive({ before: A }).before).toBe(A);
    expect(parseArchive({ before: "1 OR 1=1" }).before).toBeUndefined();
  });
});

describe("apiQuery", () => {
  it("asks for one row more than a page, to learn whether there is a next one", () => {
    expect(apiQuery(parseArchive({})).get("limit")).toBe(String(PAGE_SIZE + 1));
  });

  it("sends a matchup as model and opponent, and a lone `vs` as a model", () => {
    const both = apiQuery(parseArchive({ model: "acme/alpha", vs: "acme/beta" }));
    expect([both.get("model"), both.get("opponent")]).toEqual(["acme/alpha", "acme/beta"]);

    const alone = apiQuery(parseArchive({ vs: "acme/beta" }));
    expect([alone.get("model"), alone.get("opponent")]).toEqual(["acme/beta", null]);
  });

  it("translates the page's words into the API's", () => {
    const query = apiQuery(
      parseArchive({
        players: "humans",
        ranked: "unranked",
        ending: "checkmate",
        event: "pool-free",
      }),
    );
    expect(query.get("kind")).toBe("humans");
    expect(query.get("ranked")).toBe("false");
    expect(query.get("termination")).toBe("checkmate");
    expect(query.get("tournament")).toBe("pool-free");
  });
});

describe("archiveHref", () => {
  it("leaves every default out, so the unfiltered archive has one address", () => {
    expect(archiveHref(parseArchive({ show: "played", sort: "newest" }))).toBe("/games");
  });

  it("round-trips: parsing its own output changes nothing", () => {
    const href = archiveHref(
      parseArchive({
        q: "gpt",
        show: "all",
        result: "draw",
        model: "acme/alpha",
        sort: "longest",
        before: A,
      }),
    );
    const again = archiveHref(
      parseArchive(Object.fromEntries(new URLSearchParams(href.split("?")[1]))),
    );
    expect(again).toBe(href);
  });
});

describe("withFilter", () => {
  it("returns to the first page, because a cursor belongs to the list it came from", () => {
    const paged = parseArchive({ before: A });
    expect(withFilter(paged, { result: "white" })).toBe("/games?result=white");
  });
});

describe("isFiltered", () => {
  it("is false for the default list at any page or sort, true for any narrowing", () => {
    expect(isFiltered(parseArchive({ sort: "longest", before: A }))).toBe(false);
    expect(isFiltered(parseArchive({ show: "aborted" }))).toBe(true);
    expect(isFiltered(parseArchive({ q: "x" }))).toBe(true);
  });
});

describe("isCanonical", () => {
  it("accepts the address `archiveHref` writes, in any order", () => {
    expect(isCanonical({})).toBe(true);
    expect(isCanonical({ sort: "longest", q: "gpt" })).toBe(true);
  });

  it("rejects what a form without JavaScript submits: empty fields and spelled-out defaults", () => {
    expect(isCanonical({ q: "", show: "played", result: "", sort: "newest" })).toBe(false);
    expect(isCanonical({ q: "  gpt " })).toBe(false);
    expect(isCanonical({ nonsense: "1" })).toBe(false);
  });
});

describe("paginate", () => {
  it("shows a page, and says there is more only when the extra row came back", () => {
    const full = paginate(rows(PAGE_SIZE + 1));
    expect(full.games).toHaveLength(PAGE_SIZE);
    expect(full.more).toBe(true);

    const last = paginate(rows(PAGE_SIZE));
    expect(last.games).toHaveLength(PAGE_SIZE);
    expect(last.more).toBe(false);
  });
});

describe("append", () => {
  it("adds the next page below, without a game that is already shown", () => {
    const [a, b, c] = rows(3);
    expect(append([a, b], [b, c])).toEqual([a, b, c]);
  });
});
