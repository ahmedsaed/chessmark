import Link from "next/link";
import { notFound } from "next/navigation";

import { StatusChip, formatLabel } from "@/app/tournaments/page";
import { Schedule } from "@/components/Schedule";
import { getTournament } from "@/lib/api";
import type { Standing, TournamentDetail } from "@/lib/types";

export async function generateMetadata({ params }: PageProps<"/tournaments/[slug]">) {
  const { slug } = await params;
  const tournament = await getTournament(slug);
  if (!tournament) return { title: "Tournament not found" };

  const title = tournament.name;
  const description = `${tournament.field_description}: ${tournament.entrant_count} entrants, ${tournament.stats.played} of ${tournament.stats.pairings} games played.`;

  /* Canonical without the era query string: the eras are views of one event (ADR-0043), not
     separate pages, and each would otherwise compete with the others in an index. */
  return {
    title,
    description,
    alternates: { canonical: `/tournaments/${tournament.slug}` },
    openGraph: { title: `${title} — Chessmark`, description, type: "article" },
    twitter: { title: `${title} — Chessmark`, description },
  };
}

/**
 * Which era this table is, and a way back to the earlier ones (ADR-0043).
 *
 * A pool carries its task changes internally rather than being replaced, so the heading has to say
 * which task these numbers came from — a crosstable that does not name its task is one a reader
 * cannot check. It sits beside the status chip because it is the same kind of fact about the event.
 *
 * **A `<details>` rather than a client component.** The page is a server component and this is a
 * menu of two or three links; a disclosure gets the click-to-open, the keyboard and the focus ring
 * from the browser, ships no JavaScript, and closes by itself because every item navigates. What
 * it does not get is close-on-outside-click, which is the price and a small one at this size.
 *
 * Silent for an event that has only ever played one era — every closed tournament and every new
 * pool. There is nothing to choose between, and a menu offering one option is furniture.
 */
function EraMenu({ tournament, slug }: { tournament: TournamentDetail; slug: string }) {
  if (tournament.eras.length < 2) {
    return tournament.era ? (
      <span className="border border-line px-1.5 py-px font-mono text-label uppercase tracking-[0.14em] text-ink-faint">
        {tournament.era}
      </span>
    ) : null;
  }

  return (
    <details className="group relative">
      <summary className="flex cursor-pointer list-none items-center gap-1 border border-line px-1.5 py-px font-mono text-label uppercase tracking-[0.14em] text-ink-faint transition-colors hover:border-accent hover:text-accent [&::-webkit-details-marker]:hidden">
        {tournament.era ?? "all eras"}
        <span aria-hidden className="transition-transform group-open:rotate-180">
          ▾
        </span>
      </summary>
      <ul className="absolute left-0 top-full z-10 mt-1 min-w-full border border-line bg-surface-2 py-1 shadow-sm">
        {tournament.eras.map((name) => (
          <li key={name}>
            <Link
              href={`/tournaments/${slug}?era=${encodeURIComponent(name)}`}
              aria-current={name === tournament.era ? "page" : undefined}
              className={`block whitespace-nowrap px-2.5 py-1 font-mono text-meta ${
                name === tournament.era
                  ? "text-accent"
                  : "text-ink-faint transition-colors hover:text-accent"
              }`}
            >
              {name}
            </Link>
          </li>
        ))}
      </ul>
    </details>
  );
}

