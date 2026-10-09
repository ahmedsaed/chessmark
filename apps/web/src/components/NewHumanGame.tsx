"use client";

/**
 * Sit down against a model (HUMAN-01).
 *
 * The endpoint and the whole playing surface — a draggable board, resign, draw offers, the seat
 * resolution — were written and tested in Phase 10's backend pass, and then nothing called them:
 * `createHumanGame` had no caller and `/play` offered only a model-vs-model form. This is that
 * caller.
 *
 * Chat is **off by default and opt-in**, unlike a model-vs-model game. A person's messages are
 * stored `PENDING` and delivered to the model verbatim with no moderation check — Phase 11 owns
 * that check, and until it exists the unmoderated path should be something you choose rather than
 * something you get.
 */

import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { ModelPicker } from "@/components/ModelPicker";
import { GameLimits } from "@/components/GameLimits";
import { TalkToggle } from "@/components/TalkToggle";
import { limitFrom } from "@/lib/credit";
import { DEFAULT_PLIES, pliesFrom } from "@/lib/limits";
import { ApiError, createHumanGame } from "@/lib/api";
import type { Colour, ModelInfo } from "@/lib/types";

const DEFAULT_OPPONENT = "google/gemini-3.7-flash";

export function NewHumanGame({ models }: { models: ModelInfo[] }) {
  const { getToken } = useAuth();
  const router = useRouter();

  const playable = useMemo(
    () =>
      models
        .filter((model) => model.contestants.length > 0)
        .sort((a, b) => a.openrouter_id.localeCompare(b.openrouter_id)),
    [models],
  );

  const [opponent, setOpponent] = useState(() =>
    playable.some((m) => m.openrouter_id === DEFAULT_OPPONENT) ? DEFAULT_OPPONENT : "",
  );
  const [quantization, setQuantization] = useState("");
  const [provider, setProvider] = useState("");
  const [colour, setColour] = useState<Colour>("white");
  const [chat, setChat] = useState(false);
  const [limit, setLimit] = useState("");
  const [plies, setPlies] = useState(String(DEFAULT_PLIES));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);

    try {
      const game = await createHumanGame(await getToken(), {
        model: opponent,
        colour,
        // Omitted means "the healthiest endpoint at whatever precision", which is then recorded.
        model_quantization: quantization || null,
        // Likewise: empty is "auto", and the server pins by uptime (ADR-0066).
        model_provider: provider || null,
        trash_talk_enabled: chat,
        max_usd: limitFrom(limit),
        max_plies: pliesFrom(plies),
      });
      router.push(`/games/${game.id}`);
    } catch (failure) {
      /* The ADR-0011 guards write for a person — which limit, and when it lifts — so their text
         is surfaced verbatim rather than reduced to a status code. */
      setError(
        failure instanceof ApiError
          ? failure.message
          : "Could not reach the API. Is it running?",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-4 border border-line bg-surface-2 p-4">
      <h2 className="font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">
        Play a model · {playable.length} available
      </h2>

      <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[1fr_auto]">
        <ModelPicker
          label="Your opponent"
          value={opponent}
          onChange={(next) => {
            setOpponent(next);
            setQuantization("");
            setProvider("");
          }}
          models={playable}
          quantization={quantization}
          onQuantizationChange={setQuantization}
          provider={provider}
          onProviderChange={setProvider}
        />

        {/* Deliberately not a `fieldset`/`legend`. A legend is positioned into the fieldset's
            border rather than laid out as a flex child, so `gap-1` did not apply to it and these
            buttons sat a few pixels below the model picker's select beside them. A labelled group
            says the same thing to a screen reader and lays out identically to the picker. */}
        <div
          role="group"
          aria-labelledby="you-play-label"
          className="flex min-w-0 flex-col gap-1"
        >
          <span
            id="you-play-label"
            className="font-mono text-label uppercase tracking-[0.12em] text-ink-faint"
          >
            You play
          </span>
          <div className="flex gap-1">
            {(["white", "black"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setColour(option)}
                aria-pressed={colour === option}
                /* `text-xs` and `py-2` match the select exactly, so the two controls are the
                   same height rather than merely similar. */
                className={`border px-3 py-2 font-mono text-xs uppercase tracking-[0.1em] transition-colors ${
                  colour === option
                    ? "border-accent bg-accent text-on-accent"
                    : "border-line bg-surface text-ink-faint hover:text-ink"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      </div>


      <GameLimits
        limit={limit}
        onLimitChange={setLimit}
        plies={plies}
        onPliesChange={setPlies}
        options={
          <TalkToggle
            checked={chat}
            onChange={setChat}
            hint="Chat with the model while you play: it reads what you say and can answer back. Off by default."
          />
        }
      >
        <button
          type="button"
          onClick={start}
          disabled={!opponent || busy}
          className="border border-accent-deep bg-accent px-4 py-2 font-mono text-data uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40"
        >
          {busy ? "seating…" : "sit down"}
        </button>
      </GameLimits>


      {error && (
        <p className="border border-bad-deep bg-surface px-3 py-2 text-xs leading-relaxed text-bad">
          {error}
        </p>
      )}
    </section>
  );
}
