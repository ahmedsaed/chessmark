import { describe, expect, it } from "vitest";

import { canPay, formatBalance } from "@/lib/credit";

describe("formatBalance", () => {
  it("writes a balance in dollars and cents", () => {
    expect(formatBalance("4.36")).toBe("$4.36");
    expect(formatBalance("0E-8")).toBe("$0.00");
    expect(formatBalance("12.5")).toBe("$12.50");
  });

  it("does not round a balance under a cent to an empty-looking zero", () => {
    expect(formatBalance("0.00420000")).toBe("$0.0042");
  });

  it("says a balance a turn below zero as it is", () => {
    expect(formatBalance("-0.0031")).toBe("−$0.0031");
    expect(formatBalance("-1.5")).toBe("−$1.50");
  });
});

describe("canPay", () => {
  it("is anything above zero, however small", () => {
    expect(canPay("0.0001")).toBe(true);
    expect(canPay("0E-8")).toBe(false);
    expect(canPay("-0.002")).toBe(false);
  });
});
