"use client";

/**
 * The raw transcript inspector (LOG-01, LOG-07).
 *
 * Every number Chessmark publishes — cost, tokens, cache rate, illegal attempts — is derived from
 * these payloads. The project's claim is that a leaderboard row is one click from its evidence, so
 * this dialog is what that click opens: the request and response exactly as they crossed the wire,
 * unshaped and unsummarised.
 *
 * The API refuses raw payloads while a game is live (invariant 8), so this is reachable only from
 * a finished game.
 */

import { useEffect, useRef, useState } from "react";

import type { RawCall } from "@/lib/types";

function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return amount === 0 ? "$0" : `$${amount.toFixed(6)}`;
}

export function RawTranscript({
  apiUrl,
  gameId,
  turnId,
  label,
  onClose,
}: {
  apiUrl: string;
  gameId: string;
  turnId: number;
  label: string;
  onClose: () => void;
}) {
  const [calls, setCalls] = useState<RawCall[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${apiUrl}/games/${gameId}/turns/${turnId}/raw`, {
      signal: controller.signal,
      headers: { accept: "application/json" },
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`${response.status}`);
        setCalls((await response.json()) as RawCall[]);
      })
      .catch((cause: Error) => {
        if (cause.name !== "AbortError") setError(cause.message);
      });

    return () => controller.abort();
  }, [apiUrl, gameId, turnId]);

  useEffect(() => {
    panel.current?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={`Raw transcript for ${label}`}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full w-full max-w-[900px] flex-col border border-line bg-surface-2 outline-none"
      >
        <div className="flex flex-none items-center gap-3 border-b border-line bg-surface-3 px-4 py-2.5">
          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-accent">
            Raw transcript
          </span>
          <span className="truncate font-mono text-[11px] text-ink-dim">{label}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto flex-none border border-line px-2 py-0.5 font-mono text-[10px] text-ink-faint transition-colors hover:text-ink"
          >
            esc
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && (
            <p className="font-mono text-xs text-bad">
              Could not load the transcript (HTTP {error}).
              {error === "409" && " Raw payloads are published once the game ends."}
            </p>
          )}
          {!calls && !error && (
            <p className="font-mono text-xs text-ink-faint">Loading…</p>
          )}
          {calls?.length === 0 && (
            <p className="font-mono text-xs text-ink-faint">This turn made no LLM calls.</p>
          )}

          <div className="flex flex-col gap-5">
            {calls?.map((call) => (
              <Call key={call.id} call={call} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Call({ call }: { call: RawCall }) {
  return (
    <section className="flex flex-col gap-2">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line-soft pb-1.5">
        <span className="font-mono text-[11px] text-ink">call {call.sequence}</span>
        <span className="font-mono text-[10px] text-ink-faint">{call.model_slug}</span>
        {call.provider && (
          <span className="border border-good/40 px-1 py-px font-mono text-[9px] uppercase tracking-wider text-good">
            {call.provider}
          </span>
        )}
        {call.error && (
          <span className="font-mono text-[10px] text-bad">error: {call.error}</span>
        )}
      </header>

      {/* The numbers and the payload that produced them, side by side — the point of LOG-07. */}
      <dl className="tabular grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[10px] text-ink-faint sm:grid-cols-4">
        <Stat label="prompt" value={call.prompt_tokens.toLocaleString()} />
        <Stat label="cached" value={call.cached_tokens.toLocaleString()} />
        <Stat label="output" value={call.completion_tokens.toLocaleString()} />
        <Stat label="reasoning" value={call.reasoning_tokens.toLocaleString()} />
        <Stat label="cost" value={usd(call.cost_usd)} />
        <Stat label="latency" value={call.latency_ms ? `${call.latency_ms} ms` : "—"} />
        <Stat label="finish" value={call.finish_reason ?? "—"} />
      </dl>

      <Payload title="request" value={call.request} />
      <Payload title="response" value={call.response} />
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt>{label}</dt>
      <dd className="text-ink-dim">{value}</dd>
    </div>
  );
}

function Payload({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(title === "response");
  const text = value === null ? "null" : JSON.stringify(value, null, 2);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:text-ink-dim"
        >
          <span aria-hidden className="text-machine">
            {open ? "▾" : "▸"}
          </span>
          {title}
          <span className="text-ink-faint">· {text.length.toLocaleString()} chars</span>
        </button>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(text)}
          className="border border-line px-1.5 py-px font-mono text-[9px] text-ink-faint transition-colors hover:text-ink"
        >
          copy
        </button>
      </div>

      {open && (
        <pre className="max-h-[340px] overflow-auto border border-line bg-surface p-2.5 font-mono text-[10.5px] leading-relaxed text-ink-dim">
          {text}
        </pre>
      )}
    </div>
  );
}
