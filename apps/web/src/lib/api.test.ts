import { afterEach, describe, expect, it, vi } from "vitest";

import { getGame, listGames } from "@/lib/api";

function respond(status: number, body: unknown = {}) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getGame", () => {
  it("returns the game on 200", async () => {
    vi.stubGlobal("fetch", respond(200, { id: "abc" }));
    expect(await getGame("abc")).toEqual({ id: "abc" });
  });

  it("returns null for a valid id that names no game", async () => {
    vi.stubGlobal("fetch", respond(404));
    expect(await getGame("00000000-0000-0000-0000-000000000000")).toBeNull();
  });

  /**
   * The regression: FastAPI answers 422 for a path param that is not a UUID, so a mistyped URL
   * used to throw and render a 500 instead of the not-found page.
   */
  it("returns null for an id that is not a UUID", async () => {
    vi.stubGlobal("fetch", respond(422));
    expect(await getGame("does-not-exist")).toBeNull();
  });

  it("still throws on a server error", async () => {
    vi.stubGlobal("fetch", respond(500));
    await expect(getGame("abc")).rejects.toThrow();
  });
});

describe("listGames", () => {
  it("returns an empty list rather than throwing when the API is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    expect(await listGames()).toEqual([]);
  });
});
