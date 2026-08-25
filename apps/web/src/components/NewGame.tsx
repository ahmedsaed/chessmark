"use client";

/**
 * Start a game.
 *
 * The only part of the site that spends money, so the only part behind a sign-in (AUTH-02). It is
 * also the only place the ADR-0011 guards are visible to a person: a quota refusal, a rate limit,
 * or a tripped daily budget all arrive here as a sentence rather than a status code, which is the
 * whole reason those errors carry prose and a reset time.
 *
 * You pick a **contestant**, not a model: `model@fp4` and `model@fp8` are different entrants and
 * are ranked apart (ADR-0015). The precision picker exists because that distinction is the point,
 * and the endpoint is shown because it is pinned for the whole game and turned out to change
 * results as much as precision does.
 */

import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { CreditBadge, ModelPicker } from "@/components/ModelPicker";
import type { ModelInfo } from "@/lib/types";

const DEFAULT_WHITE = "google/gemini-3.7-flash";
const DEFAULT_BLACK = "moonshotai/kimi-k2.5";


export function NewGame({ apiUrl, models }: { apiUrl: string; models: ModelInfo[] }) {
  const { getToken } = useAuth();
  const router = useRouter();

  const playable = useMemo(
    () =>
      models
        .filter((model) => model.contestants.length > 0)
        .sort((a, b) => a.openrouter_id.localeCompare(b.openrouter_id)),
    [models],
  );

  const [white, setWhite] = useState(
    () => (playable.some((m) => m.openrouter_id === DEFAULT_WHITE) ? DEFAULT_WHITE : ""),
  );
  const [black, setBlack] = useState(
    () => (playable.some((m) => m.openrouter_id === DEFAULT_BLACK) ? DEFAULT_BLACK : ""),
  );
  const [whiteQuant, setWhiteQuant] = useState<string>("");
  const [blackQuant, setBlackQuant] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* A game costs the sum of its seats (ADR-0016) — two frontier models is a real decision, and
     the total is the number that makes it one. */
  const priceOf = (slug: string) =>
    playable.find((m) => m.openrouter_id === slug)?.credit_cost ?? 0;
  const price = priceOf(white) + priceOf(black);

  async function start() {
    setBusy(true);
    setError(null);

    try {
      const token = await getToken();
      const response = await fetch(`${apiUrl}/games`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          white,
          black,
          // Omitted means "the healthiest endpoint at whatever precision", which is then recorded.
          white_quantization: whiteQuant || null,
          black_quantization: blackQuant || null,
          max_plies: 300,
        }),
      });

      if (!response.ok) {
        // The guards write for a person: which limit, and when it lifts. Surfacing `detail`
        // verbatim is what makes that effort visible instead of showing "429".
        const body = await response.json().catch(() => null);
        setError(body?.detail ?? `Could not start the game (HTTP ${response.status}).`);
        return;
      }

      const game = await response.json();
      router.push(`/games/${game.id}`);
    } catch {
      setError("Could not reach the API. Is it running?");
    } finally {
      setBusy(false);
    }
  }

  const ready = white && black && !busy;

  return (
    <section className="flex flex-col gap-3 border border-line bg-surface-2 p-4">
      <h2 className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        Start a game · {playable.length} playable models
      </h2>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto_1fr_auto]">
        <ModelPicker
          label="White"
          value={white}
          onChange={(next) => {
            setWhite(next);
            setWhiteQuant("");
          }}
          models={playable}
          quantization={whiteQuant}
          onQuantizationChange={setWhiteQuant}
        />
        <span className="self-center text-center font-mono text-[11px] text-ink-faint">vs</span>
        <ModelPicker
          label="Black"
          value={black}
          onChange={(next) => {
            setBlack(next);
            setBlackQuant("");
          }}
          models={playable}
          quantization={blackQuant}
          onQuantizationChange={setBlackQuant}
        />

        {white && black && (
          <span className="flex items-end gap-1.5 pb-2">
            <CreditBadge credits={price} />
          </span>
        )}

        <button
          type="button"
          onClick={start}
          disabled={!ready}
          className="self-end border border-accent-deep bg-accent px-4 py-2 font-mono text-[11px] uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40"
        >
          {busy ? "starting…" : "play"}
        </button>
      </div>

      {error && (
        <p className="border border-bad-deep bg-surface px-3 py-2 text-xs leading-relaxed text-bad">
          {error}
        </p>
      )}
    </section>
  );
}
