import type { Metadata } from "next";
import Link from "next/link";

import { getLeaderboard, listGames } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "About",
  description:
    "What Chessmark measures, why chess, and what the numbers can and cannot tell you.",
};

/**
 * The about page.
 *
 * Deliberately states the limits alongside the pitch. A benchmark that only advertises its
 * strengths is asking to be trusted rather than checked, and everything here is checkable —
 * which is the actual claim.
 */
export default async function AboutPage() {
  const [board, games] = await Promise.all([getLeaderboard(), listGames(undefined, 200)]);
  const finished = games.filter((game) => game.status === "finished").length;

  return (
    <main className="mx-auto w-full max-w-[760px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">About Chessmark</h1>
      <p className="mt-4 text-lg leading-relaxed text-ink-dim">
        Two language models sit down at a chessboard neither of them can see. They play through
        tools, one move at a time, for as long as they can hold the position in their heads.
      </p>

      <Section title="What this measures">
        <p>
          Most benchmarks ask a model one question and score the answer. Chess asks it eighty
          questions in a row, where every answer depends on remembering all the previous ones, an
          adversary is actively working against it, and there is a referee who cannot be argued
          with.
        </p>
        <p>
          That makes it a test of <strong className="text-ink">agentic reliability</strong> rather
          than chess ability: long-horizon state tracking, correct tool use under pressure, and
          recovery from being told you are wrong. A model that plays a beautiful opening and then
          tries to move a knight that was captured twenty plies ago has failed at the thing being
          measured.
        </p>
        <p>
          The headline number is not the rating. It is the{" "}
          <strong className="text-ink">illegal move rate</strong> — how often a model proposed a
          move that could not be played from the position in front of it.
        </p>
      </Section>

      <Section title="How a game works">
        <p>
          A model never touches the board. It calls tools — read the position, list the legal
          moves, make a move — and the server validates every one of them with{" "}
          <code className="font-mono text-accent">python-chess</code>. The server is the only
          authority on board state, so a model cannot corrupt a game record even if it tries.
        </p>
        <p>
          An illegal move is not an instant loss. The model is told exactly why, handed the full
          list of legal moves, and asked again — five times before it forfeits. The whole
          conversation is one append-only transcript that never rewrites earlier messages, which
          is both what makes prompt caching work and what makes the replay honest.
        </p>
      </Section>

      <Section title="What is recorded">
        <p>
          Everything, verbatim. Raw request and response payloads with credentials redacted, every
          reasoning trace, every tool call and its result, token counts, and the exact cost
          computed from the tokens the provider actually returned — never estimated.
        </p>
        <p>
          If a number appears on the leaderboard, the transcript behind it is one click away.
          That is the whole point:{" "}
          <Link href="/leaderboard" className="text-accent underline-offset-4 hover:underline">
            a rating
          </Link>{" "}
          reaches its games, a game reaches its plies, and a ply reaches the JSON the provider
          sent back.
        </p>
      </Section>

      <Section title="What it cannot tell you">
        <ul className="flex list-disc flex-col gap-2 pl-5 marker:text-ink-faint">
          <li>
            <strong className="text-ink">The sample is small.</strong> {board.games_counted} ranked
            game{board.games_counted === 1 ? "" : "s"} of {finished} finished. Every rating carries
            its deviation for exactly this reason — read the ± before the number.
          </li>
          <li>
            <strong className="text-ink">A contestant is a model at a precision.</strong> The same
            weights served at 4-bit and 8-bit are different entrants and are ranked apart, because
            they do not play the same.
          </li>
          <li>
            <strong className="text-ink">The endpoint matters too.</strong> One provider returned
            malformed tool calls on roughly one call in six while others serving identical weights
            returned none. Each seat pins one endpoint for a whole game, and the provider is
            recorded per call.
          </li>
          <li>
            <strong className="text-ink">This is not a chess engine ranking.</strong> Every
            contestant here would lose to free software from 2005. The interesting question is not
            how well they play, but how long they can stay coherent.
          </li>
        </ul>
      </Section>

      <p className="mt-12 border-t border-line pt-6 text-sm text-ink-dim">
        The full method, including how ratings are computed and which games are excluded, is on the{" "}
        <Link href="/methodology" className="text-accent underline-offset-4 hover:underline">
          methodology page
        </Link>
        .
      </p>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-12">
      <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">{title}</h2>
      <div className="mt-4 flex flex-col gap-4 leading-relaxed text-ink-dim">{children}</div>
    </section>
  );
}
