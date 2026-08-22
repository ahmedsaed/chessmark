"use client";

/**
 * The replay transport: a ply slider, step controls, and autoplay.
 *
 * Keyboard navigation is bound at the **document**, not on the slider, because the thing people
 * actually do is click the board and then press ←/→. Requiring focus on a thin range input first
 * makes the shortcuts feel broken. Typing in a field still wins — the handler stands down when
 * the event came from an input, a textarea, or anything contenteditable.
 */

import { useCallback, useEffect, useRef } from "react";

const SPEEDS = [
  { label: "1×", ms: 1400 },
  { label: "2×", ms: 700 },
  { label: "4×", ms: 350 },
];

interface Props {
  ply: number;
  total: number;
  playing: boolean;
  speed: number;
  onSeek: (ply: number) => void;
  onPlayingChange: (playing: boolean) => void;
  onSpeedChange: (index: number) => void;
  /** False while a dialog owns the keyboard, so ←/→ scroll the transcript instead of scrubbing. */
  keysEnabled?: boolean;
}

export function Scrubber({
  ply,
  total,
  playing,
  speed,
  onSeek,
  onPlayingChange,
  onSpeedChange,
  keysEnabled = true,
}: Props) {
  const clamp = useCallback(
    (value: number) => Math.max(0, Math.min(total, value)),
    [total],
  );

  const step = useCallback(
    (delta: number) => {
      onPlayingChange(false); // any manual step takes the wheel from autoplay
      onSeek(clamp(ply + delta));
    },
    [clamp, onPlayingChange, onSeek, ply],
  );

  // Autoplay. Stops itself at the end rather than looping — a replay that silently restarts
  // reads as a bug the first time you look away and back.
  const seekRef = useRef(onSeek);
  const stopRef = useRef(onPlayingChange);

  // Kept current in an effect rather than during render, and declared *before* the timer effect
  // so the timer always fires against this render's callbacks.
  useEffect(() => {
    seekRef.current = onSeek;
    stopRef.current = onPlayingChange;
  });

  useEffect(() => {
    if (!playing) return;
    if (ply >= total) {
      stopRef.current(false);
      return;
    }

    const timer = setTimeout(() => seekRef.current(ply + 1), SPEEDS[speed].ms);
    return () => clearTimeout(timer);
  }, [playing, ply, total, speed]);

  useEffect(() => {
    if (!keysEnabled) return;

    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      switch (event.key) {
        case "ArrowLeft":
          event.preventDefault();
          step(-1);
          break;
        case "ArrowRight":
          event.preventDefault();
          step(1);
          break;
        case "Home":
          event.preventDefault();
          step(-Infinity);
          break;
        case "End":
          event.preventDefault();
          step(Infinity);
          break;
        case " ":
          event.preventDefault();
          onPlayingChange(!playing);
          break;
        default:
          break;
      }
    }

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [step, playing, onPlayingChange, keysEnabled]);

  const moveNumber = ply === 0 ? 0 : Math.ceil(ply / 2);

  return (
    <div className="flex flex-col gap-2 border border-line bg-surface-2 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <Control label="Start" hint="Home" onClick={() => step(-Infinity)} disabled={ply === 0}>
          ⏮
        </Control>
        <Control label="Previous ply" hint="←" onClick={() => step(-1)} disabled={ply === 0}>
          ◀
        </Control>

        <button
          type="button"
          onClick={() => onPlayingChange(!playing)}
          disabled={total === 0}
          aria-label={playing ? "Pause" : "Play"}
          title={`${playing ? "Pause" : "Play"} (space)`}
          className="flex h-7 w-9 flex-none items-center justify-center border border-accent-deep bg-accent text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40"
        >
          <span aria-hidden className="text-[11px] leading-none">
            {playing ? "❚❚" : "▶"}
          </span>
        </button>

        <Control label="Next ply" hint="→" onClick={() => step(1)} disabled={ply >= total}>
          ▶
        </Control>
        <Control label="End" hint="End" onClick={() => step(Infinity)} disabled={ply >= total}>
          ⏭
        </Control>

        <span className="tabular ml-1 flex-none font-mono text-[11px] text-ink-dim">
          {ply}
          <span className="text-ink-faint">/{total}</span>
        </span>
        <span className="tabular flex-none font-mono text-[10px] text-ink-faint">
          {moveNumber > 0 ? `move ${moveNumber}` : "start"}
        </span>

        <span className="ml-auto flex flex-none items-center gap-1">
          {SPEEDS.map((option, index) => (
            <button
              key={option.label}
              type="button"
              onClick={() => onSpeedChange(index)}
              aria-pressed={speed === index}
              className={`border px-1.5 py-0.5 font-mono text-[9px] transition-colors ${
                speed === index
                  ? "border-accent text-accent"
                  : "border-line text-ink-faint hover:text-ink-dim"
              }`}
            >
              {option.label}
            </button>
          ))}
        </span>
      </div>

      <label className="flex items-center gap-2">
        <span className="sr-only">Ply</span>
        <input
          type="range"
          min={0}
          max={Math.max(total, 1)}
          value={ply}
          onChange={(event) => {
            onPlayingChange(false);
            onSeek(Number(event.target.value));
          }}
          aria-label="Ply"
          aria-valuetext={ply === 0 ? "starting position" : `ply ${ply} of ${total}`}
          className="h-1 w-full flex-1 cursor-pointer appearance-none bg-line accent-[var(--color-accent)]"
        />
      </label>
    </div>
  );
}

function Control({
  children,
  label,
  hint,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  label: string;
  hint: string;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={`${label} (${hint})`}
      className="flex h-7 w-7 flex-none items-center justify-center border border-line bg-surface text-ink-dim transition-colors hover:border-accent-dim hover:text-ink disabled:opacity-30 disabled:hover:border-line"
    >
      <span aria-hidden className="text-[10px] leading-none">
        {children}
      </span>
    </button>
  );
}
