import Link from "next/link";
import { notFound } from "next/navigation";

import { StatusChip, formatLabel } from "@/app/tournaments/page";
import { getTournament } from "@/lib/api";
import type { Standing, TournamentDetail, TournamentPairing } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: PageProps<"/tournaments/[slug]">) {
  const { slug } = await params;
  const tournament = await getTournament(slug);
  if (!tournament) return { title: "Tournament not found" };

  return {
    title: tournament.name,
    description: `${tournament.field_description}: ${tournament.entrant_count} entrants, ${tournament.stats.played} of ${tournament.stats.pairings} games played.`,
  };
}

export default async function TournamentPage({ params }: PageProps<"/tournaments/[slug]">) {
  const { slug } = await params;
  const tournament = await getTournament(slug);
  if (!tournament) notFound();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <Link
        href="/tournaments"
        className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
      >
        ← All tournaments
      </Link>

      <div className="mt-4 flex flex-wrap items-baseline gap-3">
        <h1 className="font-serif text-4xl leading-tight text-ink">{tournament.name}</h1>
        <StatusChip status={tournament.status} />
      </div>
      <p className="mt-1 font-mono text-xs text-ink-faint">
        {tournament.field_description} · {tournament.entrant_count} entrants ·{" "}
        {formatLabel(tournament)}
        {tournament.is_ranked ? " · ranked" : " · unranked"}
      </p>

      <Progress tournament={tournament} />
      {tournament.format === "swiss" && tournament.status !== "finished" && (
        <p className="mt-3 max-w-prose font-mono text-[10.5px] leading-relaxed text-ink-faint">
          Swiss pairs on the standings, so only the current round exists — round{" "}
          {currentRound(tournament)} of {tournament.rounds}. The next is written once this one
          finishes, which is also why a crash cannot desynchronise it.
        </p>
      )}
      <Metrics tournament={tournament} />

      <div className="mt-12 grid grid-cols-1 gap-10 lg:grid-cols-[1.2fr_1fr]">
        <Standings rows={tournament.standings} />
        <Schedule pairings={tournament.pairings} names={nameMap(tournament)} />
      </div>
    </main>
  );
}

/** The highest round written down so far — for Swiss, the one being played. */
function currentRound(tournament: TournamentDetail): number {
  return tournament.pairings.reduce((highest, p) => Math.max(highest, p.round_number), 0);
}

function nameMap(tournament: TournamentDetail): Map<string, string> {
  return new Map(tournament.standings.map((row) => [row.key, row.display_name]));
}

/**
 * How far along the event is, as one bar.
 *
 * Abandoned games are shown rather than hidden: a bracket that quietly drops a fifth of its games
 * is indistinguishable from one that is wrong, and free-tier providers abandon games regularly.
 */
function Progress({ tournament }: { tournament: TournamentDetail }) {
  const { played, live, waiting, abandoned, pairings } = tournament.stats;
  if (!pairings) {
    return (
      <p className="mt-8 border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
        Nothing scheduled yet. The first round is written down the next time the runner ticks.
      </p>
    );
  }

  const segments = [
    { label: "played", count: played, className: "bg-accent" },
    { label: "live", count: live, className: "bg-machine" },
    { label: "abandoned", count: abandoned, className: "bg-bad" },
    { label: "waiting", count: waiting, className: "bg-line" },
  ].filter((segment) => segment.count > 0);

  return (
    <section className="mt-8">
      <div className="flex h-2 w-full overflow-hidden border border-line-soft">
        {segments.map((segment) => (
          <span
            key={segment.label}
            className={segment.className}
            style={{ width: `${(segment.count / pairings) * 100}%` }}
            aria-hidden
          />
        ))}
      </div>
      <p className="tabular mt-2 flex flex-wrap gap-x-4 font-mono text-[10px] text-ink-faint">
        {segments.map((segment) => (
          <span key={segment.label}>
            {segment.count} {segment.label}
          </span>
        ))}
        {/* The total, set apart from the states so it does not read as one of them. */}
        <span className="ml-auto text-ink-dim">
          {pairings} pairing{pairings === 1 ? "" : "s"}
        </span>
      </p>
    </section>
  );
}

/** What the event has cost and produced. Every figure traces to the call log (invariant 4). */
function Metrics({ tournament }: { tournament: TournamentDetail }) {
  const s = tournament.stats;
  const usd = (value: string) => {
    const n = Number(value);
    return Number.isFinite(n) ? `$${n.toFixed(n < 1 ? 4 : 2)}` : "—";
  };

  return (
    <dl className="mt-8 grid grid-cols-2 gap-px border border-line-soft bg-line-soft sm:grid-cols-3 lg:grid-cols-6">
      <Fact
        label="Cost"
        value={usd(s.total_cost_usd)}
        note={tournament.max_usd ? `of ${usd(tournament.max_usd)}` : "uncapped"}
      />
      <Fact label="Tokens" value={s.total_tokens.toLocaleString()} />
      <Fact
        label="Plies"
        value={s.total_plies.toLocaleString()}
        note={s.mean_plies ? `${Math.round(s.mean_plies)} a game` : undefined}
      />
      <Fact
        label="Decisive"
        value={s.played ? `${Math.round((s.decisive / Math.max(s.played, 1)) * 100)}%` : "—"}
        note={`${s.decisive} won, ${s.draws} drawn`}
      />
      {/* The benchmark's headline number, and the reason the project exists. */}
      <Fact
        label="Illegal"
        value={String(s.illegal_attempts)}
        tone={s.illegal_attempts > 0 ? "bad" : "good"}
        note="attempts, all seats"
      />
      <Fact
        label="Concurrency"
        value={String(tournament.max_concurrent)}
        note={`${s.live} in flight`}
      />
    </dl>
  );
}

