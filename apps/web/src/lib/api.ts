/**
 * Server-side API client.
 *
 * In Next.js 16 `fetch` is **not** cached by default, so nothing here needs `no-store` — a live
 * game page reads fresh data on every request without asking.
 */

import type {
  GameDetail,
  GameEvent,
  GameSummary,
  ModelInfo,
  Leaderboard,
  TurnSummary,
} from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { accept: "application/json" },
  });

  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Returns null rather than throwing, so a page can render a proper 404. */
async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * Never throws. The lobby should still render if the API is briefly unreachable — an empty
 * section is a better failure than a blank page.
 */
async function getOrEmpty<T>(path: string): Promise<T[]> {
  try {
    return await get<T[]>(path);
  } catch {
    return [];
  }
}

export function listGames(status?: string, limit = 20): Promise<GameSummary[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (status) query.set("status", status);
  return getOrEmpty<GameSummary>(`/games?${query}`);
}

export function getGame(id: string): Promise<GameDetail | null> {
  return getOrNull<GameDetail>(`/games/${id}`);
}

export function listModels(freeOnly = false): Promise<ModelInfo[]> {
  return getOrEmpty<ModelInfo>(`/models?free_only=${freeOnly}`);
}

/**
 * The whole event log for a game.
 *
 * The conversation panel is built from events, so a spectator arriving mid-game needs the history
 * or the panel sits empty until the next turn. Live and replay read the same rows (ADR-0008),
 * which is exactly what makes seeding it this way safe.
 */
export function listEvents(id: string, limit = 5000): Promise<GameEvent[]> {
  return getOrEmpty<GameEvent>(`/games/${id}/events?limit=${limit}`);
}

/**
 * Every turn of a game, with its per-turn token and cost totals.
 *
 * Replay needs this alongside the event log: events say what happened, turns say which database
 * row it happened in — which is what makes the raw payloads reachable (LOG-07).
 */
export function listTurns(id: string): Promise<TurnSummary[]> {
  return getOrEmpty<TurnSummary>(`/games/${id}/turns`);
}

/**
 * The leaderboard, with its exclusions.
 *
 * Never throws: an empty ranking is the honest state before any ranked game has been played, and
 * a blank page would be a worse way to say so.
 */
export async function getLeaderboard(): Promise<Leaderboard> {
  try {
    return await get<Leaderboard>("/leaderboard");
  } catch {
    return {
      rows: [],
      games_counted: 0,
      excluded: [],
      prompt_version: null,
      periods: 0,
    };
  }
}

/**
 * The games behind one leaderboard row (BENCH-02).
 *
 * Only the *ratable* ones — exactly what moved the rating, not everything the model has played.
 */
export function getContestantGames(
  modelSlug: string,
  quantization?: string,
): Promise<GameSummary[]> {
  const query = quantization ? `?quantization=${encodeURIComponent(quantization)}` : "";
  return getOrEmpty<GameSummary>(`/leaderboard/${modelSlug}/games${query}`);
}

/** The PGN download URL. Handed to the browser as a link so the file arrives with its filename. */
export function pgnUrl(id: string): string {
  return `${API_URL}/games/${id}/pgn`;
}

export const apiUrl = API_URL;
