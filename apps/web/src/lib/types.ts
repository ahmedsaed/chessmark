/**
 * Shapes returned by the Chessmark API.
 *
 * Hand-written for now and deliberately narrow — only what the UI reads. Generating these from
 * the OpenAPI schema is Phase 6's follow-up; until then a mismatch shows up as a type error here
 * rather than as undefined at runtime.
 *
 * Money arrives as a **string**, not a number: costs run to eight decimal places and JSON floats
 * would round them at exactly the scale the benchmark cares about.
 */

export type Colour = "white" | "black";

export type GameStatus = "pending" | "running" | "finished" | "aborted";

export type GameResult = "1-0" | "0-1" | "1/2-1/2" | "*";

export interface Player {
  id: string;
  colour: Colour;
  kind: "model" | "human" | "engine";
  display_name: string;
  model: string | null;
  /**
   * What this seat ran under, and what actually served it. "Which model" is not a complete
   * answer: the same id at fp8 and at fp4 is not the same contestant.
   */
  provider_routing: Record<string, unknown>;
  /** The endpoint chosen before the game started. */
  pinned_provider: string | null;
  /** What actually served it. Should be exactly `[pinned_provider]` — more than one means the
   *  pin did not hold and the result measures a blend of endpoints. */
  providers_used: string[];
  quantization: string | null;

  illegal_attempts: number;
  forfeited: boolean;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  total_cost_usd: string;
}

export interface GameSummary {
  id: string;
  status: GameStatus;
  result: GameResult;
  termination: string | null;
  winner_colour: Colour | null;
  ply_count: number;
  is_ranked: boolean;
  trash_talk_enabled: boolean;
  total_cost_usd: string;
  total_tokens: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  players: Player[];
}

/**
 * A game *you* hold a seat in.
 *
 * Two fields the public summary deliberately lacks: `/games` says nothing about who plays what,
 * because publishing that to every spectator to save one request is the wrong trade. These come
 * from `/games/mine`, which answers only for the caller.
 */
export interface MyGameSummary extends GameSummary {
  your_colour: Colour;
  /** Running, and waiting on you. What the "your move" badge reads. */
  your_turn: boolean;
}

export interface GameDetail extends GameSummary {
  start_fen: string;
  current_fen: string;
  termination_detail: string | null;
  prompt_version: string | null;
  tool_schema_version: string | null;
  max_usd: string | null;
  max_illegal_retries: number;
  max_plies: number;
  provider_routing: Record<string, unknown>;
  /** Highest event sequence emitted so far — the stream's starting cursor. */
  event_seq: number;
  moves: string[];
}

export interface ModelInfo {
  id: string;
  openrouter_id: string;
  display_name: string;
  provider: string;
  context_length: number | null;
  supports_reasoning: boolean;
  is_free: boolean;
  prompt_usd_per_token: string;
  completion_usd_per_token: string;
  /** Every precision this model is served at. `contestants` is the useful shape. */
  quantizations: string[];
  /** One entry per precision that can be played, healthiest endpoint first (ADR-0015). */
  contestants: Contestant[];
  endpoint_count: number;
  /** Points at different weights over time, so it can never be ranked. */
  is_floating_alias: boolean;
  /** What one seat against this model costs to start, in credits (ADR-0016). */
  credit_cost: number;
}

/**
 * One precision a model can be played at, and the endpoint that would serve it.
 *
 * A contestant, not a capability: `model@fp4` and `model@fp8` are different entrants and are
 * ranked apart (ADR-0015).
 */
export interface Contestant {
  quantization: string;
  provider: string;
  uptime_1d: number | null;
  /** How many endpoints serve this precision. One means an outage takes the contestant with it. */
  endpoint_count: number;
}

/** What a model has actually done, over every game — not only the ratable ones (Phase 20). */
export interface ModelStats {
  games: number;
  /** Higher than `games` only when a model played itself: it won one and lost one. */
  seats: number;
  wins: number;
  draws: number;
  losses: number;
  forfeits: number;
  illegal_attempts: number;
  moves_played: number;
  illegal_per_move: number;
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  /** Null when nothing has been sent — not measured, rather than measured at zero. */
  cache_rate: number | null;
  total_cost_usd: string;
  cost_per_game: string;
  mean_latency_ms: number | null;
}

