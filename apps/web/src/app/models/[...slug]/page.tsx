import Link from "next/link";
import { notFound } from "next/navigation";

import { GameCard } from "@/components/GameCard";
import { CreditBadge } from "@/components/ModelPicker";
import { getModel, listGamesByModel } from "@/lib/api";
import { modelSlugFromSegments } from "@/lib/models";
import type { Contestant, GameSummary, LeaderboardRow, ModelDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Everything known about one model — the only page about it.
 *
 * `/leaderboard/{slug}?q=fp8` used to be a second one. It showed the contestant's rating and the
 * ratable games behind it; this one showed the registry facts and the record over *every* game.
 * Both printed a "W / D / L" heading, over different sets of games, and neither said so — so which
 * numbers a reader saw depended on whether they arrived from the leaderboard or the tournament
 * table. That page now redirects here and its contents are the contestant blocks below.
 *
 * The three scopes stay visibly apart rather than being averaged into one figure:
 *
 * * **Record** — every game, ranked or not.
 * * **Contestants** — the rating per precision, and the games it was computed from (BENCH-02).
 * * **Played, did not count** — the difference, with a reason each (BENCH-10).
 */


export async function generateMetadata({ params }: PageProps<"/models/[...slug]">) {
  const { slug } = await params;
  const model = await getModel(modelSlugFromSegments(slug));
  if (!model) return { title: "Model not found" };

  return {
    title: model.display_name,
    description: `${model.display_name} on Chessmark: ${model.stats.games} game${
      model.stats.games === 1 ? "" : "s"
    }, ${(model.stats.illegal_per_move * 100).toFixed(1)}% illegal moves.`,
  };
}

export default async function ModelPage({ params }: PageProps<"/models/[...slug]">) {
  const { slug } = await params;
  const id = modelSlugFromSegments(slug);

  const model = await getModel(id);
  if (!model) notFound();

  /* Asked for by the id the **registry** holds, not the one rebuilt from the URL. A dynamic
     segment arrives percent-encoded, and a slug that is one `%3A` away from right still finds the
     model in a path while matching nothing in a query — see `modelSlugFromSegments`. Using the
     answer's own spelling removes the class of fault rather than this instance of it.

     The ceiling rather than the default fifty: the sections below partition this list, and a game
     the page fetched no summary for would silently vanish from whichever section it belongs to. */
  const games = await listGamesByModel(model.openrouter_id, 200);
  const gamesById = new Map(games.map((game) => [game.id, game]));

  const rated = new Set(Object.values(model.rated_games).flat());
  const excluded = new Set(model.excluded.map((entry) => entry.game_id));
  const unfinished = games.filter((game) => !rated.has(game.id) && !excluded.has(game.id));

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <Link
        href="/models"
        className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
      >
        ← All models
      </Link>

      <div className="mt-4 flex flex-wrap items-baseline gap-3">
        <h1 className="font-serif text-4xl leading-tight text-ink">{model.display_name}</h1>
        <CreditBadge credits={model.credit_cost} />
      </div>
      <p className="mt-1 font-mono text-xs text-ink-faint">{model.openrouter_id}</p>

      <Facts model={model} />

      {model.stats.games === 0 ? (
        <p className="mt-10 border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          This model has never played a game here. Everything above comes from the registry; the
          numbers below appear once it has.
        </p>
      ) : (
        <Record model={model} />
      )}

      {games.length === 0 && model.stats.games > 0 && (
        /* The record says it has played and we have no games to show, which is a failed read, not
           an empty history. Said out loud because the three sections below are conditional:
           without this, an unreachable API and a model that has never played render the same page
           — the silence `reportFailure` was written to end, reappearing in the UI. */
        <p className="mt-10 border border-bad-deep bg-surface px-4 py-3 text-sm text-bad">
          This model&rsquo;s {model.stats.games} games could not be loaded. The numbers above are
          right; the lists below are missing, not empty.
        </p>
      )}

      <Contestants model={model} gamesById={gamesById} />
      <NotCounted model={model} gamesById={gamesById} />
      <Unfinished games={unfinished} />
    </main>
  );
}

/** Registry facts: what the catalogue says, before any game was played. */
function Facts({ model }: { model: ModelDetail }) {
  const perMillion = (value: string) => {
    const n = Number(value) * 1_000_000;
    return Number.isFinite(n) ? `$${n.toFixed(2)}` : "—";
  };

  return (
    <dl className="mt-8 grid grid-cols-2 gap-px border border-line-soft bg-line-soft sm:grid-cols-4">
      <Fact label="Input" value={`${perMillion(model.prompt_usd_per_token)}/M`} />
      <Fact label="Output" value={`${perMillion(model.completion_usd_per_token)}/M`} />
      <Fact
        label="Context"
        value={model.context_length ? `${Math.round(model.context_length / 1000)}k` : "—"}
        note="≈1.8k tokens a ply"
      />
      <Fact label="Reasoning" value={model.supports_reasoning ? "yes" : "no"} />
    </dl>
  );
}

