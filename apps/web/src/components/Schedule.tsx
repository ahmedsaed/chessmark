"use client";

/**
 * Every pairing, grouped by round — what has been played, what is live, what is waiting.
 *
 * A client component for one reason: the list is paged, and the button that extends it has to
 * hold state. Everything it needs is already on the page — the API sends the whole schedule with
 * the tournament — so "load more" is a slice rather than a request, and there is nothing to wait
 * for and nothing to fail. The arithmetic lives in `lib/schedule`, which is where it is tested.
 */

import Link from "next/link";
import { useState } from "react";

import { SCHEDULE_PAGE, schedulePage } from "@/lib/schedule";
import type { TournamentPairing } from "@/lib/types";

export function Schedule({
  pairings,
  names,
}: {
  pairings: TournamentPairing[];
  /** Contestant key → display name, for the tooltips. A plain object, so it crosses the RSC
      boundary as data rather than relying on how a `Map` is serialised. */
  names: Record<string, string>;
}) {
  const [visible, setVisible] = useState(SCHEDULE_PAGE);
  const { rounds, shown, remaining } = schedulePage(pairings, visible);

  return (
    <section>
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Schedule
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        {/* Both numbers, because one alone lies by omission: "10" hides how long the event is and
            "312" describes a list the reader cannot see. */}
        <span className="tabular font-mono text-[10px] text-ink-faint">
          {remaining > 0 ? `${shown} of ${pairings.length}` : pairings.length}
        </span>
      </div>

      <div className="mb-3">
        <StateLegend />
      </div>

      {rounds.length === 0 && (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          Nothing scheduled yet.
        </p>
      )}

      <div className="flex flex-col gap-4">
        {rounds.map(({ round, pairings: games }) => (
          <div key={round}>
            <p className="mb-1 font-mono text-[9px] uppercase tracking-[0.14em] text-ink-faint">
              Round {round}
            </p>
            <ul className="flex flex-col gap-px border border-line-soft bg-line-soft">
              {games.map((pairing) => (
                <Pairing key={pairing.id} pairing={pairing} names={names} />
              ))}
            </ul>
          </div>
        ))}
      </div>

      {remaining > 0 && (
        <button
          type="button"
          onClick={() => setVisible((seen) => seen + SCHEDULE_PAGE)}
          className="mt-3 w-full border border-line-soft bg-surface px-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:border-accent hover:text-accent"
        >
          {/* The count is the point. "Load more" alone gives no sense of whether one more press
              finishes the list or forty do. */}
          Load {Math.min(SCHEDULE_PAGE, remaining)} more · {remaining} older
        </button>
      )}
    </section>
  );
}

function Pairing({
  pairing,
  names,
}: {
  pairing: TournamentPairing;
  names: Record<string, string>;
}) {
  const short = (key: string | null) =>
    key ? key.split("/").slice(1).join("/") || key : "bye";

  const score =
    pairing.white_score === null
      ? null
      : pairing.white_score === 1
        ? "1–0"
        : pairing.white_score === 0
          ? "0–1"
          : "½–½";

  const body = (
    <div className="flex items-center gap-2 bg-surface px-3 py-2 font-mono text-[11px]">
      <StateDot state={pairing.state} />
      <span className="min-w-0 flex-1 truncate text-ink-dim" title={names[pairing.white_key]}>
        {short(pairing.white_key)}
      </span>
      <span className="tabular flex-none text-[10px] text-ink">
        {score ?? (pairing.state === "abandoned" ? "—" : "vs")}
      </span>
      <span
        className="min-w-0 flex-1 truncate text-right text-ink-dim"
        title={pairing.black_key ? names[pairing.black_key] : "bye"}
      >
        {short(pairing.black_key)}
      </span>
    </div>
  );

  // A played or live pairing has a game to read; a queued one has nothing to link to yet.
  return (
    <li title={pairing.abandoned_reason ?? undefined}>
      {pairing.game_id ? (
        <Link
          href={`/games/${pairing.game_id}`}
          className="block transition-colors hover:bg-surface-2"
        >
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

/**
 * The colours a pairing's state is drawn in, and they are the site's own.
 *
 * They used to be `accent` for played, `machine` for live, `bad` for abandoned — state-mapped, but
 * **inverted relative to every other page**: red-with-a-pulse is what the header, the lobby card
 * and the hero all use for *live*, and cyan is the `machine` register that tool calls and
 * compaction notices live in. So the one place it meant "abandoned" read as live, and the one
 * place it meant "live" read as machinery. Not random, but no reader could have known.
 *
 * Now: `good` for a real result, `bad` and pulsing for a game actually moving, `ink-faint` for one
 * paused on a provider, and hollow for a pairing nothing has started.
 */
const PAIRING_TONE: Record<TournamentPairing["state"], string> = {
  played: "bg-good",
  live: "bg-bad animate-pulse",
  paused: "bg-ink-faint",
  abandoned: "border border-bad-deep bg-transparent",
  waiting: "border border-line bg-transparent",
};

const PAIRING_HINT: Record<TournamentPairing["state"], string> = {
  played: "a result",
  live: "playing now",
  paused: "waiting on a provider",
  abandoned: "the harness gave up",
  waiting: "not started",
};

function StateDot({ state }: { state: TournamentPairing["state"] }) {
  return (
    <i
      aria-label={`${state} — ${PAIRING_HINT[state]}`}
      title={`${state} — ${PAIRING_HINT[state]}`}
      className={`block h-1.5 w-1.5 flex-none ${PAIRING_TONE[state]}`}
    />
  );
}

/**
 * What the dots mean, said once.
 *
 * The colours are consistent now, which is necessary and not sufficient: a 1.5px square carries no
 * label. "It feels random" is the correct reaction to an unexplained colour, however principled it
 * is underneath.
 */
function StateLegend() {
  const order: TournamentPairing["state"][] = [
    "live",
    "paused",
    "played",
    "waiting",
    "abandoned",
  ];
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {order.map((state) => (
        <li key={state} className="flex items-center gap-1.5">
          <i aria-hidden className={`block h-1.5 w-1.5 flex-none ${PAIRING_TONE[state]}`} />
          <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
            {state}
          </span>
        </li>
      ))}
    </ul>
  );
}
