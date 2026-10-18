import { Paragraph, SentenceText } from "@/components/Prose";
import { gameSummary, momentCaption, momentTitle } from "@/lib/prose";
import type { GameDetail } from "@/lib/types";

/**
 * A finished game, told in words under the board (ADR-0069).
 *
 * Server-rendered and in the first response, because the reader it is mostly for is a crawler:
 * the board and the event stream say all of this already to a person who opens them, but nothing
 * a search engine reads from them distinguishes one game from the next. The quotes are what no
 * other page has — what the models themselves wrote at the moments that mattered.
 *
 * A quote is a slice of the model's own text, never reworded (invariant 3); the turn it came from
 * is in the replay above, whole.
 */
export function GameStory({ game }: { game: GameDetail }) {
  const summary = gameSummary(game);
  const moments = game.digest?.moments ?? [];

  return (
    <section aria-labelledby="game-story" className="mx-auto mt-12 w-full max-w-[760px]">
      <h2
        id="game-story"
        className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint"
      >
        About this game
      </h2>
      <Paragraph sentences={summary} className="mt-3 leading-relaxed text-ink-dim" />

      {moments.length > 0 && (
        <>
          {/* "In their own words" only when there are words: a person's seat and a decision model
              write none, and their moments are still facts worth stating. */}
          <h2 className="mt-10 font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
            {moments.some((moment) => moment.excerpt) ? "In their own words" : "Moments"}
          </h2>
          <ol className="mt-3 flex flex-col gap-8">
            {moments.map((moment) => (
              <li key={`${moment.kind}-${moment.ply}`}>
                <h3 className="font-serif text-lg text-ink">{momentTitle(moment)}</h3>
                <p className="mt-1 text-sm leading-relaxed text-ink-dim">
                  <SentenceText sentence={momentCaption(game, moment)} />
                </p>
                {moment.excerpt && (
                  <figure className="mt-3 border-l-2 border-line pl-4">
                    <blockquote className="whitespace-pre-wrap text-sm leading-relaxed text-ink">
                      {moment.truncated ? "… " : ""}
                      {moment.excerpt}
                    </blockquote>
                    <figcaption className="mt-2 font-mono text-meta text-ink-faint">
                      {moment.source === "output" ? "its reply" : "its reasoning"}
                      {moment.truncated ? ", the end of it" : ""}
                      {" — the whole turn is in the replay above"}
                    </figcaption>
                  </figure>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
