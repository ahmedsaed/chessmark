"use client";

/**
 * Take-away actions: copy the link, download the PGN — and the sound toggle.
 *
 * Both are on the page for live games too. A share link handed out mid-game keeps working after
 * it ends — it simply becomes the replay — and that continuity is the point of Phase 8.
 *
 * The sound toggle rides this row because it is the one control every game page already has, live
 * or replay, spectating or playing. Words rather than a speaker icon, like its neighbours: the row
 * is a set of labelled mono buttons and an icon would be the one thing in it a reader has to
 * decode.
 */

import { useState } from "react";

import { useSoundEnabled } from "@/hooks/useMoveSounds";

const BUTTON =
  "inline-flex min-h-11 items-center border border-line bg-surface px-3 py-1 font-mono text-data uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink sm:min-h-0 sm:px-2 sm:text-meta";

export function GameActions({ pgnHref }: { pgnHref: string }) {
  const [copied, setCopied] = useState(false);
  const [sound, setSound] = useSoundEnabled();

  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission). The URL is in the
      // address bar either way, so this is not worth an error state.
    }
  }

  return (
    <span className="flex items-center gap-2">
      {/* `aria-pressed` makes it a toggle to a screen reader, and the label states the current
          state rather than the action — "sound on" pressed reads as what it is. */}
      <button
        type="button"
        aria-pressed={sound}
        onClick={() => setSound(!sound)}
        className={BUTTON}
      >
        {sound ? "sound on" : "sound off"}
      </button>
      <button
        type="button"
        onClick={copy}
        className={BUTTON}
      >
        {copied ? "copied" : "copy link"}
      </button>
      <a
        href={pgnHref}
        download
        className={BUTTON}
      >
        pgn
      </a>
    </span>
  );
}
