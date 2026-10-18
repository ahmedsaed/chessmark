import Link from "next/link";

import { endingLabel } from "@/lib/archive";
import { games, illegalRateLabel, resultBarLabel } from "@/lib/prose";
import type { ModelDetail, ModelProfile as Profile, RecordCount } from "@/lib/types";

/**
 * What a model has done, as tables and bars: by colour, by ending, against whom, and from which
 * openings (ADR-0069).
 *
 * **Tables, not paragraphs.** The first version said all of this in sentences, and a paragraph of
 * numbers is the wrong form for a reader comparing them. The tables are still text, so a search
 * engine and a screen reader read them as they are; only the bars are pictures, and each carries
 * its sentence from `lib/prose` as its accessible name.
 */
export function ModelProfile({ model }: { model: ModelDetail }) {
  const profile = model.profile;
  if (!profile) return null;

  return (
    <>
      <section className="mt-12">
        <Heading>Results</Heading>
        <div className="grid gap-8 sm:grid-cols-2">
          <ByColour profile={profile} />
          <Endings profile={profile} />
        </div>
        {model.runtime !== "decision" && profile.field_illegal_per_move !== null && (
          <IllegalRate mine={model.stats.illegal_per_move} field={profile.field_illegal_per_move} />
        )}
      </section>

      <HeadToHead profile={profile} />
      <Openings profile={profile} />
    </>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline gap-3">
      <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">{children}</h2>
      <span className="h-px flex-1 bg-line-soft" aria-hidden />
    </div>
  );
}

function Subheading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-3 font-mono text-label uppercase tracking-[0.14em] text-ink-faint">
      {children}
    </h3>
  );
}

/** "3–1–0", in a column that lines up. */
function Score({ record }: { record: RecordCount }) {
  return (
    <span className="tabular whitespace-nowrap font-mono text-data text-ink">
      {record.wins}–{record.draws}–{record.losses}
    </span>
  );
}

/**
 * Wins, draws and losses as one bar. A picture of the numbers beside it, so it is named by the
 * same numbers in words rather than left for a screen reader to skip or to misread.
 */
function ResultBar({ label, record }: { label: string; record: RecordCount }) {
  const total = games(record);
  const share = (n: number) => `${total === 0 ? 0 : (n / total) * 100}%`;
  return (
    <div
      role="img"
      aria-label={label}
      className="flex h-1.5 w-full overflow-hidden bg-surface-3"
    >
      <span className="bg-good" style={{ width: share(record.wins) }} />
      <span className="bg-ink-faint" style={{ width: share(record.draws) }} />
      <span className="bg-bad" style={{ width: share(record.losses) }} />
    </div>
  );
}

/** One labelled row: a name, its record, and the bar under them. */
function ResultRow({ name, record }: { name: string; record: RecordCount }) {
  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-sm text-ink-dim">{name}</span>
        <span className="flex flex-none items-baseline gap-3">
          <span className="tabular font-mono text-meta text-ink-faint">
            {games(record)} {games(record) === 1 ? "game" : "games"}
          </span>
          <Score record={record} />
        </span>
      </div>
      <ResultBar label={resultBarLabel(name, record)} record={record} />
    </li>
  );
}

function Legend() {
  const key = [
    ["bg-good", "won"],
    ["bg-ink-faint", "drawn"],
    ["bg-bad", "lost"],
  ];
  return (
    <p className="mt-3 flex gap-4 font-mono text-meta text-ink-faint" aria-hidden>
      {key.map(([colour, word]) => (
        <span key={word} className="flex items-center gap-1.5">
          <i className={`block h-1.5 w-3 ${colour}`} />
          {word}
        </span>
      ))}
    </p>
  );
}

function ByColour({ profile }: { profile: Profile }) {
  return (
    <div>
      <Subheading>By colour · won–drawn–lost</Subheading>
      <ul className="flex flex-col gap-4">
        <ResultRow name="As White" record={profile.by_colour.white} />
        <ResultRow name="As Black" record={profile.by_colour.black} />
      </ul>
      <Legend />
    </div>
  );
}

function Endings({ profile }: { profile: Profile }) {
  const endings = profile.endings.filter((ending) => games(ending.record) > 0);
  return (
    <div>
      <Subheading>How its games ended</Subheading>
      <ul className="flex flex-col gap-4">
        {endings.map((ending) => (
          <ResultRow
            key={ending.termination}
            name={capitalise(endingLabel(ending.termination))}
            record={ending.record}
          />
        ))}
      </ul>
    </div>
  );
}

/**
 * Its illegal-move rate against every model's. The headline number of the benchmark, and one that
 * means little alone: 2.5 per 100 is good or bad only next to what the field does.
 */
