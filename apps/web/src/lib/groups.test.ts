import { describe, expect, it } from "vitest";

import { groupOf, parseGroup, rowsOf } from "@/lib/groups";

describe("model groups (ADR-0063)", () => {
  it("files a decision model on its own board and everything else on the chat board", () => {
    expect(groupOf({ runtime: "decision" })).toBe("decision");
    expect(groupOf({ runtime: "llm" })).toBe("chat");
  });

  it("keeps the API's order within a group", () => {
    const rows = [
      { runtime: "llm" as const, id: "a" },
      { runtime: "decision" as const, id: "b" },
      { runtime: "llm" as const, id: "c" },
    ];
    expect(rowsOf(rows, "chat").map((r) => r.id)).toEqual(["a", "c"]);
    expect(rowsOf(rows, "decision").map((r) => r.id)).toEqual(["b"]);
  });

  it("defaults to chat for a missing, repeated or unknown parameter", () => {
    expect(parseGroup(undefined)).toBe("chat");
    expect(parseGroup("nonsense")).toBe("chat");
    expect(parseGroup(["decision", "chat"])).toBe("chat");
    expect(parseGroup("decision")).toBe("decision");
  });
});
