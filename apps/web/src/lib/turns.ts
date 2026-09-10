/**
 * Assemble the event stream into turns.
 *
 * The conversation panel is a messaging-app timeline where the **move is the date separator**
 * (ADR-0013): everything a model did between two dividers is one coherent thought. That grouping
 * has to be derived here, because the event log is flat.
 */

import type {
  Colour,
  GameEvent,
  LiveFrame,
  StreamNotice,
  TurnBlock,
  TurnView,
} from "@/lib/types";

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

/**
 * What a compaction pass actually did, in one line.
 *
 * Every one of these numbers was already being written to the event and none of it was on screen,
 * which is how a game "compacted" five times — folding three messages of forty-four each time —
 * without anybody noticing it had never made room (ADR-0021).
 *
 * **Characters, said as characters.** The backend refuses to publish a token estimate of part of a
 * transcript, so the size it can measure exactly is what it reports; `occupied_tokens` is the one
 * token figure, and it is the provider's own count of the prompt before the pass.
 */
/**
 * How an ending reads in the timeline.
 *
 * The termination is the headline and the detail is the reason, because the reason is the whole
 * point: a game that says only "abandoned" is indistinguishable from one that is broken, and every
 * abandonment already records why — a rate-limited provider, a window it could not fit inside, a
 * patience window spent. The detail was in the payload the entire time and never had anywhere to
 * appear.
 */
export function endedText(ended: {
  result: string;
  termination: string;
  detail: string;
}): string {
  const label = ended.termination || "ended";
  const head = ended.result && ended.result !== "*" ? `${label} · ${ended.result}` : label;
  return ended.detail ? `${head} — ${ended.detail}` : head;
}

export function compactionText(payload: Record<string, unknown>): string {
  const folded = asNumber(payload.folded);
  const trimmed = asNumber(payload.trimmed);
  const clamped = asNumber(payload.clamped);
  const before = asNumber(payload.characters_before);
  const after = asNumber(payload.characters_after);

  const did: string[] = [];
  if (folded > 0) did.push(`${folded} messages summarised`);
  if (trimmed > 0) did.push(`${trimmed} stale tool results dropped`);
  /* **Named, and named separately.** Clamping is the one pass that shortens something the *model*
     wrote rather than dropping something a tool returned, and a pass that only clamped used to
     render as the bare fallback below — "history compacted" — which is exactly the reading a
     person should not be left with when a reply has had its middle removed. The raw payload still
     holds every character (LOG-07); this is the pointer that says to go and look. */
  if (clamped > 0) {
    did.push(`${clamped} long ${clamped === 1 ? "reply" : "replies"} shortened`);
  }
  if (did.length === 0) did.push("history compacted");

  const parts = [did.join(", ")];
  if (asNumber(payload.kept) > 0) parts.push(`${asNumber(payload.kept)} messages kept`);
  if (before > 0 && after > 0 && after < before) {
    parts.push(`${percent(1 - after / before)} smaller`);
  }
  const occupied = asNumber(payload.occupied_tokens);
  const context = asNumber(payload.context_tokens);
  if (occupied > 0 && context > 0) {
    parts.push(`was ${compact(occupied)} of ${compact(context)} tokens`);
  }
  return parts.join(" · ");
}