export default async function TournamentPage({
  params,
  searchParams,
}: PageProps<"/tournaments/[slug]">) {
  const { slug } = await params;
  const { era } = await searchParams;
  const tournament = await getTournament(slug, typeof era === "string" ? era : undefined);
  if (!tournament) notFound();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <Link
        href="/tournaments"
        className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
      >
        ← All tournaments
      </Link>

      <div className="mt-4 flex flex-wrap items-baseline gap-3">
        <h1 className="font-serif text-4xl leading-tight text-ink">{tournament.name}</h1>
        <StatusChip status={tournament.status} />
        <EraMenu tournament={tournament} slug={slug} />
      </div>
      <p className="mt-1 font-mono text-xs text-ink-faint">
        {tournament.field_description} · {tournament.entrant_count} entrants ·{" "}
        {formatLabel(tournament)}
        {tournament.is_ranked ? " · ranked" : " · unranked"}
      </p>

      <Progress tournament={tournament} />
      {tournament.format === "swiss" && tournament.status !== "finished" && (
        <p className="mt-3 font-mono text-meta leading-relaxed text-ink-faint">
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

function nameMap(tournament: TournamentDetail): Record<string, string> {
  return Object.fromEntries(tournament.standings.map((row) => [row.key, row.display_name]));
}

/**
 * How far along the event is, as one bar.
 *
 * Abandoned games are shown rather than hidden: a bracket that quietly drops a fifth of its games
 * is indistinguishable from one that is wrong, and free-tier providers abandon games regularly.
 */
function Progress({ tournament }: { tournament: TournamentDetail }) {
  const { played, live, paused, waiting, abandoned, pairings } = tournament.stats;
  if (!pairings) {
    return (
      <p className="mt-8 border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
        Nothing scheduled yet. The first round is written down the next time the runner ticks.
      </p>
    );
  }

  /* The same colours the schedule's dots use, so a reader who learns one has learned both. */
  const segments = [
    { label: "played", count: played, className: "bg-good" },
    { label: "live", count: live, className: "bg-bad" },
    { label: "paused", count: paused, className: "bg-ink-faint" },
    { label: "abandoned", count: abandoned, className: "bg-bad-deep" },
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
      <p className="tabular mt-2 flex flex-wrap gap-x-4 font-mono text-meta text-ink-faint">
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
        /* `live` and not `live + paused`: a paused game holds no slot (ADR-0017), and saying "4 in
           flight" against a bound of 1 was the arithmetic that made the page look broken. */
        note={s.paused ? `${s.live} running · ${s.paused} paused` : `${s.live} running`}
      />
    </dl>
  );
}

function Standings({ rows }: { rows: Standing[] }) {
  /* A pool is ordered by a rating computed over its own games, a closed event by points
     (ADR-0027). The API decides which and says so by sending a rating at all, so the table reads
     the data rather than re-deriving the format — one place makes the choice. */
  const rated = rows.some((row) => row.rating !== null);

  return (
    <section>
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Standings
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
      </div>

      <ul className="flex flex-col gap-px border border-line-soft bg-line-soft">
        <li
          /* **The trailing columns go below `sm`, and the name gets their width.** Five columns of
             fixed widths left the `1fr` model column with **37px** on a phone —
             `nemotron-3-nano-omni-30b-a3b-reasoning:free` needs 310px and showed four characters.
             A pool is ranked by rating (ADR-0027), so rating is the column that has to survive;
             points, W/D/L and SB are the ones a reader opens the page on a laptop for. */
          className={`tabular grid ${
            rated
              ? "grid-cols-[2rem_1fr_5.5rem] sm:grid-cols-[2rem_1fr_5.5rem_3rem_4.5rem]"
              : "grid-cols-[2rem_1fr_3rem] sm:grid-cols-[2rem_1fr_3rem_4.5rem_3.5rem]"
          } items-center gap-2 bg-surface-2 px-3 py-1.5 font-mono text-label uppercase tracking-[0.12em] text-ink-faint`}
        >
          <span>#</span>
          <span>Model</span>
          {rated ? (
            <span
              className="text-right"
              title="Glicko-2 over this event's games only, so a place here cannot move because of a game played elsewhere"
            >
              Rating
            </span>
          ) : (
            <span className="text-right">Pts</span>
          )}
          {rated ? (
            <span className="hidden text-right sm:block">Pts</span>
          ) : (
            <span className="hidden text-right sm:block">W/D/L</span>
          )}
          {rated ? (
            <span className="hidden text-right sm:block">W/D/L</span>
          ) : (
            <span
              className="hidden text-right sm:block"
              title="Sonneborn-Berger: beating strong opponents counts more"
            >
              SB
            </span>
          )}
        </li>
        {rows.map((row) => (
          <li
            key={row.key}
            /* **A row that will never gain another game does not look like one still competing.**
               A pool keeps a departed model's record — the games are real and the rating is real —
               and stops pairing it, so the table has to show which kind of row this is. Dimmed
               rather than hidden or struck through: the result stands, it is simply finished. */
            title={
              row.in_field
                ? undefined
                : "No longer in this field — its games and rating stand, but it will not be paired again"
            }
            className={`tabular grid ${
              rated
                ? "grid-cols-[2rem_1fr_5.5rem] sm:grid-cols-[2rem_1fr_5.5rem_3rem_4.5rem]"
                : "grid-cols-[2rem_1fr_3rem] sm:grid-cols-[2rem_1fr_3rem_4.5rem_3.5rem]"
            } items-center gap-2 bg-surface px-3 py-2 font-mono text-xs ${
              row.in_field ? "" : "opacity-50"
            }`}
          >
            <span className={row.place === 1 ? "text-accent" : "text-ink-faint"}>{row.place}</span>
            <Link
              /* The prefetch storm `/leaderboard` had: an uncacheable payload the router keeps
                 re-requesting. FRONTEND.md. */
              prefetch={false}
              href={`/models/${row.key.split("@")[0]}`}
              className="min-w-0 truncate text-ink transition-colors hover:text-accent"
            >
              {row.key.split("/").slice(1).join("/") || row.key}
              {!row.in_field && (
                <span aria-hidden className="ml-1.5 text-label text-ink-faint">
                  ·  left the field
                </span>
              )}
            </Link>
            {rated && (
              <span className="text-right text-ink">
                {row.rating === null ? (
                  /* Not 1500. An unrated model is not an average one, and printing a number here
                     would make exactly the claim the deviation exists to avoid. */
                  <span className="text-meta text-ink-faint">unrated</span>
                ) : (
                  <>
                    {Math.round(row.rating)}
                    {/* Same mark as the leaderboard, for the same reason: a reader who does not
                        think in deviations still has to be told this one is not settled yet. */}
                    {row.rating_provisional && (
                      <span
                        className="text-ink-faint"
                        title="Provisional — too few games in this event to settle the rating"
                      >
                        ?
                      </span>
                    )}
                    {row.rating_deviation !== null && (
                      /* The deviation is not decoration: it is what stops a two-game rating being
                         read as a two-hundred-game one. */
                      <span className="ml-1 hidden text-meta text-ink-faint sm:inline">
                        ± {Math.round(row.rating_deviation)}
                      </span>
                    )}
                  </>
                )}
              </span>
            )}
            <span
              className={`hidden text-right sm:block ${rated ? "text-meta text-ink-faint" : "text-ink"}`}
            >
              {row.score.toFixed(1)}
            </span>
            <span className="hidden text-right text-meta text-ink-faint sm:block">
              {row.wins}/{row.draws}/{row.losses}
            </span>
            {!rated && (
              <span className="hidden text-right text-meta text-ink-faint sm:block">
                {row.sonneborn_berger.toFixed(1)}
              </span>
            )}
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
      <dt className="font-mono text-label uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd className={`tabular mt-1 font-mono text-sm ${colour}`}>{value}</dd>
      {note && <p className="tabular mt-0.5 font-mono text-label text-ink-faint">{note}</p>}
    </div>
  );
}
