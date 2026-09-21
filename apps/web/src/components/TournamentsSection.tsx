/**
 * What a tournament is, and the ones that are running.
 *
 * **The concept is the first cell of the grid, not a paragraph above it.** There is one tournament
 * today, and a full-width row built for three would have rendered one card beside two holes — a
 * section that looks like it is waiting for something. As a cell, the explainer balances a single
 * event and steps aside as more are created.
 *
 * It says what `/tournaments` cannot: that page lists events to somebody who already knows what one
 * is. A visitor arriving at the lobby does not, and "a field, a format and a set of bounds" is the
 * whole idea (TOURNAMENTS.md). The pool gets the second paragraph because it is the part that
 * surprises people — an event that never ends and re-checks its own field.
 *
 * A server component fed the list the lobby already awaited. It fetches nothing of its own, and
 * deliberately does not reach for standings: the podium directly above already answers "who is
 * winning", and repeating it here would cost a request per tournament to say it twice.
 */

import Link from "next/link";

import { StatusChip, formatLabel } from "@/app/tournaments/page";
import type { TournamentSummary } from "@/lib/types";

/** Three, which is a row beside the explainer at `lg` and the whole section on a phone. */
const SHOWN = 3;

/** Cells across at `lg`: one per tournament, plus the explainer. Indexed by how many are shown. */
const COLUMNS = ["lg:grid-cols-2", "lg:grid-cols-2", "lg:grid-cols-3", "lg:grid-cols-4"] as const;

/**
 * Running first, then newest.
 *
 * A finished event is a record and a running one is a thing to watch; the lobby is for the second.
 * `started_at` rather than `created_at` because a tournament is created empty and may sit for days
 * before anyone runs it.
 */
function order(tournaments: TournamentSummary[]): TournamentSummary[] {
  return [...tournaments].sort((a, b) => {
    const running = Number(b.status === "running") - Number(a.status === "running");
    if (running !== 0) return running;
    return (b.started_at ?? b.created_at).localeCompare(a.started_at ?? a.created_at);
  });
}

export function TournamentsSection({ tournaments }: { tournaments: TournamentSummary[] }) {
  const shown = order(tournaments).slice(0, SHOWN);

  return (
    <section className="mt-16">
      <div className="mb-5 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Tournaments
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <Link
          href="/tournaments"
          prefetch={false}
          className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
        >
          All {tournaments.length} →
        </Link>
      </div>

      {/* **The column count follows the number of cells**, explainer included. A fixed four-wide
          grid put the two cells this deployment has into the left half and left the right half
          blank — the "waiting for something" look the explainer-as-a-cell was meant to avoid.
          Written as whole class names because Tailwind reads the source, not the value.
          `auto-rows-fr` keeps a short card as tall as the explainer rather than leaving a step. */}
      <div
        className={`grid grid-cols-1 gap-3 sm:auto-rows-fr sm:grid-cols-2 ${COLUMNS[shown.length]}`}
      >
        <Explainer />

        {shown.length === 0 ? (
          <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim sm:col-span-1">
            None running. One is created from the command line and ticked along by a worker; when
            the first is under way it appears here.
          </p>
        ) : (
          shown.map((tournament) => <Card key={tournament.id} tournament={tournament} />)
        )}
      </div>
    </section>
  );
}

function Explainer() {
  return (
    <div className="flex flex-col gap-2.5 border border-line bg-surface p-4">
      <h3 className="font-mono text-label uppercase tracking-[0.16em] text-accent">
        What a tournament is
      </h3>
      <p className="text-sm leading-relaxed text-ink-dim">
        A field, a format, and a set of bounds. Every bracket — the free models, open weights
        against closed, one vendor&rsquo;s catalogue — is the same machinery with a different
        filter.
      </p>
      <p className="text-sm leading-relaxed text-ink-dim">
        A <strong className="font-normal text-ink">pool</strong> never ends. It re-checks its field
        every tick, so a model listed this morning enters by itself, and it pairs whoever has played
        least — which is what makes an open population rankable at all.
      </p>
    </div>
  );
}

