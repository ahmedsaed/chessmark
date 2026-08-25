"use client";

/**
 * The whole registry, filtered as you type.
 *
 * **No network request per keystroke.** 275 models is a few hundred kilobytes fetched once by the
 * page above, and filtering them in the browser is both faster and simpler than a debounced
 * endpoint that has to be rate-limited and cached. The exit criterion asserts that choice rather
 * than merely permitting it.
 *
 * Grouped by provider, the same shape the picker uses (`lib/models.ts`), because someone scanning
 * a catalogue thinks in vendors — "what does Anthropic have" is the question, not "which models
 * contain the letters a-n-t".
 */

import Link from "next/link";
import { useMemo, useState } from "react";

import { CreditBadge } from "@/components/ModelPicker";
import { browseModels, countModels } from "@/lib/models";
import type { ModelInfo } from "@/lib/types";

function usdPerMillion(perToken: string): string {
  const value = Number(perToken) * 1_000_000;
  return Number.isFinite(value) ? `$${value.toFixed(2)}` : "—";
}

function context(tokens: number | null): string {
  if (!tokens) return "—";
  return tokens >= 1_000_000
    ? `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 ? 1 : 0)}M`
    : `${Math.round(tokens / 1000)}k`;
}

export function ModelTable({ models }: { models: ModelInfo[] }) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => browseModels(models, query), [models, query]);
  const shown = countModels(groups);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search models or providers…"
          aria-label="Search models"
          className="min-w-0 flex-1 border border-line bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:text-ink-faint focus:border-accent-dim focus:outline-none"
        />
        <span className="tabular flex-none font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
          {shown} of {models.length}
        </span>
      </div>

      {groups.length === 0 && (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          Nothing matches “{query.trim()}”.
        </p>
      )}

      {groups.map((group) => (
        <section key={group.provider}>
          <div className="mb-2 flex items-baseline gap-3">
            <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
              {group.provider}
            </h2>
            <span className="h-px flex-1 bg-line-soft" aria-hidden />
            <span className="tabular font-mono text-[10px] text-ink-faint">
              {group.models.length}
            </span>
          </div>

          <ul className="flex flex-col gap-px border border-line-soft bg-line-soft">
            {group.models.map((model) => (
              <li key={model.id}>
                <Link
                  href={`/models/${model.openrouter_id}`}
                  className="grid grid-cols-[1fr_auto] items-center gap-3 bg-surface px-3 py-2 transition-colors hover:bg-surface-2 sm:grid-cols-[1fr_5rem_5rem_4rem_auto]"
                >
                  <span className="min-w-0 truncate font-mono text-xs text-ink">
                    {model.openrouter_id.split("/").slice(1).join("/") || model.openrouter_id}
                  </span>

                  {/* UI-07: cost, context window, reasoning support — the three facts that decide
                      whether a model is worth playing, and whether it can finish. */}
                  <span
                    className="tabular hidden font-mono text-[10px] text-ink-faint sm:block"
                    title="input / output per million tokens"
                  >
                    {usdPerMillion(model.prompt_usd_per_token)} /{" "}
                    {usdPerMillion(model.completion_usd_per_token)}
                  </span>
                  <span
                    className="tabular hidden font-mono text-[10px] text-ink-faint sm:block"
                    title="context window — the transcript grows ~1.8k tokens a ply"
                  >
                    {context(model.context_length)}
                  </span>
                  <span className="hidden font-mono text-[10px] sm:block">
                    {model.supports_reasoning ? (
                      <span className="text-machine" title="exposes reasoning">
                        reasons
                      </span>
                    ) : (
                      <span className="text-ink-faint">—</span>
                    )}
                  </span>

                  <CreditBadge credits={model.credit_cost} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
