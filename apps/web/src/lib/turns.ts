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
    // Whitespace is not yet a block: a model whose first fragment is a newline would otherwise
    // open an empty bordered box in the timeline before it had written anything.
    if (!partial[kind].trim()) continue;
    blocks.push(
      kind === "reasoning"
        ? { kind, seq: key--, text: partial[kind], tokens: 0, durationMs: null }
        : { kind, seq: key--, text: partial[kind] },
    );
  }

  return blocks;
}

/**
 * Whether two renderings of one turn are the same, for the panel's memo.
 *
 * Here rather than beside the component because it is a rule about a turn, and because getting it
 * wrong is invisible: a comparison that reports "unchanged" too eagerly does not fail, it just
 * silently stops updating the screen while the data underneath goes on changing.
 *
 * **Which is what happened.** It compared `blocks.length` alone, and a block still being generated
 * grows a fragment at a time while the list does not — so the first token created the block and
 * every token after it was dropped on the floor. The block appeared with one word in it and froze;
 * a refresh rebuilt from the buffer and showed the lot, which is the tell that the data was right
 * and the render was skipped.
 *
 * `blocks` is append-only apart from the one still being written, so the newest block's size is
 * the only thing that can differ at equal length. That keeps this O(1), which matters: scrubbing a
 * replay re-folds the log and hands back completely new objects on every step.
 */
export function sameTurnContent(a: TurnView, b: TurnView): boolean {
  return (
    a.key === b.key &&
    a.san === b.san &&
    a.live === b.live &&
    a.ply === b.ply &&
    a.colour === b.colour &&
    a.blocks.length === b.blocks.length &&
    lastBlockSize(a) === lastBlockSize(b) &&
    a.said.length === b.said.length
  );
}

