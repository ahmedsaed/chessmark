import { describe, expect, it } from "vitest";

import { DEFAULT_PLIES, pliesFrom } from "@/lib/limits";

describe("pliesFrom", () => {
  it("is the default when left empty or unreadable", () => {
    expect(pliesFrom("")).toBe(DEFAULT_PLIES);
    expect(pliesFrom("abc")).toBe(DEFAULT_PLIES);
  });

  it("is held to what the API accepts", () => {
    expect(pliesFrom("1")).toBe(2);
    expect(pliesFrom("5000")).toBe(1000);
    expect(pliesFrom("120.4")).toBe(120);
  });
});
