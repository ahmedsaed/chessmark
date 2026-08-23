"use client";

/**
 * Keep the game's own record fresh while it plays.
 *
 * The board and the conversation are rebuilt from the event stream, so they update themselves. The
 * *stats* are not in the stream: tokens, cost, cache rate, illegal attempts and the game's status
 * live on `games` and `players` rows, and the server-rendered prop is a snapshot from page load.
 * Left alone it never changes, so a game could run for eighty plies while the rail insisted it had
 * spent nothing.
 *
 * Refetched **per ply, not on a timer**. A move landing is exactly the moment those numbers change,
 * so polling would be a worse approximation of the same thing — and one request per ply on a game
 * that takes seconds per move is nothing.
 */

import { useEffect, useState } from "react";

import type { GameDetail } from "@/lib/types";

export function useGameDetail({
  initial,
  apiUrl,
  plyCount,
  ended,
}: {
  initial: GameDetail;
  apiUrl: string;
  /** Moves seen so far. Changing it means the stats behind them have changed too. */
  plyCount: number;
  /** True once the stream reports an ending — the last refetch, and the one that matters most. */
  ended: boolean;
}) {
  const [detail, setDetail] = useState<GameDetail>(initial);

  useEffect(() => {
    // Nothing has happened since the server rendered it.
    if (plyCount === 0 && !ended) return;

    const controller = new AbortController();

    (async () => {
      try {
        const response = await fetch(`${apiUrl}/games/${initial.id}`, {
          signal: controller.signal,
          headers: { accept: "application/json" },
        });
        if (response.ok) setDetail((await response.json()) as GameDetail);
      } catch {
        // A stale rail is a much smaller problem than an error banner over a live game, and the
        // next ply will try again.
      }
    })();

    return () => controller.abort();
  }, [initial.id, apiUrl, plyCount, ended]);

  return detail;
}