/** What it has actually done — over every game, not only the ratable ones. */
function Record({ model }: { model: ModelDetail }) {
  const s = model.stats;
  const usd = (value: string) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return n < 0.001 && n > 0 ? `$${n.toFixed(6)}` : `$${n.toFixed(3)}`;
  };

  return (
    <section className="mt-10">
      <h2 className="mb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        Record · every game, ranked or not
      </h2>
      {/* Named here because the rating below counts fewer games, and a page that prints two
          W/D/L figures without saying which is which is the confusion this merge removed. */}
      <p className="mb-4 text-sm leading-relaxed text-ink-dim">
        Exhibitions, human games and ranked games alike. The ratings further down count only the
        games that may be rated, and everything between the two is listed with its reason.
      </p>

      <dl className="grid grid-cols-2 gap-px border border-line-soft bg-line-soft sm:grid-cols-4">
        <Fact
          label="Games"
          value={String(s.games)}
          note={s.seats !== s.games ? `${s.seats} seats — it has played itself` : undefined}
        />
        <Fact label="W / D / L" value={`${s.wins} / ${s.draws} / ${s.losses}`} />
        {/* The benchmark's headline number, and the reason the project exists. */}
        <Fact
          label="Illegal per move"
          value={`${(s.illegal_per_move * 100).toFixed(2)}%`}
          note={`${s.illegal_attempts} in ${s.moves_played} moves`}
          tone={s.illegal_attempts > 0 ? "bad" : "good"}
        />
        <Fact
          label="Forfeits"
          value={String(s.forfeits)}
          tone={s.forfeits > 0 ? "bad" : undefined}
        />
        <Fact label="Cost" value={usd(s.total_cost_usd)} note={`${usd(s.cost_per_game)} a game`} />
        <Fact label="Tokens" value={s.total_tokens.toLocaleString()} />
        <Fact
          label="Cache rate"
          value={s.cache_rate === null ? "—" : `${Math.round(s.cache_rate * 100)}%`}
          note="of the prompt"
        />
        <Fact
          label="Latency"
          value={s.mean_latency_ms === null ? "—" : `${Math.round(s.mean_latency_ms)}ms`}
          note={`${s.llm_calls} calls`}
        />
      </dl>
    </section>
  );
}

/**
 * Every precision this model is served at, with its rating and the games behind it.
 *
 * Was two sections here and a whole second page elsewhere. A contestant is one thing — the
 * endpoint that serves it, the rating it holds, and the games that produced that rating — so it
 * is one block.
 */
function Contestants({
  model,
  gamesById,
}: {
  model: ModelDetail;
  gamesById: Map<string, GameSummary>;
}) {
  return (
    <section className="mt-12">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Contestants
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
      </div>

      {model.contestants.length === 0 ? (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          No active endpoint serves this model with tool calling, so it cannot be played. Nothing is
          wrong with it — the providers that carried it have stopped.
        </p>
      ) : (
        <>
          <p className="mb-4 text-sm leading-relaxed text-ink-dim">
            A contestant is <b className="font-normal text-ink">(model, precision)</b>. The same
            weights served at fp8 and fp4 are different entrants and are ranked apart, because the
            precision changes the result as much as the model does.
          </p>
          <div className="flex flex-col gap-6">
            {model.contestants.map((contestant) => (
              <ContestantBlock
                key={contestant.quantization}
                contestant={contestant}
                rating={model.ratings.find(
                  (row) => row.quantization === contestant.quantization,
                )}
                games={(
                  model.rated_games[`${model.openrouter_id}@${contestant.quantization}`] ?? []
                )
                  .map((gameId) => gamesById.get(gameId))
                  .filter((game): game is GameSummary => game !== undefined)}
              />
            ))}
          </div>
        </>
      )}

      {model.is_floating_alias && (
        <p className="mt-4 border border-bad-deep bg-surface px-3 py-2 text-xs leading-relaxed text-bad">
          This is a floating alias: it points at different weights over time, so a rating computed
          across it would rate no particular model. It can be played, never ranked.
        </p>
      )}
    </section>
  );
}

/**
 * One precision: what serves it, what it is rated, and the games that produced that rating.
 *
 * The anchor is what `/leaderboard/{slug}?q=fp8` redirects to, so a link published before the
 * merge still lands on the thing it named.
 */
