import { describe, expect, it } from "vitest";

import { originFromEnv } from "@/lib/env";

describe("originFromEnv", () => {
  it("uses the value when there is one", () => {
    expect(originFromEnv("https://api.chessmark.dev", "http://localhost:8010")).toBe(
      "https://api.chessmark.dev",
    );
  });

  it("falls back when the variable is unset", () => {
    expect(originFromEnv(undefined, "http://localhost:8010")).toBe("http://localhost:8010");
  });

  /**
   * The regression, and the reason this function exists. An unset GitHub Actions variable is not
   * absent by the time it reaches the build — it is `""`, which `??` keeps, which made every
   * fetch relative and hung `next build` on a page that mentions no URL.
   */
  it("falls back when the variable is set but empty", () => {
    expect(originFromEnv("", "http://localhost:8010")).toBe("http://localhost:8010");
  });

  it("falls back when the variable is only whitespace", () => {
    expect(originFromEnv("   ", "http://localhost:8010")).toBe("http://localhost:8010");
  });

  it("strips a trailing slash, so a path is never joined onto one", () => {
    expect(originFromEnv("https://api.chessmark.dev/", "x")).toBe("https://api.chessmark.dev");
    expect(originFromEnv("https://api.chessmark.dev///", "x")).toBe("https://api.chessmark.dev");
  });

  it("strips a trailing slash from the fallback too", () => {
    expect(originFromEnv(undefined, "http://localhost:8010/")).toBe("http://localhost:8010");
  });
});
