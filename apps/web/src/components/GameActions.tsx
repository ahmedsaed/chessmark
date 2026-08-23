"use client";

/**
 * Take-away actions: copy the link, download the PGN.
 *
 * Both are on the page for live games too. A share link handed out mid-game keeps working after
 * it ends — it simply becomes the replay — and that continuity is the point of Phase 8.
 */

import { useState } from "react";

export function GameActions({ pgnHref }: { pgnHref: string }) {
  const [copied, setCopied] = useState(false);

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
      <button
        type="button"
        onClick={copy}
        className="border border-line bg-surface px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink"
      >
        {copied ? "copied" : "copy link"}
      </button>
      <a
        href={pgnHref}
        download
        className="border border-line bg-surface px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink"
      >
        pgn
      </a>
    </span>
  );
}