function ContestantBlock({
  contestant,
  rating,
  games,
}: {
  contestant: Contestant;
  rating?: LeaderboardRow;
  games: GameSummary[];
}) {
  return (
    <div id={`c-${contestant.quantization}`} className="scroll-mt-6 border border-line-soft">
      <div className="flex flex-wrap items-center gap-3 border-b border-line-soft bg-surface-2 px-3 py-2.5 font-mono text-xs">
        <span className="border border-good/40 px-1.5 py-px text-[9px] uppercase tracking-wider text-good">
          {contestant.quantization}
        </span>
        <span className="text-ink">{contestant.provider}</span>
        {contestant.uptime_1d !== null && (
          <span className="tabular text-[10px] text-ink-faint">
            {contestant.uptime_1d.toFixed(1)}% uptime
          </span>
        )}
        <span className="ml-auto text-[10px] text-ink-faint">
          {contestant.endpoint_count} endpoint
          {contestant.endpoint_count === 1 ? "" : "s"}
          {contestant.endpoint_count === 1 && " — an outage takes it with them"}
        </span>
      </div>

      {rating === undefined ? (
        <p className="px-3 py-4 text-sm text-ink-dim">
          Not rated at this precision. Nothing it has played here is ratable yet — anything it did
          play is listed below with the reason it did not count.
        </p>
      ) : (
        <dl className="grid grid-cols-2 gap-px border-b border-line-soft bg-line-soft sm:grid-cols-4">
          {/* The deviation travels with the rating everywhere it is shown: three games and three
              hundred must not read as the same claim. */}
          <Fact
            label="Rating"
            value={`${Math.round(rating.rating)} ± ${Math.round(rating.rating_deviation)}`}
            note={rating.provisional ? "provisional" : `over ${rating.games} rated games`}
          />
          <Fact label="W / D / L" value={`${rating.wins} / ${rating.draws} / ${rating.losses}`} />
          <Fact
            label="Illegal per move"
            value={`${(rating.illegal_per_move * 100).toFixed(2)}%`}
            note={`${rating.illegal_attempts} in ${rating.moves_played} moves`}
            tone={rating.illegal_attempts > 0 ? "bad" : "good"}
          />
          <Fact
            label="Forfeits"
            value={String(rating.forfeits)}
            tone={rating.forfeits > 0 ? "bad" : undefined}
          />
        </dl>
      )}

      {games.length > 0 && (
        <div className="p-3">
          {/* Every published number reaches the games that produced it, or the ranking is asking
              to be taken on faith (BENCH-02). */}
          <h3 className="mb-3 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            {games.length} rated game{games.length === 1 ? "" : "s"}
          </h3>
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {games.map((game) => (
              <GameCard key={game.id} game={game} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * The finished games that did not count, grouped by the reason (BENCH-10).
 *
 * This section *is* the difference between the record and the ratings. Without it the page prints
 * two W/D/L figures and leaves the reader to guess why they disagree — which is what having two
 * pages did, except the reader could not even see both at once.
 */
function NotCounted({
  model,
  gamesById,
}: {
  model: ModelDetail;
  gamesById: Map<string, GameSummary>;
}) {
  const byReason = new Map<string, GameSummary[]>();
  for (const entry of model.excluded) {
    const game = gamesById.get(entry.game_id);
    if (game === undefined) continue;
    byReason.set(entry.reason, [...(byReason.get(entry.reason) ?? []), game]);
  }

  if (byReason.size === 0) return null;

  const total = [...byReason.values()].reduce((sum, group) => sum + group.length, 0);

  return (
    <section className="mt-12">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Played, did not count
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-[10px] text-ink-faint">{total}</span>
      </div>

      <p className="mb-5 text-sm leading-relaxed text-ink-dim">
        In the record above, and in no rating. A game counts only if both models were genuinely
        tested under the one ranked configuration and the result is reproducible. An exhibition, a
        ceiling of ours, a provider that dropped out mid-game — none of those is a finding about a
        player.
      </p>

      <div className="flex flex-col gap-6">
        {[...byReason].map(([reason, group]) => (
          <div key={reason}>
            <h3 className="mb-3 font-mono text-[11px] leading-relaxed text-ink-dim">
              {reason} <span className="text-ink-faint">· {group.length}</span>
            </h3>
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {group.map((game) => (
                <GameCard key={game.id} game={game} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Games not yet judged — running, paused, or waiting. No rating can see them yet. */
function Unfinished({ games }: { games: GameSummary[] }) {
  if (games.length === 0) return null;

  return (
    <section className="mt-12">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          In progress
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-[10px] text-ink-faint">{games.length}</span>
      </div>
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {games.map((game) => (
          <GameCard key={game.id} game={game} />
        ))}
      </ul>
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
      <dt className="font-mono text-[9px] uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd className={`tabular mt-1 font-mono text-sm ${colour}`}>{value}</dd>
      {note && <p className="tabular mt-0.5 font-mono text-[9.5px] text-ink-faint">{note}</p>}
    </div>
  );
}