export interface ModelDetail extends ModelInfo {
  stats: ModelStats;
  /** Empty when no contestant of this model is ranked — which is a fact worth showing. */
  ratings: LeaderboardRow[];
}

export type EventType =
  | "game_started"
  | "turn_started"
  | "thinking"
  | "output"
  | "tool_called"
  | "illegal_attempt"
  | "move_made"
  | "message_sent"
  | "draw_offered"
  | "game_ended";

export interface GameEvent {
  seq: number;
  type: EventType;
  payload: Record<string, unknown>;
  created_at?: string;
}

/**
 * One tool call as the conversation shows it.
 *
 * `args` and `result` are provider- and tool-shaped, so they stay `unknown` — the panel renders
 * them generically rather than pretending to know the schema of seven different tools.
 */
export interface ToolCallView {
  name: string;
  ok: boolean;
  args: Record<string, unknown>;
  result: Record<string, unknown> | null;
}

/** One agent turn, assembled from the event stream. */
export interface TurnView {
  key: string;
  ply: number;
  colour: Colour;
  playerId: string;
  model: string;
  /** A person's turn, not a model's. There is no provider call behind it and no name in `model`. */
  human: boolean;
  /** What the model was thinking. DeepSeek fills this; Gemini never does. */
  reasoning: string[];
  /** What the model said outside a tool call. Gemini fills this; DeepSeek never does. */
  output: string[];
  tools: ToolCallView[];
  illegal: { move: string; detail: string; attempt: number }[];
  said: string[];
  san: string | null;
  /** True until the move lands — the live turn stays expanded (ADR-0013). */
  live: boolean;
}

/** A turn as `/games/{id}/turns` returns it. Replay needs the id to fetch raw payloads. */
export interface TurnSummary {
  id: number;
  player_id: string;
  ply_number: number | null;
  status: string;
  illegal_attempts: number;
  tool_call_count: number;
  llm_call_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  cost_usd: string;
  latency_ms: number | null;
  error: string | null;
  reasoning_available: boolean;
}

/**
 * One LLM call exactly as it crossed the wire (LOG-01).
 *
 * `request` and `response` are unshaped provider payloads, so they are typed as unknown rather
 * than modelled — the moment this file claims to know their structure, it is lying about a
 * provider somewhere.
 */
export interface RawCall {
  id: number;
  sequence: number;
  model_slug: string;
  provider: string | null;
  request: Record<string, unknown>;
  response: Record<string, unknown> | null;
  reasoning_text: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  cost_usd: string;
  latency_ms: number | null;
  finish_reason: string | null;
  error: string | null;
  created_at: string;
}

/** `GET /me` — who the caller is and what today's quota has left. */
export interface Me {
  id: string;
  email: string | null;
  display_name: string | null;
  is_admin: boolean;
  /** Credits held. Granted by an administrator, spent to start a game (ADR-0016). */
  credit_balance: number;
  games_started_today: number;
  usd_spent_today: string;
}


/** One contestant's standing (BENCH-02). */
export interface LeaderboardRow {
  model_id: string;
  model_slug: string;
  /** Half the contestant's identity — `model@fp4` and `model@fp8` are different entrants. */
  quantization: string;
  display_name: string;

  rating: number;
  /**
   * How sure we are. Printed next to the rating on purpose: without it a reader compares three
   * games against three hundred as though the numbers meant the same thing.
   */
  rating_deviation: number;
  volatility: number;

  games: number;
  wins: number;
  draws: number;
  losses: number;

  illegal_attempts: number;
  moves_played: number;
  /** The benchmark's headline number. */
  illegal_per_move: number;

  forfeits: number;
  mean_cost_usd: string;
  mean_latency_ms: number;
}

/** A finished game that did not count, and why (BENCH-10). */
export interface ExcludedGame {
  game_id: string;
  reason: string;
}

export interface Leaderboard {
  rows: LeaderboardRow[];
  games_counted: number;
  excluded: ExcludedGame[];
  prompt_version: string | null;
  periods: number;
}
