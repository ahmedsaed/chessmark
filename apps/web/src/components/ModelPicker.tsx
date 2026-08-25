"use client";

/**
 * Choosing a contestant.
 *
 * Shared by both ways of starting a game — two models, or you against one — because the choice is
 * the same choice. You pick a **contestant**, not a model: `model@fp4` and `model@fp8` are
 * different entrants and are ranked apart (ADR-0015). The precision picker exists because that
 * distinction is the point, and the endpoint is shown because it is pinned for the whole game and
 * turned out to change results as much as precision does.
 *
 * **This was a native `<select>` and could not stay one.** It listed two models when the catalogue
 * was unseeded; the real catalogue is 330 across 36 providers, with `openai` alone holding 86. A
 * flat dropdown of 330 options cannot be searched, cannot be grouped in a way anyone can read, and
 * cannot show what a model costs — and cost is now the thing you choose on, since a seat runs from
 * 1 to 6 credits (ADR-0016).
 *
 * So: a disclosure with a search box and providers collapsed. Collapsed is the default because 36
 * rows fit on a screen and 330 do not; a provider carries its model count and its cheapest price,
 * which is enough to decide whether to open it.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { browseModels, countModels } from "@/lib/models";
import type { ModelInfo } from "@/lib/types";

function usdPerMillion(perToken: string): string {
  const value = Number(perToken) * 1_000_000;
  return Number.isFinite(value) ? `$${value.toFixed(2)}/M` : "—";
}

export function ModelPicker({
  label,
  value,
  onChange,
  models,
  quantization,
  onQuantizationChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  models: ModelInfo[];
  quantization: string;
  onQuantizationChange: (value: string) => void;
}) {
  const chosen = models.find((model) => model.openrouter_id === value);
  const entrants = chosen?.contestants ?? [];
  // Empty means "let the server pick the healthiest", which is the sane default and is recorded.
  const entrant = entrants.find((c) => c.quantization === quantization) ?? entrants[0];

  const labelId = useId();

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span id={labelId} className="font-mono text-[9px] uppercase tracking-[0.12em] text-ink-faint">
        {label}
      </span>

      <Dropdown
        labelId={labelId}
        models={models}
        value={value}
        onChange={onChange}
        chosen={chosen}
      />

      {entrants.length > 1 && (
        <span className="flex flex-wrap items-center gap-1">
          {entrants.map((option) => {
            const active = option.quantization === (quantization || entrants[0].quantization);
            return (
              <button
                key={option.quantization}
                type="button"
                onClick={() => onQuantizationChange(option.quantization)}
                aria-pressed={active}
                title={`${option.provider}, uptime ${option.uptime_1d?.toFixed(1) ?? "?"}% — a separate entrant`}
                className={`border px-1.5 py-px font-mono text-[9px] uppercase tracking-wider transition-colors ${
                  active
                    ? "border-accent bg-accent text-on-accent"
                    : "border-line text-ink-faint hover:text-ink-dim"
                }`}
              >
                {option.quantization}
              </button>
            );
          })}
        </span>
      )}

      {chosen && entrant && (
        <span className="tabular font-mono text-[9.5px] text-ink-faint">
          in {usdPerMillion(chosen.prompt_usd_per_token)} · out{" "}
          {usdPerMillion(chosen.completion_usd_per_token)} ·{" "}
          <span className="text-good">{entrant.provider}</span>
          {entrant.uptime_1d !== null && ` ${entrant.uptime_1d.toFixed(1)}%`}
        </span>
      )}
    </div>
  );
}

function Dropdown({
  labelId,
  models,
  value,
  onChange,
  chosen,
}: {
  labelId: string;
  models: ModelInfo[];
  value: string;
  onChange: (value: string) => void;
  chosen: ModelInfo | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const root = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);

  const groups = useMemo(() => browseModels(models, query), [models, query]);
  const total = countModels(groups);
  const searching = query.trim() !== "";

  /* A search that leaves everything collapsed has shown the reader nothing: they typed a model
     name and got a list of providers. Searching expands, browsing does not. */
  const isExpanded = (provider: string) => searching || expanded.has(provider);

  useEffect(() => {
    if (!open) return;

    // Focus the search box, because typing is the fast path through 330 models.
    search.current?.focus();

    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function pick(slug: string) {
    onChange(slug);
    setOpen(false);
    setQuery("");
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-labelledby={labelId}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => {
          const next = !open;
          setOpen(next);
          /* Open onto the current choice. Everything starts collapsed, so without this a reader
             who reopens the picker sees 36 provider rows and none of them is the model they
             already have — the one piece of context the list should never make them hunt for.
             Done here rather than in an effect: an effect that sets state on open is a cascading
             render, and this is a consequence of the click, not of the open state. */
          if (next && chosen) {
            setExpanded((previous) => new Set(previous).add(chosen.provider));
          }
        }}
        className="flex w-full items-center gap-2 border border-line bg-surface px-2 py-2 text-left font-mono text-xs transition-colors hover:border-accent-dim focus-visible:border-accent"
      >
        <span className={`min-w-0 flex-1 truncate ${chosen ? "text-ink" : "text-ink-faint"}`}>
          {chosen ? chosen.openrouter_id : "choose a model…"}
        </span>
        {chosen && <CreditBadge credits={chosen.credit_cost} />}
        <span aria-hidden className="flex-none text-[9px] text-ink-faint">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-labelledby={labelId}
          /* Above everything, because the picker sits inside a bordered panel that would
             otherwise clip it, and wide enough that a long slug is readable. */
          className="absolute left-0 right-0 z-50 mt-1 flex max-h-[22rem] min-w-[20rem] flex-col border border-line bg-surface-2 shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
        >
          <div className="flex-none border-b border-line bg-surface-3 p-2">
            <input
              ref={search}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search models or providers…"
              aria-label="Search models"
              className="w-full border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent-dim focus:outline-none"
            />
            <p className="tabular mt-1.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
              {total} model{total === 1 ? "" : "s"} · {groups.length} provider
              {groups.length === 1 ? "" : "s"}
            </p>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {groups.length === 0 && (
              <p className="p-3 font-mono text-[11px] text-ink-faint">
                Nothing matches “{query.trim()}”.
              </p>
            )}

            {groups.map((group) => (
              <div key={group.provider} className="border-b border-line-soft last:border-b-0">
                <button
                  type="button"
                  aria-expanded={isExpanded(group.provider)}
                  onClick={() =>
                    setExpanded((previous) => {
                      const next = new Set(previous);
                      if (next.has(group.provider)) next.delete(group.provider);
                      else next.add(group.provider);
                      return next;
                    })
                  }
                  className="flex w-full items-center gap-2 px-2 py-1.5 text-left font-mono text-[11px] transition-colors hover:bg-surface-3"
                >
                  <span aria-hidden className="w-2 flex-none text-[9px] text-machine">
                    {isExpanded(group.provider) ? "▾" : "▸"}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-ink">{group.provider}</span>
                  <span className="tabular flex-none text-[9.5px] text-ink-faint">
                    {group.models.length}
                  </span>
                  {/* The cheapest price decides whether opening a provider is worth it. */}
                  <CreditBadge credits={group.cheapest} muted />
                </button>

                {isExpanded(group.provider) && (
                  <ul>
                    {group.models.map((model) => {
                      const selected = model.openrouter_id === value;
                      return (
                        <li key={model.id}>
                          <button
                            type="button"
                            role="option"
                            aria-selected={selected}
                            onClick={() => pick(model.openrouter_id)}
                            className={`flex w-full items-center gap-2 py-1.5 pl-6 pr-2 text-left font-mono text-[11px] transition-colors ${
                              selected
                                ? "bg-accent-deep/40 text-ink"
                                : "text-ink-dim hover:bg-surface-3 hover:text-ink"
                            }`}
                          >
                            <span className="min-w-0 flex-1 truncate">
                              {model.openrouter_id.split("/").slice(1).join("/") ||
                                model.openrouter_id}
                            </span>
                            <span className="tabular hidden flex-none text-[9px] text-ink-faint sm:inline">
                              {usdPerMillion(model.prompt_usd_per_token)}
                            </span>
                            <CreditBadge credits={model.credit_cost} />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * What a seat costs, in credits.
 *
 * On the face of the picker rather than in a tooltip: the catalogue spans a 300-fold price range
 * (ADR-0016), so with 330 models to choose between, a name alone is not something a person can
 * choose on. The tiers are coloured because the number alone reads as trivia — gold for the
 * expensive end is the same signal the rest of the site uses for "this costs something".
 */
export function CreditBadge({ credits, muted = false }: { credits: number; muted?: boolean }) {
  const tone =
    credits >= 6
      ? "border-bad-deep text-bad"
      : credits >= 3
        ? "border-accent-deep text-accent"
        : "border-line text-ink-dim";

  return (
    <span
      title={`${credits} credit${credits === 1 ? "" : "s"} to start a game against this model`}
      className={`tabular flex-none border px-1 py-px font-mono text-[9px] uppercase tracking-wider ${tone} ${
        muted ? "opacity-70" : ""
      }`}
    >
      {credits} cr
    </span>
  );
}
