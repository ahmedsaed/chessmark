"use client";

/**
 * Take-away actions: the sound toggle, copy the link, download the PGN, export a GIF.
 *
 * All are on the page for live games too. A share link handed out mid-game keeps working after
 * it ends — it simply becomes the replay — and that continuity is the point of Phase 8.
 *
 * The sound toggle rides this row because it is the one control every game page already has, live
 * or replay, spectating or playing.
 *
 * **Icons, each with a name and a tooltip.** The row used to be four labelled mono words, and a
 * fourth action made it wider than the status row it sits in can spare. The trade an icon makes is
 * that a reader has to decode it, so every one carries an `aria-label` for a screen reader and a
 * `title` for a pointer — and the state a word used to carry ("copied", "sound off") is drawn into
 * the icon and announced in the live region.
 */

import { useState, type ReactNode } from "react";

import { useSoundEnabled } from "@/hooks/useMoveSounds";
import type { GameDetail } from "@/lib/types";

/* Square, 44px on a phone to stay a fingertip's target (WCAG 2.5.5), compact from `sm` up. */
const BUTTON =
  "inline-flex h-11 w-11 items-center justify-center border border-line bg-surface text-ink-faint transition-colors hover:border-accent-dim hover:text-ink disabled:cursor-progress disabled:opacity-60 sm:h-7 sm:w-7";

type Flash = "copied" | "exported" | "failed" | null;

export function GameActions({
  gameId,
  apiUrl,
  pgnHref,
}: {
  gameId: string;
  apiUrl: string;
  pgnHref: string;
}) {
  const [flash, setFlash] = useState<Flash>(null);
  const [exporting, setExporting] = useState(false);
  const [sound, setSound] = useSoundEnabled();

  function show(state: Flash) {
    setFlash(state);
    setTimeout(() => setFlash(null), 1600);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      show("copied");
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission). The URL is in the
      // address bar either way, so this is not worth an error state.
    }
  }

  async function exportGif() {
    setExporting(true);
    try {
      /* The record is read fresh rather than taken from the page. A live game has moved on since
         the page rendered, and the GIF should be the game as it stands when the button is
         pressed — not as it stood when the tab was opened. */
      const response = await fetch(`${apiUrl}/games/${gameId}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`game ${response.status}`);
      const game = (await response.json()) as GameDetail;

      const [{ gameGif }, { gifFilename }] = await Promise.all([
        import("@/lib/gif/export"),
        import("@/lib/gif/frames"),
      ]);
      const blob = await gameGif(game);

      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = gifFilename(game);
      link.click();
      // Revoked on the next task, after the click has handed the URL to the download.
      setTimeout(() => URL.revokeObjectURL(href));
      show("exported");
    } catch (error) {
      console.error("GIF export failed", error);
      show("failed");
    } finally {
      setExporting(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      {/* A toggle: the name stays fixed and `aria-pressed` carries the state, which is how a
          screen reader expects a toggle to work — "Move sounds, pressed". */}
      <button
        type="button"
        aria-label="Move sounds"
        title={sound ? "Sound on" : "Sound off"}
        aria-pressed={sound}
        onClick={() => setSound(!sound)}
        className={BUTTON}
      >
        {sound ? <SpeakerOn /> : <SpeakerOff />}
      </button>
      <button
        type="button"
        aria-label="Copy link"
        title="Copy link"
        onClick={copy}
        className={BUTTON}
      >
        {flash === "copied" ? <Check /> : <LinkIcon />}
      </button>
      <a
        href={pgnHref}
        download
        aria-label="Download PGN"
        title="Download PGN"
        className={BUTTON}
      >
        <FileIcon />
      </a>
      <button
        type="button"
        aria-label="Export GIF"
        title="Export GIF"
        onClick={exportGif}
        disabled={exporting}
        aria-busy={exporting}
        className={BUTTON}
      >
        {flash === "exported" ? <Check /> : flash === "failed" ? <Cross /> : <FilmIcon />}
      </button>
      <span role="status" className="sr-only">
        {flash === "copied"
          ? "Link copied"
          : flash === "exported"
            ? "GIF downloaded"
            : flash === "failed"
              ? "GIF export failed"
              : ""}
      </span>
    </span>
  );
}

/* Drawn here rather than taken from an icon set: six 16px strokes are not worth a dependency, and
   this is how the rest of the site draws its glyphs (`SiteHeader`, `ArchiveRow`). */
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="block"
    >
      {children}
    </svg>
  );
}

function SpeakerOn() {
  return (
    <Icon>
      <path d="M2.5 6h2.5l3.5-3v10l-3.5-3H2.5z" />
      <path d="M11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.5a6.2 6.2 0 0 1 0 9" />
    </Icon>
  );
}

function SpeakerOff() {
  return (
    <Icon>
      <path d="M2.5 6h2.5l3.5-3v10l-3.5-3H2.5z" />
      <path d="M11 6l3.5 4M14.5 6L11 10" />
    </Icon>
  );
}

function LinkIcon() {
  return (
    <Icon>
      <path d="M6.8 9.2a2.6 2.6 0 0 0 3.7 0l2.4-2.4a2.6 2.6 0 0 0-3.7-3.7l-.9.9" />
      <path d="M9.2 6.8a2.6 2.6 0 0 0-3.7 0L3.1 9.2a2.6 2.6 0 0 0 3.7 3.7l.9-.9" />
    </Icon>
  );
}

function FileIcon() {
  return (
    <Icon>
      <path d="M4 1.8h5.2L12 4.6v9.6H4z" />
      <path d="M9 1.8v3h3M6 8h4M6 10.5h4" />
    </Icon>
  );
}

function FilmIcon() {
  return (
    <Icon>
      <rect x="2" y="2.5" width="12" height="11" rx="1" />
      <path d="M5 2.5v11M11 2.5v11M2 5.5h3M2 8h3M2 10.5h3M11 5.5h3M11 8h3M11 10.5h3" />
    </Icon>
  );
}

function Check() {
  return (
    <Icon>
      <path d="M3 8.5l3 3 7-7" />
    </Icon>
  );
}

function Cross() {
  return (
    <Icon>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Icon>
  );
}
