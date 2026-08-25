/**
 * Server-side API client.
 *
 * In Next.js 16 `fetch` is **not** cached by default, so nothing here needs `no-store` — a live
 * game page reads fresh data on every request without asking.
 */

import type {
  GameDetail,
  GameEvent,
  GameResult,
  GameStatus,
  GameSummary,
  ModelDetail,
  ModelInfo,
  Leaderboard,
  MyGameSummary,
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

/**
 * Returns null rather than throwing, so a page can render a proper 404.
 *
 * 422 counts as absent alongside 404. FastAPI rejects a path param that is not a UUID during
 * validation, so `/games/does-not-exist` answers 422 while `/games/<unused-uuid>` answers 404 —
 * a distinction that matters to the API and not at all to a reader following a broken link.
 * Mapping only 404 turned a mistyped URL into a 500.
 */
const ABSENT = new Set([404, 422]);

async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path);
  } catch (error) {
    if (error instanceof ApiError && ABSENT.has(error.status)) return null;
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

/** One model with its aggregates, or null for a slug nothing answers to. */
export function getModel(slug: string): Promise<ModelDetail | null> {
  return getOrNull<ModelDetail>(`/models/${slug}`);
}

/** Every game a model has played, either seat (Phase 20). */
export function listGamesByModel(slug: string, limit = 50): Promise<GameSummary[]> {
  const query = new URLSearchParams({ model: slug, limit: String(limit) });
  return getOrEmpty<GameSummary>(`/games?${query}`);
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

// ---------------------------------------------------------------------- human play

/**
 * Endpoints a person acts through.
 *
 * These all carry a Clerk token, so unlike everything above they are called from the client
 * rather than during server rendering — the token belongs to the browser session and never
 * reaches a server component.
 */

async function post<T>(path: string, token: string | null, body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, describe(payload) ?? `POST ${path} failed`);
  }
  return payload as T;
}

/**
 * A readable message out of FastAPI's `detail`, which is a string for most errors and an object
 * for a refused move — the shape carrying the legal move list (ADR-0002).
 */
function describe(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const inner = (detail as { detail?: unknown }).detail;
    if (typeof inner === "string") return inner;
  }
  return null;
}

export interface HumanActionResult {
  ply: number;
  status: GameStatus;
  result: GameResult;
  termination: string | null;
  detail: string;
  game_over: boolean;
}

export function createHumanGame(
  token: string | null,
  body: {
    model: string;
    colour: "white" | "black";
    model_quantization?: string | null;
    trash_talk_enabled?: boolean;
  },
): Promise<{ id: string }> {
  return post<{ id: string }>("/games/human", token, body);
}

/**
 * The games you hold a seat in (HUMAN-03).
 *
 * Read from the browser rather than during server rendering, like everything else here: the
 * answer depends on who is asking, and the token belongs to the browser session.
 */
export async function listMyGames(token: string | null): Promise<MyGameSummary[]> {
  const response = await fetch(`${API_URL}/games/mine`, {
    headers: {
      accept: "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status, `Could not load your games (HTTP ${response.status}).`);
  }
  return (await response.json()) as MyGameSummary[];
}

export function sendMove(
  id: string,
  token: string | null,
  move: string,
  expectedPly: number,
): Promise<HumanActionResult> {
  return post<HumanActionResult>(`/games/${id}/moves`, token, {
    move,
    expected_ply: expectedPly,
  });
}

export function resignGame(id: string, token: string | null): Promise<HumanActionResult> {
  return post<HumanActionResult>(`/games/${id}/resign`, token);
}

export function offerDraw(id: string, token: string | null): Promise<HumanActionResult> {
  return post<HumanActionResult>(`/games/${id}/draw`, token);
}

export function respondToDraw(
  id: string,
  token: string | null,
  accept: boolean,
): Promise<HumanActionResult> {
  return post<HumanActionResult>(`/games/${id}/draw/respond`, token, { accept });
}

export function sayToModel(
  id: string,
  token: string | null,
  message: string,
): Promise<HumanActionResult> {
  return post<HumanActionResult>(`/games/${id}/say`, token, { message });
}
