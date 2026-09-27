import { describe, expect, it } from "vitest";

import { paddleConfigFrom } from "@/lib/paddle";

describe("paddleConfigFrom", () => {
  it("is off, not an error, when no token is configured", () => {
    expect(paddleConfigFrom(undefined, undefined)).toBeNull();
    expect(paddleConfigFrom("", "production")).toBeNull();
  });

  it("refuses to guess the environment for a token", () => {
    /* The failure this prevents: a sandbox token defaulted to production, or a live one to sandbox —
       either way a checkout that fails in front of a buyer, or test purchases that are real. */
    expect(() => paddleConfigFrom("test_abc", undefined)).toThrow(/NEXT_PUBLIC_PADDLE_ENV/);
    expect(() => paddleConfigFrom("test_abc", "staging")).toThrow(/NEXT_PUBLIC_PADDLE_ENV/);
  });

  it("refuses a token from the other environment", () => {
    expect(() => paddleConfigFrom("test_abc", "production")).toThrow(/production token/);
    expect(() => paddleConfigFrom("live_abc", "sandbox")).toThrow(/sandbox token/);
  });

  it("accepts a token with its own environment", () => {
    expect(paddleConfigFrom("test_abc", "sandbox")).toEqual({
      token: "test_abc",
      environment: "sandbox",
    });
    expect(paddleConfigFrom("live_abc", "production")).toEqual({
      token: "live_abc",
      environment: "production",
    });
  });
});
