/**
 * Play one yourself: the scoreboard, and what you are up against.
 *
 * **The joke is the numbers, which is the only kind this site can tell.** Everything here is
 * measured — the human record comes from `/games/human-record`, the rest is summed from the
 * ranking the lobby already holds — so the section is funny for as long as the models keep being
 * bad at chess and stops being funny the moment they are not. That is the correct behaviour for a
 * benchmark, and it is why none of these lines is written into the markup.
 *
 * **It says nothing about credits.** A seat is granted while the site is in testing (ADR-0016),
 * which `/play` explains to a signed-in reader who can act on it; the lobby is the same page for
 * everybody and cannot tell who is asking, so it invites and leaves the door to answer.
 */

import Link from "next/link";

import type { HumanRecord, LeaderboardRow } from "@/lib/types";

export function ChallengeSection({
  record,
  rows,
  gamesCounted,
}: {
  record: HumanRecord;
  rows: LeaderboardRow[];
  gamesCounted: number;
}) {
  return (
    <section className="mt-16">
      <div className="mb-5 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Play one yourself
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <Link
          href="/play"
          prefetch={false}
          className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
        >
          Play →
        </Link>
      </div>

      {/* `auto-rows-fr` only once they are side by side: stacked, it stretched every cell to the
          tallest and left the shorter one with a hole in the middle. */}
      <div className="grid grid-cols-1 gap-3 lg:auto-rows-fr lg:grid-cols-2">
        <Scoreboard record={record} />
        <Opponent rows={rows} gamesCounted={gamesCounted} />
      </div>
    </section>
  );
}

/**
 * The whole human-versus-model record, which is currently one game.
 *
 * Printing a one-game record as a headline is a joke on this project specifically: the leaderboard
 * beside it marks a three-game rating provisional and prints its deviation everywhere, so the
 * caption saying the same thing about the scoreboard is the house style applied to itself.
 */
function Scoreboard({ record }: { record: HumanRecord }) {
  return (
    <div className="flex flex-col gap-4 border border-accent-dim bg-surface-2 p-5">
      {/* A scoreboard, so the two sides are level and the dash sits on the numerals' baseline
          rather than floating between the labels above them. */}
      <div className="flex items-end gap-5">
        <Side label="Humans" value={record.wins} tone="text-accent" />
        <span className="font-serif text-3xl leading-none text-ink-faint" aria-hidden>
          —
        </span>
        <Side label="Models" value={record.losses} tone="text-ink" />
      </div>

      <p className="font-mono text-meta text-ink-faint">
        {caption(record)}
        {record.draws > 0 ? ` · ${record.draws} drawn` : ""}
      </p>

      <p className="text-sm leading-relaxed text-ink-dim">
        Sit down against any model in the catalogue. It gets the same prompt, the same tools and the
        same five chances to find a legal move as it does in a ranked game. You get a board, and as
        long as you like to think about it.
      </p>

      <Link
        href="/play"
        prefetch={false}
        className="mt-auto self-start border border-accent-deep bg-accent px-4 py-2 font-mono text-data uppercase tracking-[0.14em] text-on-accent transition-colors hover:bg-accent-dim"
      >
        Take a seat →
      </Link>
    </div>
  );
}

/**
 * The caption under the score, which has to be true at every value it can hold.
 *
 * A line that is funny at one game and wrong at two hundred is a line somebody has to remember to
 * come back for, and nobody does.
 */
function caption(record: HumanRecord): string {
  if (record.games === 0) return "nobody has sat down yet";
  if (record.games === 1) return "one game. provisional, obviously";
  if (record.games < 10) return `${record.games} games. provisional, obviously`;
  return `${record.games} games, and counting`;
}

function Side({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <p className="flex flex-col gap-1">
      <span className="font-mono text-label uppercase tracking-[0.16em] text-ink-faint">
        {label}
      </span>
      {/* `tabular` matters at three digits: without it the two sides drift apart as the score
          grows, and a scoreboard that does not line up is not a scoreboard. */}
      <span className={`tabular font-serif text-5xl leading-none ${tone}`}>{value}</span>
    </p>
  );
}

/**
 * What the opponent has been caught doing, in ranked games.
 *
 * Summed from the ranking the lobby already awaited rather than fetched: these are the same rows
 * the podium is drawn from, so the section costs one read for the score and nothing for this.
 * Scoped to *ranked* games and labelled as such — the archive holds unranked ones too, and a
 * number that quietly mixes the two is the kind this project is supposed to be better than.
 */
function Opponent({ rows, gamesCounted }: { rows: LeaderboardRow[]; gamesCounted: number }) {
  const illegal = rows.reduce((total, row) => total + row.illegal_attempts, 0);
  const moves = rows.reduce((total, row) => total + row.moves_played, 0);
  const forfeits = rows.reduce((total, row) => total + row.forfeits, 0);
  const clean = rows.filter((row) => row.illegal_attempts === 0).length;
  const worst = rows.reduce<LeaderboardRow | null>(
    (found, row) => (found === null || row.illegal_per_move > found.illegal_per_move ? row : found),
    null,
  );

  return (
    <div className="flex flex-col gap-3 border border-line bg-surface p-5">
      <h3 className="font-mono text-label uppercase tracking-[0.16em] text-accent">
        Know your opponent
      </h3>

      <dl className="flex flex-col gap-2">
        <Fact value={illegal.toLocaleString("en")}>
          illegal moves attempted in {gamesCounted} ranked game{gamesCounted === 1 ? "" : "s"} —
          none of them by a person, because the board will not take one
        </Fact>
        <Fact value={forfeits.toLocaleString("en")}>
          forfeit{forfeits === 1 ? "" : "s"}: five tries, no legal move found, game over
        </Fact>
        {worst && worst.illegal_per_move > 0 && (
          <Fact value={worst.illegal_per_move.toFixed(2)}>
            illegal attempts <em className="not-italic text-ink-dim">per move</em> from the worst of
            them — {worst.illegal_attempts} of them across {worst.moves_played} moves
          </Fact>
        )}
        {rows.length > 0 && (
          <Fact value={`${clean}/${rows.length}`}>
            have never tried one. The rest are as described above
          </Fact>
        )}
      </dl>

      <p className="mt-auto font-mono text-meta text-ink-faint">
        {moves.toLocaleString("en")} moves played · every one of them logged
      </p>
    </div>
  );
}

function Fact({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2.5">
      <dt className="tabular w-16 flex-none text-right font-mono text-data text-accent">{value}</dt>
      <dd className="min-w-0 flex-1 text-sm leading-snug text-ink-dim">{children}</dd>
    </div>
  );
}
