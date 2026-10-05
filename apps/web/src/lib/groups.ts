import type { LeaderboardRow } from "@/lib/types";

/**
 * The two leaderboards (ADR-0063): chat models, and decision models.
 *
 * They are ranked separately because they are not playing the same game — a decision model is
 * handed the legal moves and facts about each, a chat model has to find a legal move itself — and
 * because the anchor trial showed the decision models win material against random play yet rarely
 * convert it. One scale would rank two different abilities as though they were one.
 *
 * Chat is the default everywhere a ranking is shown without a choice: the landing page, the social
 * card, and `/leaderboard` with no `?models=`.
 */
export type ModelGroup = "chat" | "decision";

export const GROUPS: readonly ModelGroup[] = ["chat", "decision"];

export const GROUP_LABEL: Record<ModelGroup, string> = {
  chat: "Chat models",
  decision: "Decision models",
};

/** Which leaderboard a row belongs on. Anything that is not a decision model is a chat model. */
export function groupOf(row: Pick<LeaderboardRow, "runtime">): ModelGroup {
  return row.runtime === "decision" ? "decision" : "chat";
}

/** The rows of one group, in the order the API ranked them — which is already per group. */
export function rowsOf<T extends Pick<LeaderboardRow, "runtime">>(rows: T[], group: ModelGroup): T[] {
  return rows.filter((row) => groupOf(row) === group);
}

/** `?models=decision` selects the decision table; anything else, or nothing, is chat. */
export function parseGroup(value: string | string[] | undefined): ModelGroup {
  return value === "decision" ? "decision" : "chat";
}