/** `180552700` → `181M`. A token total is a magnitude, and nine digits read as noise. */
function compact(tokens: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 0 }).format(
    tokens,
  );
}

/** Money is a string from the API — eight decimal places that a JSON float would round away. */
function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return amount === 0 ? "$0.00" : amount < 0.01 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`;
}

function Card({ tournament }: { tournament: TournamentSummary }) {
  const { stats } = tournament;

  return (
    <Link
      href={`/tournaments/${tournament.slug}`}
      /* The same trap as the model links: a visible link to a dynamic route prefetches a payload
         the router cannot store and reschedules itself for as long as it is on screen
         (FRONTEND.md). Every route in this app is dynamic, so this is not a special case. */
      prefetch={false}
      className="flex flex-col gap-2.5 border border-line bg-surface-2 p-4 transition-colors hover:border-accent focus-visible:border-accent"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate font-serif text-lg text-ink">{tournament.name}</span>
        <StatusChip status={tournament.status} />
      </div>

      <p className="font-mono text-meta text-ink-faint">
        {tournament.field_description} · {tournament.entrant_count} entrants ·{" "}
        {formatLabel(tournament)}
      </p>

      <Progress stats={stats} />

      <div className="tabular mt-auto flex flex-col gap-1 font-mono text-meta text-ink-faint">
        <p>
          {stats.decisive} decisive · {stats.draws} draw{stats.draws === 1 ? "" : "s"}
          {/* Null until something has finished: a tournament with no settled game has no mean. */}
          {stats.mean_plies ? ` · ${Math.round(stats.mean_plies)} plies average` : ""}
        </p>
        {/* What it took. The illegal attempts are the benchmark's headline number in aggregate, and
            the cost is the point of a free field: 180 million tokens for nothing. */}
        <p>
          {compact(stats.total_tokens)} tokens · {stats.illegal_attempts} illegal ·{" "}
          {usd(stats.total_cost_usd)}
        </p>
      </div>
    </Link>
  );
}

/**
 * A pairing list, drawn.
 *
 * The counts below say the same thing and the bar is what makes a third of this pool being stuck
 * visible rather than merely stated — `pool-free` is 20 played, 5 paused and 5 abandoned, which
 * reads as "two thirds done" in numbers and as "one sixth of it is red" here.
 *
 * `aria-hidden`, because the line underneath is the accessible version of the same fact and a
 * screen reader gets nothing from four unlabelled boxes.
 */
function Progress({ stats }: { stats: TournamentSummary["stats"] }) {
  const total = Math.max(stats.pairings, 1);
  const segments = [
    { key: "played", value: stats.played, tone: "bg-accent" },
    { key: "live", value: stats.live, tone: "bg-machine" },
    { key: "paused", value: stats.paused, tone: "bg-ink-faint" },
    { key: "abandoned", value: stats.abandoned, tone: "bg-bad" },
  ].filter((segment) => segment.value > 0);

  return (
    <div className="flex flex-col gap-1.5">
      {/* `bg-line` is the track: whatever the segments do not cover is a pairing still to play. */}
      <div aria-hidden className="flex h-1.5 w-full overflow-hidden bg-line">
        {segments.map((segment) => (
          <span
            key={segment.key}
            className={segment.tone}
            style={{ width: `${(segment.value / total) * 100}%` }}
          />
        ))}
      </div>
      <p className="tabular font-mono text-meta text-ink-dim">
        {stats.played} of {stats.pairings} pairing{stats.pairings === 1 ? "" : "s"}
        {stats.live > 0 ? ` · ${stats.live} live` : ""}
        {stats.paused > 0 ? ` · ${stats.paused} paused` : ""}
        {stats.abandoned > 0 ? ` · ${stats.abandoned} abandoned` : ""}
      </p>
    </div>
  );
}
