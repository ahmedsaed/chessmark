import Link from "next/link";

import { getLeaderboard } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Methodology · Chessmark",
  description:
    "How Chessmark ranks language models, which games are excluded and why, and where the ranking is weak.",
};

/**
 * How the ranking works, and where it is weak (BENCH-10).
 *
 * The second half is the point. Every benchmark can describe its method; the ones worth trusting
 * also say what their numbers cannot support. Everything in "Where this is weak" is a limitation
 * we hit while building, not a hypothetical.
 */
export default async function MethodologyPage() {
  const board = await getLeaderboard();
  const finished = board.games_counted + board.excluded.length;

  return (
    <main className="mx-auto w-full max-w-[760px] px-5 py-10">
      <Link
        href="/leaderboard"
        className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-faint transition-colors hover:text-accent"
      >
        ← Leaderboard
      </Link>

      <h1 className="mt-4 font-serif text-4xl leading-tight text-ink">Methodology</h1>
      <p className="mt-4 text-ink-dim">
        What the numbers mean, which games produced them, and what they cannot tell you.
      </p>

      <Section title="What is being measured">
        <p>
          Two models play chess through tools. Neither can touch the board directly: they call{" "}
          <Code>make_move</Code>, and the server validates every move with a real chess engine. An
          illegal move is rejected with the complete list of legal moves and the model may try
          again. After five rejections in one turn it forfeits.
        </p>
        <p>
          That makes this a benchmark of <strong>agentic reliability over a long horizon</strong>{" "}
          rather than of chess strength. The interesting number is not who won — it is how often a
          model proposed something impossible, and whether it could still operate its tools on move
          sixty.
        </p>
      </Section>

      <Section title="Ratings">
        <p>
          <strong>Glicko-2</strong>, implemented from Glickman&rsquo;s paper. Elo would give an
          order; Glicko-2 gives an order <em>and</em> a deviation, which is why every rating is
          printed as <Code>1650 ± 40</Code>. A model with three games and one with three hundred
          cannot honestly be shown as equally well known.
        </p>
        <p>
          Ratings are computed over <strong>rating periods of one UTC day</strong>, in batches, as
          the system specifies — rating game by game gives a different and less defensible answer.
          A period in which a contestant plays nothing widens its deviation: a rating from March is
          not still worth ± 40 in December.
        </p>
        <p>
          The whole table is <strong>recomputed from scratch</strong> on every request. Ratings are
          a pure function of the games behind them, and a stored number that had drifted from that
          function would be undetectable.
        </p>
      </Section>

      <Section title="A contestant is a model at a precision">
        <p>
          The same weights served at 4-bit and at 8-bit are <strong>different entrants</strong> and
          are ranked separately. Averaging them would produce a number describing neither.
        </p>
        <p>
          Each seat is also <strong>pinned to one endpoint</strong> for the whole game, chosen by
          uptime. Before that was enforced, one 80-ply game was served by two different providers —
          a blend nothing can reproduce. Providers are not interchangeable even at identical
          precision: one endpoint was measured returning malformed tool calls on roughly one call
          in six while two others did not fail once.
        </p>
      </Section>

      <Section title="Which games count">
        <p>
          A game counts only if both models were genuinely tested and the result is reproducible.
          Of {finished} finished games, <strong>{board.games_counted}</strong> counted and{" "}
          <strong>{board.excluded.length}</strong> did not. Every exclusion is listed with its
          reason on the{" "}
          <Link href="/leaderboard" className="text-accent underline-offset-4 hover:underline">
            leaderboard
          </Link>
          .
        </p>
        <ul className="mt-3 flex list-disc flex-col gap-1.5 pl-5">
          <li>
            <strong>Harness stops do not count.</strong> A ply cap, a spend budget, or a provider we
            could not reach are our decisions, not the models&rsquo;. Two such games turned out to
            be hiding a resignation and a checkmate one move away.
          </li>
          <li>
            <strong>Forfeits do count.</strong> Running out of illegal-move retries, or never
            calling a tool, is failing at the task. Excluding those would leave a ranking that
            measures only chess.
          </li>
          <li>
            <strong>Unranked games do not count.</strong> Trash talk is delivered into the
            opponent&rsquo;s transcript, and models demonstrably change their moves in response to
            it. Exhibition games keep it on and stay on the site; they simply do not rate.
          </li>
          <li>
            <strong>Floating aliases cannot be ranked.</strong> A <Code>~model-latest</Code> pointer
            names different weights over time.
          </li>
        </ul>
      </Section>

      <Section title="Where this is weak">
        <p className="text-ink">
          Every item here is a limitation we ran into, not a hypothetical.
        </p>
        <ul className="mt-3 flex list-disc flex-col gap-1.5 pl-5">
          <li>
            <strong>Small samples.</strong> With a handful of games per contestant, the deviations
            are wide and the order is a suggestion. Read <Code>± </Code> before reading the rank.
          </li>
          <li>
            <strong>Colour is not balanced.</strong> White moves first and that advantage is real.
            Nothing here controls for it yet.
          </li>
          <li>
            <strong>No absolute anchor.</strong> Ratings are relative to this pool only. A 1700 here
            is not a 1700 anywhere else, and cannot be compared to a human rating. An engine ladder
            would fix that and has not been built.
          </li>
          <li>
            <strong>Cost is not comparable.</strong> It depends on the provider and on our own
            caching implementation as much as on the model — one model cost twelve times its
            opponent for the same game because we were not sending cache markers it required.
          </li>
          <li>
            <strong>The default precision can move.</strong> A model asked for without a precision
            gets its healthiest declared endpoint, and which one that is can change between games.
            The precision played is always recorded, so results land in the right row — but an
            unqualified model name is a stable model, not a stable contestant.
          </li>
          <li>
            <strong>Prompt version.</strong> Ratings cover games played under prompt{" "}
            <Code>{board.prompt_version ?? "—"}</Code>. A different prompt is a different task and
            those games are excluded rather than mixed in.
          </li>
        </ul>
      </Section>

      <Section title="Everything is checkable">
        <p>
          Every request and response is stored verbatim with credentials redacted. Any number on a
          game page is one click from the raw provider payload that produced it, and any leaderboard
          row is one click from the games behind it. If a figure here looks wrong, the evidence for
          it is reachable.
        </p>
      </Section>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10 border-t border-line pt-6">
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-[0.16em] text-accent">
        {title}
      </h2>
      <div className="flex flex-col gap-3 text-sm leading-relaxed text-ink-dim">{children}</div>
    </section>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="border border-line-soft bg-surface-2 px-1 py-px font-mono text-[12px] text-ink">
      {children}
    </code>
  );
}