/** How long the newest block's text is; 0 for a block that has none. */
function lastBlockSize(turn: TurnView): number {
  const block = turn.blocks.at(-1);
  if (block === undefined) return 0;
  return "text" in block ? block.text.length : 0;
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
        const reason = asString(payload.reason) || "paused by the harness";
        const resumeAfter = asString(payload.resume_after) || null;
        const pausedColour = asString(payload.colour);

        /* The game is paused whichever way the row is drawn: this is what the board says about
           itself, and it is cleared by the resume below. */
        paused = {
          key: `paused-${event.seq}`,
          seq: event.seq,
          kind: "paused",
          text: reason,
          resumeAfter,
          // The seat whose endpoint we are waiting on. A halt names none, and neither does a
          // pause written before this was recorded — both render as they always did. Only a
          // standalone row uses it: inside a turn, the turn's own header already names the seat.
          ...(pausedColour === "white" || pausedColour === "black"
            ? { seat: { colour: pausedColour, model: asString(payload.model) || null } }
            : {}),
        };

        /* **Inside the turn when one is still open** (ADR-0045). A turn interrupted by a provider
           keeps the rounds it completed, so a pause genuinely falls between two of its steps — and
           the step list is the only place that can show which work survived the interruption and
           which followed it.
           `san === null` is what "still open" means: a turn that has played its move is finished,
           and a pause after it belongs to whatever comes next rather than to the turn that has
           already moved. `current` is not cleared on a move, so without this every between-turns
           pause would be filed under the turn above it — which is the bug this whole change is
           about, reintroduced from the other direction. */
        if (current && current.san === null) {
          const last = current.blocks.at(-1);
          if (last?.kind === "paused" && last.text === reason) {
            // A provider that keeps refusing produces pause, resume, pause, resume. One row with
            // the count, carrying the wait the reader is actually in.
            last.count += 1;
            last.resumeAfter = resumeAfter;
          } else {
            current.blocks.push({
              kind: "paused",
              seq: event.seq,
              text: reason,
              resumeAfter,
              count: 1,
            });
          }
          break;
        }

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

        /* **Swallowed when the pause it ends is a step of an open turn.** The steps that follow it
           *are* the resumption — the model carried on and moved — so a row saying so is a second
           telling of something already on the screen, and it cannot even be drawn in the right
           place: the notice is ordered against whole turns, so it landed after the move the resume
           made possible. A resume that ends a pause between turns still gets its row, because
           there is nothing else there to say the game came back. */
        const openPause = current?.san === null && current.blocks.at(-1)?.kind === "paused";
        if (!openPause) {
          notices.push({
            key: `resumed-${event.seq}`,
            seq: event.seq,
            kind: "resumed",
            text: asString(payload.detail) || "resumed",
            resumeAfter: null,
          });
        }
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


/**
 * One row of the stream.
 *
 * A turn carries the waits that led to it — see `buildTimeline`.
 */
export type TimelineEntry =
  | { kind: "turn"; turn: TurnView; waits: StreamNotice[] }
  | { kind: "notice"; notice: StreamNotice };

/**
 * Fold a run of identical pauses into one row.
 *
 * A provider that keeps refusing produces pause, resume, pause, resume, for as long as it keeps
 * refusing. `f129b600` filled its panel with eight of them carrying the same sentence, and a reader
 * had to count rows to learn the only thing the run actually says: it was refused eight times, and
 * here is when it tries next.
 *
 * **Only genuinely identical pauses fold.** The text is the whole reason — model, provider and
 * limit source — so two pauses for different reasons keep their own rows and stay legible as two
 * different problems. A rate limit followed by a halt must never read as one thing that happened
 * nine times.
 *
 * The resumes *inside* a run are absorbed, because "it came back and was refused again" is what the
 * count already says; one that ends a run survives, because then it really did come back and that
 * is the last thing that happened.
 *
 * Takes a plain list rather than the timeline: a run is never interrupted by a turn, because
 * `buildTimeline` has already grouped these, so the rule "a turn breaks the run" is a property of
 * the input instead of a condition in the loop.
 */
export function foldPauses(notices: StreamNotice[]): StreamNotice[] {
  const out: StreamNotice[] = [];

  for (let i = 0; i < notices.length; i++) {
    const first = notices[i];
    if (first.kind !== "paused") {
      out.push(first);
      continue;
    }

    let last = first;
    let count = 1;
    let j = i + 1;
    while (j < notices.length) {
      const next = notices[j];
      const after = notices[j + 1];
      const sameAgain = (n: StreamNotice | undefined) =>
        n !== undefined && n.kind === "paused" && n.text === first.text;

      if (next.kind === "resumed" && sameAgain(after)) {
        last = after!;
        count += 1;
        j += 2;
      } else if (sameAgain(next)) {
        last = next;
        count += 1;
        j += 1;
      } else break;
    }

    // The *last* pause of the run is shown: its `resumeAfter` is the wait a reader is actually in,
    // and the earlier ones have already elapsed.
    out.push(count > 1 ? { ...last, key: first.key, count } : first);
    i = j - 1;
  }

  return out;
}

/**
 * Turns and the waits that led to them, in one list.
 *
 * **A section is one seat\'s waits plus its turn, and the model name heads the whole of it.** That
 * is the thing this used to reconstruct rather than build: notices were spliced in by `seq`, runs
 * were folded in a second pass, and a third walked *backwards* from each turn to work out whether
 * the header above had already named this seat — which is how the header came out twice the first
 * time, because a run ends with a seatless `resumed` and the scan stopped there.
 *
 * A pause is written for the seat that could not play, and the attempt it belongs to is rolled back
 * whole — so there is no turn row for it, and the waits simply belong to the turn that eventually
 * succeeds. Saying so here means the header is rendered once because there is one section, not
 * because a lookback agreed there should be.
 *
 * A wait with no turn after it — the game is still paused — stays a row of its own and carries its
 * own seat marker. So does anything seatless: a halt, a compaction, the ending.
 *
 * **A live turn sorts last.** `liveTurn` gives it `seq: -1` so its key cannot collide with a real
 * turn\'s; that is right for keys and wrong for order, and it once left a pause below the THINKING
 * block while live and above it after a reload. It is by definition the newest thing in the stream.
 */
export function buildTimeline(turns: TurnView[], notices: StreamNotice[]): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  const queue = [...notices].sort((a, b) => a.seq - b.seq);
  let pending: StreamNotice[] = [];

  const flush = () => {
    for (const notice of foldPauses(pending)) out.push({ kind: "notice", notice });
    pending = [];
  };

  for (const turn of turns) {
    while (queue.length > 0 && (turn.seq < 0 || queue[0].seq < turn.seq)) {
      pending.push(queue.shift()!);
    }

    // Whose waits these are. The first seated notice speaks for the run: a halt in the middle of
    // one names nobody, and must not make the run look like somebody else\'s.
    const seated = pending.find((notice) => notice.seat);
    if (seated && seated.seat!.colour === turn.colour) {
      out.push({ kind: "turn", turn, waits: foldPauses(pending) });
      pending = [];
    } else {
      flush();
      out.push({ kind: "turn", turn, waits: [] });
    }
  }

  pending.push(...queue);
  flush();
  return out;
}