function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** 261751 → "262k". A reader comparing a prompt to a window does not want six digits of either. */
function compact(tokens: number): string {
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}k` : String(tokens);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * One event per `seq`, in ascending order.
 *
 * The page seeds the panel with a history fetch and then subscribes to the stream from a cursor,
 * and **the two can overlap**: the game record and the event list are two separate requests, so an
 * event appended between them is both in the history and replayed by the stream. That really
 * happens — a live game with a worker appending turn events produced two `turn_started` rows at
 * the same `seq`, which React reported as a duplicate key and which would have counted the same
 * `move_made` twice in the move list.
 *
 * `seq` is gap-free and unique per game (ADR-0008), so it is the identity. Deduping here rather
 * than at the seam makes the guarantee `LiveGame` already claims — that a duplicated or
 * out-of-order event cannot desync the view — true of the conversation and the move list, not just
 * of the board.
 */
function dedupe(events: GameEvent[]): GameEvent[] {
  const bySeq = new Map<number, GameEvent>();
  for (const event of events) {
    if (!bySeq.has(event.seq)) bySeq.set(event.seq, event);
  }
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

export interface StreamState {
  turns: TurnView[];
  moves: string[];
  ended: { result: string; termination: string; detail: string } | null;
  /** Pauses and resumes, in `seq` order. The panel interleaves them with the turns. */
  notices: StreamNotice[];
  /** The pause the game is currently sitting in, if it has not resumed. */
  paused: StreamNotice | null;
}

/**
 * Fold events into turns and the move list.
 *
 * Pure and total: it takes every event seen so far and rebuilds the view from scratch. That costs
 * a little work per render and buys a guarantee worth far more — a reconnect that replays events
 * cannot leave the panel in a state that incremental patching would have produced.
 */
/**
 * Live frames as blocks the open turn can draw (ADR-0035).
 *
 * A turn is one transaction, so `foldEvents` cannot see a round until every round has finished and
 * the whole turn commits. These arrive as each round lands, and are appended to whichever turn is
 * still open — they are what a spectator reads during the ten minutes ply 8 of `e601f9af` spent
 * generating.
 *
 * **Never the record.** The committed events supersede them, the client clears them at the next
 * `turn_started`, and nothing here is stored. Given a frame and the event that later carries the
 * same content, this must produce the same block, or a step would visibly change as it settled.
 */
/**
 * The turn a run of live frames belongs to, or `null` if it has not announced itself.
 *
 * A provisional turn: it has no `seq`, no committed events and no move, and it exists only until
 * the real one arrives. Drawn exactly like a real open turn, because to a reader it *is* the open
 * turn — the only difference is that the record has not caught up.
 */
export function liveTurn(frames: LiveFrame[]): TurnView | null {
  const started = [...frames].reverse().find((frame) => frame.frame === "turn");
  if (started === undefined) return null;

  const blocks = liveBlocks(frames);
  return {
    // Negative, so it can never collide with a real turn's key or sort after one.
    key: `live-${started.ply}`,
    seq: -1,
    ply: started.ply,
    colour: started.colour,
    playerId: started.player_id,
    model: started.model,
    human: false,
    blocks,
    reasoning: blocks.filter((b) => b.kind === "reasoning").map((b) => b.text),
    withheldReasoning: 0,
    output: blocks.filter((b) => b.kind === "output").map((b) => b.text),
    tools: blocks.flatMap((b) => (b.kind === "tool" ? [b.call] : [])),
    illegal: blocks.flatMap((b) => (b.kind === "illegal" ? [b] : [])),
    said: blocks.filter((b) => b.kind === "said").map((b) => b.text),
    san: null,
    live: true,
  };
}

export function liveBlocks(frames: LiveFrame[]): TurnBlock[] {
  const blocks: TurnBlock[] = [];
  /* Fragments of a block still being generated, held apart from the finished ones. A `block`
     frame for the same register replaces them: it is the whole thing, and the fragments were only
     ever a preview of it. */
  const partial: { reasoning: string; output: string } = { reasoning: "", output: "" };

  /* Negative and descending, so a provisional block can never collide with a committed event's
     `seq` — which is what React keys on, and what would otherwise reuse a real block's DOM for a
     provisional one. */
  let key = -1;

  for (const frame of frames) {
    if (frame.frame === "turn") continue;
    if (frame.frame === "token") {
      partial[frame.kind] += frame.text;
      continue;
    }

    if (frame.kind === "reasoning" || frame.kind === "output") partial[frame.kind] = "";

    switch (frame.kind) {
      case "reasoning":
        blocks.push({
          kind: "reasoning",
          seq: key--,
          text: frame.text ?? "",
          tokens: frame.tokens ?? 0,
          durationMs: typeof frame.duration_ms === "number" ? frame.duration_ms : null,
        });
        break;
      case "output":
        blocks.push({ kind: "output", seq: key--, text: frame.text ?? "" });
        break;
      case "said":
        blocks.push({ kind: "said", seq: key--, text: frame.text ?? "" });
        break;
      case "illegal":
        blocks.push({
          kind: "illegal",
          seq: key--,
          move: String((frame.args ?? {}).move ?? ""),
          detail: String((frame.result ?? {}).detail ?? ""),
          attempt: frame.attempt ?? 0,
        });
        break;
      case "tool":
        blocks.push({
          kind: "tool",
          seq: key--,
          call: {
            name: frame.tool ?? "",
            ok: frame.ok !== false,
            args: frame.args ?? {},
            result: frame.result ?? null,
          },
        });
        break;
    }
  }

  /* The block being generated right now, last and unfinished. `tokens` is zero and the duration
     null because neither is known until the round returns — and a label reading "reasoned for 0s"
     while the model is still reasoning would be worse than no label. */
  for (const kind of ["reasoning", "output"] as const) {
    if (!partial[kind]) continue;
    blocks.push(
      kind === "reasoning"
        ? { kind, seq: key--, text: partial[kind], tokens: 0, durationMs: null }
        : { kind, seq: key--, text: partial[kind] },
    );
  }

  return blocks;
}

export function foldEvents(events: GameEvent[], initialMoves: string[]): StreamState {
  const turns: TurnView[] = [];
  const moves = [...initialMoves];
  const notices: StreamNotice[] = [];
  let ended: StreamState["ended"] = null;
  let paused: StreamNotice | null = null;
  let current: TurnView | null = null;

  /**
   * Open a turn for an event that arrives without one.
   *
   * **A person's actions have no `turn_started`.** The worker emits one before a model thinks;
   * `orchestration/human.py` emits only the action itself, because a human turn has no provider
   * call to bracket. So a human's move landed in the move list with no turn to belong to, and the
   * conversation showed the model talking to nobody — every human ply simply missing from the
   * timeline it was half of.
   *
   * Synthesising it here rather than appending a `turn_started` server-side keeps the event log
   * describing what actually happened: nothing started a turn, a person just moved.
   */
  const openTurn = (event: GameEvent, payload: Record<string, unknown>): TurnView => {
    const turn: TurnView = {
      key: `turn-${event.seq}`,
      seq: event.seq,
      ply: asNumber(payload.ply),
      colour: (asString(payload.colour) || "white") as Colour,
      playerId: asString(payload.player_id),
      model: asString(payload.model),
      human: payload.human === true,
      blocks: [],
      reasoning: [],
      withheldReasoning: 0,
      output: [],
      tools: [],
      illegal: [],
      said: [],
      san: null,
      live: false,
    };
    turns.push(turn);
    return turn;
  };

  /** The turn an event belongs to, opening one if the last is closed or absent. */
  const turnFor = (event: GameEvent, payload: Record<string, unknown>): TurnView => {
    if (current && current.san === null) return current;
    current = openTurn(event, payload);
    return current;
  };

  for (const event of dedupe(events)) {
    const payload = event.payload ?? {};

    switch (event.type) {
      case "turn_started": {
        current = {
          key: `turn-${event.seq}`,
          seq: event.seq,
          ply: asNumber(payload.ply),
          colour: (asString(payload.colour) || "white") as Colour,
          playerId: asString(payload.player_id),
          model: asString(payload.model),
          human: payload.human === true,
          blocks: [],
          reasoning: [],
          withheldReasoning: 0,
          output: [],
          tools: [],
          illegal: [],
          said: [],
          san: null,
          live: true,
        };
        turns.push(current);
        break;
      }

      case "thinking": {
        /* The text is withheld from a game its reader is *playing* (invariant 8) — `api/redaction`
           strips it on the way out and leaves the token count. So an absent `reasoning` with a
           count is not "the model said nothing", it is "you may not read this yet", and the two
           looked identical on the page: a turn showing only its tool calls with no hint that
           anything had been held back. */
        const text = asString(payload.reasoning);
        if (!current) break;
        if (text) {
          current.reasoning.push(text);
          current.blocks.push({
            kind: "reasoning",
            seq: event.seq,
            text,
            tokens: asNumber(payload.tokens),
            /* Absent on every event written before the round's latency was carried on the
               event, which is most of the archive. Null rather than zero: "not recorded" and
               "took no time" must not render the same way. */
            durationMs: typeof payload.duration_ms === "number" ? payload.duration_ms : null,
          });
        } else current.withheldReasoning += asNumber(payload.tokens);
        break;
      }

      case "output": {
        // Prose the model wrote outside a tool call. Kept apart from `reasoning` because
        // providers split the two differently and a reader wants to know which they are seeing.
        const text = asString(payload.content);
        if (current && text.trim()) {
          current.output.push(text);
          current.blocks.push({ kind: "output", seq: event.seq, text });
        }
        break;
      }

      case "tool_called": {
        if (current) {
          const call = {
            name: asString(payload.tool),
            ok: payload.ok !== false,
            args: asRecord(payload.args),
            result: payload.result === undefined ? null : asRecord(payload.result),
          };
          current.tools.push(call);
          current.blocks.push({ kind: "tool", seq: event.seq, call });
        }
        break;
      }

      case "illegal_attempt": {
        if (current) {
          const attempt = {
            move: asString(payload.move),
            detail: asString(payload.detail),
            attempt: asNumber(payload.attempt),
          };
          current.illegal.push(attempt);
          current.blocks.push({ kind: "illegal", seq: event.seq, ...attempt });
        }
        break;
      }

      case "message_sent": {
        /* `content` is what a model's `say` writes and what this always read. A person's `say`
           wrote `message` instead, so nothing a human typed ever reached the panel — stored and
           delivered to the model, invisible on the page. The backend writes `content` now; this
           reads both, because the event log is append-only and the old rows are still there. */
        const text = asString(payload.content) || asString(payload.message);
        if (text) {
          const turn = turnFor(event, payload);
          turn.said.push(text);
          turn.blocks.push({ kind: "said", seq: event.seq, text });
        }
        break;
      }

      case "move_made": {
        const san = asString(payload.san);
        if (san) moves.push(san);
        const turn = turnFor(event, payload);
        turn.san = san;
        turn.live = false; // the move closes the turn, so it folds
        break;
      }

      /* The model summarised its own earlier turns to stay inside its context window. Shown in
         the stream because it changes what the model can see from here on: a reader wondering why
         it forgot a plan it announced at move 12 deserves to find the answer in the timeline.
         It closes the live turn's fold no more than a tool call does — compaction happens *inside*
         a turn, before the model answers, so the turn stays open. */
      case "compacted": {
        notices.push({
          key: `compacted-${event.seq}`,
          seq: event.seq,
          kind: "compacted",
          text: compactionText(payload),
          resumeAfter: null,
        });
        break;
      }

      /* The harness stopped the game and means to continue it — a provider rate limit. Shown
         rather than swallowed, because the alternative is a board that stops moving with nothing
         on the page to say why, which is what it did. */
      case "game_paused": {
        if (current) current.live = false;
        paused = {
          key: `paused-${event.seq}`,
          seq: event.seq,
          kind: "paused",
          text: asString(payload.reason) || "paused by the harness",
          resumeAfter: asString(payload.resume_after) || null,
        };
        notices.push(paused);
        break;
      }

      case "game_resumed": {
        /* Clears the pause **and the ending**. The log is append-only (ADR-0008), so a game that
           was abandoned and then reopened still carries its `game_ended` — and this cleared only
           the pause, so the page went on showing "abandoned" over a game that was playing. It
           survived a refresh, because the stale ending was in the log rather than in a cache.
           A game that ends again appends another `game_ended` after this, which sets it back. */
        paused = null;
        ended = null;
        notices.push({
          key: `resumed-${event.seq}`,
          seq: event.seq,
          kind: "resumed",
          text: asString(payload.detail) || "resumed",
          resumeAfter: null,
        });
        break;
      }

      case "game_ended": {
        if (current) current.live = false;
        ended = {
          result: asString(payload.result),
          termination: asString(payload.termination),
          detail: asString(payload.detail),
        };
        /* **The ending is a notice like every other interruption.** It was the one lifecycle event
           that set state and pushed nothing, so a stream ran pause → resume → pause → resume and
           simply stopped — and on a game that had been reopened and then abandoned again, the last
           thing a reader saw was "resumed", with the abandonment two rows later in the log and
           nowhere on the page. The header carries the ending too, but only at the final ply; the
           timeline is where a reader looks for *when*, and it has to say so itself. */
        notices.push({
          key: `ended-${event.seq}`,
          seq: event.seq,
          kind: "ended",
          text: endedText(ended),
          resumeAfter: null,
        });
        break;
      }

      default:
        break;
    }
  }

  // A game that reached a result is not paused, whatever order the events arrived in.
  return { turns, moves, ended, notices, paused: ended ? null : paused };
}
