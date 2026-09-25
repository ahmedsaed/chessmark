import { describe, expect, it } from "vitest";

import { shortName } from "@/lib/og/names";

describe("shortName", () => {
  it("drops the vendor and the price tier, which every row of the pool repeats", () => {
    expect(shortName("NVIDIA: Nemotron 3 Ultra (free)")).toBe("Nemotron 3 Ultra");
    expect(shortName("Google: Gemma 4 26B A4B  (free)")).toBe("Gemma 4 26B A4B");
  });

  it("leaves a name with neither alone — including a person's", () => {
    expect(shortName("Ling 3.0 Flash Fin")).toBe("Ling 3.0 Flash Fin");
    expect(shortName("Magnus")).toBe("Magnus");
  });

  it("keeps a colon that is part of the name rather than a vendor prefix", () => {
    expect(shortName("Re:Zero")).toBe("Re:Zero");
  });
});