function IllegalRate({ mine, field }: { mine: number; field: number }) {
  const scale = Math.max(mine, field) || 1;
  /* Accent, not good or bad. The record above already colours this same rate red for any illegal
     attempt at all; a green bar for being under the field's average put two verdicts on one number. */
  const rows = [
    { name: "This model", value: mine, colour: "bg-accent" },
    { name: "Every model", value: field, colour: "bg-ink-faint" },
  ];
  return (
    <div className="mt-8">
      <Subheading>Illegal move attempts per 100 moves</Subheading>
      <div role="img" aria-label={illegalRateLabel(mine, field)} className="flex flex-col gap-2">
        {rows.map((row) => (
          <div
            key={row.name}
            className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-3"
            aria-hidden
          >
            <span className="text-sm text-ink-dim">{row.name}</span>
            <span className="h-1.5 bg-surface-3">
              <span
                className={`block h-full ${row.colour}`}
                style={{ width: `${(row.value / scale) * 100}%` }}
              />
            </span>
            <span className="tabular text-right font-mono text-data text-ink">
              {(row.value * 100).toFixed(1)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Every opponent, most played first. A person is "People", never named and never linked. */
function HeadToHead({ profile }: { profile: Profile }) {
  if (profile.opponents.length === 0) return null;
  return (
    <section className="mt-12">
      {/* Scoped in the heading, as Record is: these rows sum to Record's W/D/L, not to a
          contestant's, which counts only the games that may be rated. */}
      <Heading>Head to head · every finished game</Heading>
      <div className="overflow-x-auto border border-line-soft">
        {/* Fixed layout with narrow number columns: left to the browser, the five number columns
            split the width with the names and cut "NVIDIA: Nemotron 3.5 Lightning" short beside
            a column holding one digit. */}
        <table className="w-full min-w-[34rem] table-fixed text-sm">
          <colgroup>
            <col />
            <col className="w-20" />
            <col className="w-20" />
            <col className="w-20" />
            <col className="w-20" />
            <col className="w-20" />
          </colgroup>
          <thead>
            <tr className="border-b border-line-soft bg-surface font-mono text-label uppercase tracking-[0.14em] text-ink-faint">
              <th scope="col" className="px-3 py-2 text-left font-normal">
                Opponent
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                Games
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                Won
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                Drawn
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                Lost
              </th>
              <th
                scope="col"
                className="px-3 py-2 text-right font-normal"
                title="(won + drawn ÷ 2) ÷ games"
              >
                Score
              </th>
            </tr>
          </thead>
          <tbody>
            {profile.opponents.map((opponent) => {
              const played = games(opponent.record);
              const score = (opponent.record.wins + opponent.record.draws / 2) / played;
              return (
                <tr
                  key={opponent.openrouter_id ?? "people"}
                  className="border-b border-line-soft bg-surface last:border-b-0"
                >
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    {opponent.openrouter_id ? (
                      <Link
                        href={`/models/${opponent.openrouter_id}`}
                        /* A visible link to a model page prefetches in a loop (FRONTEND.md): the
                           first version of this table asked for two of them 1,607 times each. */
                        prefetch={false}
                        className="block truncate text-ink underline-offset-4 transition-colors hover:text-accent hover:underline"
                      >
                        {opponent.display_name}
                      </Link>
                    ) : (
                      <span className="text-ink-dim">People</span>
                    )}
                  </th>
                  <Cell>{played}</Cell>
                  <Cell>{opponent.record.wins}</Cell>
                  <Cell>{opponent.record.draws}</Cell>
                  <Cell>{opponent.record.losses}</Cell>
                  <Cell>{Math.round(score * 100)}%</Cell>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Cell({ children }: { children: React.ReactNode }) {
  return (
    <td className="tabular whitespace-nowrap px-3 py-2 text-right font-mono text-data text-ink">
      {children}
    </td>
  );
}

/** The openings its games reached most often, per colour, named from Lichess's table. */
function Openings({ profile }: { profile: Profile }) {
  const sides = [
    { name: "As White", rows: profile.openings.white },
    { name: "As Black", rows: profile.openings.black },
  ].filter((side) => side.rows.length > 0);
  if (sides.length === 0) return null;
  return (
    <section className="mt-12">
      <Heading>Openings</Heading>
      <div className="grid gap-8 sm:grid-cols-2">
        {sides.map((side) => (
          <div key={side.name}>
            <Subheading>{side.name}</Subheading>
            <ol className="flex flex-col gap-px border border-line-soft bg-line-soft">
              {side.rows.map((opening) => (
                <li
                  key={opening.eco + opening.name}
                  className="flex items-baseline gap-3 bg-surface px-3 py-2"
                >
                  <span className="tabular w-9 flex-none font-mono text-meta text-ink-faint">
                    {opening.eco}
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-ink">{opening.name}</span>
                  <span className="tabular flex-none font-mono text-meta text-ink-faint">
                    {opening.games} {opening.games === 1 ? "game" : "games"}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
