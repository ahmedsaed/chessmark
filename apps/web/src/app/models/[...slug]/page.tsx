import Link from "next/link";
import { notFound } from "next/navigation";

import { GameCard } from "@/components/GameCard";
import { CreditBadge } from "@/components/ModelPicker";
import { getModel, listGamesByModel } from "@/lib/api";
import type { LeaderboardRow, ModelDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * A catch-all segment, because an OpenRouter id contains a slash: `google/gemini-3.7-flash` is one
 * identifier, not a nested route. `params.slug` arrives as `["google", "gemini-3.7-flash"]` and is
 * rejoined.
 */
function slugOf(parts: string[]): string {
  return parts.join("/");
}

export async function generateMetadata({ params }: PageProps<"/models/[...slug]">) {
  const { slug } = await params;
  const model = await getModel(slugOf(slug));
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
  const id = slugOf(slug);

  const model = await getModel(id);
  if (!model) notFound();

  const games = await listGamesByModel(id);

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

      <Contestants model={model} />
      {model.ratings.length > 0 && <Ratings rows={model.ratings} />}

      {games.length > 0 && (
        <section className="mt-12">
          <div className="mb-4 flex items-baseline gap-3">
            <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
              Games
            </h2>
            <span className="h-px flex-1 bg-line-soft" aria-hidden />
            <span className="tabular font-mono text-[10px] text-ink-faint">{games.length}</span>
          </div>
          {/* Every aggregate above is reachable from here — the games behind the numbers. */}
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {games.map((game) => (
              <GameCard key={game.id} game={game} />
            ))}
          </ul>
        </section>
      )}
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
      <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        Record · every game, ranked or not
      </h2>

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

/** Every precision it is served at, and which endpoint a game would pin (ADR-0015). */
function Contestants({ model }: { model: ModelDetail }) {
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
          No active endpoint serves this model with tool calling, so it cannot be played. Nothing
          is wrong with it — the providers that carried it have stopped.
        </p>
      ) : (
        <>
          <p className="mb-3 max-w-prose text-sm text-ink-dim">
            A contestant is <b className="font-normal text-ink">(model, precision)</b>. The same
            weights served at fp8 and fp4 are different entrants and are ranked apart, because the
            precision changes the result as much as the model does.
          </p>
          <ul className="flex flex-col gap-px border border-line-soft bg-line-soft">
            {model.contestants.map((contestant) => (
              <li
                key={contestant.quantization}
                className="flex flex-wrap items-center gap-3 bg-surface px-3 py-2 font-mono text-xs"
              >
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
              </li>
            ))}
          </ul>
        </>
      )}

      {model.is_floating_alias && (
        <p className="mt-3 border border-bad-deep bg-surface px-3 py-2 text-xs leading-relaxed text-bad">
          This is a floating alias: it points at different weights over time, so a rating computed
          across it would rate no particular model. It can be played, never ranked.
        </p>
      )}
    </section>
  );
}

function Ratings({ rows }: { rows: LeaderboardRow[] }) {
  return (
    <section className="mt-12">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Ratings
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
      </div>

      <ul className="flex flex-col gap-px border border-line-soft bg-line-soft">
        {rows.map((row) => (
          <li key={`${row.model_slug}@${row.quantization}`}>
            <Link
              href={`/leaderboard/${encodeURIComponent(row.model_slug)}?q=${encodeURIComponent(row.quantization)}`}
              className="flex items-center gap-3 bg-surface px-3 py-2.5 font-mono text-xs transition-colors hover:bg-surface-2"
            >
              <span className="border border-good/40 px-1.5 py-px text-[9px] uppercase tracking-wider text-good">
                {row.quantization}
              </span>
              <span className="tabular text-accent">
                {Math.round(row.rating)}
                {/* The deviation travels with the rating everywhere it is shown: three games and
                    three hundred must not read as the same claim. */}
                <span className="ml-1 text-[10px] text-ink-faint">
                  ±{Math.round(row.rating_deviation)}
                </span>
              </span>
              <span className="tabular ml-auto text-[10px] text-ink-faint">
                {row.games} ranked game{row.games === 1 ? "" : "s"}
              </span>
            </Link>
          </li>
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