function Standings({ rows }: { rows: Standing[] }) {
  return (
    <section>
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Standings
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
      </div>

      <ul className="flex flex-col gap-px border border-line-soft bg-line-soft">
        <li className="tabular grid grid-cols-[2rem_1fr_3rem_4.5rem_3.5rem] items-center gap-2 bg-surface-2 px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-ink-faint">
          <span>#</span>
          <span>Model</span>
          <span className="text-right">Pts</span>
          <span className="text-right">W/D/L</span>
          <span className="text-right" title="Sonneborn-Berger: beating strong opponents counts more">
            SB
          </span>
        </li>
        {rows.map((row) => (
          <li
            key={row.key}
            className="tabular grid grid-cols-[2rem_1fr_3rem_4.5rem_3.5rem] items-center gap-2 bg-surface px-3 py-2 font-mono text-xs"
          >
            <span className={row.place === 1 ? "text-accent" : "text-ink-faint"}>{row.place}</span>
            <Link
              href={`/models/${row.key.split("@")[0]}`}
              className="min-w-0 truncate text-ink transition-colors hover:text-accent"
            >
              {row.key.split("/").slice(1).join("/") || row.key}
            </Link>
            <span className="text-right text-ink">{row.score.toFixed(1)}</span>
            <span className="text-right text-[10px] text-ink-faint">
              {row.wins}/{row.draws}/{row.losses}
            </span>
            <span className="text-right text-[10px] text-ink-faint">
              {row.sonneborn_berger.toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
      {rows.length === 0 && (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          No entrants.
        </p>
      )}
    </section>
  );
}

/** Every pairing, grouped by round — what has been played, what is live, what is waiting. */
function Schedule({
  pairings,
  names,
}: {
  pairings: TournamentPairing[];
  names: Map<string, string>;
}) {
  const rounds = new Map<number, TournamentPairing[]>();
  for (const pairing of pairings) {
    const bucket = rounds.get(pairing.round_number) ?? [];
    bucket.push(pairing);
    rounds.set(pairing.round_number, bucket);
  }

  return (
    <section>
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Schedule
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-[10px] text-ink-faint">{pairings.length}</span>
      </div>

      {rounds.size === 0 && (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          Nothing scheduled yet.
        </p>
      )}

      <div className="flex flex-col gap-4">
        {[...rounds.entries()].map(([round, games]) => (
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
    </section>
  );
}

function Pairing({
  pairing,
  names,
}: {
  pairing: TournamentPairing;
  names: Map<string, string>;
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
      <span className="min-w-0 flex-1 truncate text-ink-dim" title={names.get(pairing.white_key)}>
        {short(pairing.white_key)}
      </span>
      <span className="tabular flex-none text-[10px] text-ink">
        {score ?? (pairing.state === "abandoned" ? "—" : "vs")}
      </span>
      <span
        className="min-w-0 flex-1 truncate text-right text-ink-dim"
        title={pairing.black_key ? names.get(pairing.black_key) : "bye"}
      >
        {short(pairing.black_key)}
      </span>
    </div>
  );

  // A played or live pairing has a game to read; a queued one has nothing to link to yet.
  return (
    <li title={pairing.abandoned_reason ?? undefined}>
      {pairing.game_id ? (
        <Link href={`/games/${pairing.game_id}`} className="block transition-colors hover:bg-surface-2">
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

function StateDot({ state }: { state: TournamentPairing["state"] }) {
  const tone = {
    played: "bg-accent",
    live: "bg-machine",
    abandoned: "bg-bad",
    waiting: "bg-line",
  }[state];

  return <i aria-label={state} title={state} className={`block h-1.5 w-1.5 flex-none ${tone}`} />;
}

function Fact({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "good" | "bad";
}) {
  const colour = tone === "bad" ? "text-bad" : tone === "good" ? "text-good" : "text-ink";
  return (
    <div className="bg-surface px-3 py-2.5">
      <dt className="font-mono text-[9px] uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd className={`tabular mt-1 font-mono text-sm ${colour}`}>{value}</dd>
      {note && <p className="tabular mt-0.5 font-mono text-[9.5px] text-ink-faint">{note}</p>}
    </div>
  );
}
