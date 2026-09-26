import type { Runtime } from "@/lib/types";

/**
 * Marks a decision model wherever a model is named (ADR-0049).
 *
 * **Only a decision model is marked.** A chat model is the default the whole site was written
 * for, and labelling every row "llm" would be noise on nine rows in ten. A decision model plays a
 * different task — every legal move is handed to it, described, and it cannot make an illegal one
 * — and it shares the leaderboard with the chat models, so a reader comparing the two needs to
 * see which is which without opening anything.
 */
export function RuntimeBadge({ runtime, className = "" }: { runtime: Runtime; className?: string }) {
  if (runtime !== "decision") return null;
  return (
    <span
      title="A decision model: shown every legal move, each described, and asked for a probability on each. It writes no text and cannot play an illegal move."
      className={`inline-block flex-none border border-machine-deep px-1 py-px font-mono text-label uppercase tracking-wider text-machine ${className}`}
    >
      decision
    </span>
  );
}
