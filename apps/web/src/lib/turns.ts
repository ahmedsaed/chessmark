/**
 * Assemble the event stream into turns.
 *
 * The conversation panel is a messaging-app timeline where the **move is the date separator**
 * (ADR-0013): everything a model did between two dividers is one coherent thought. That grouping
 * has to be derived here, because the event log is flat.
 */

import type {
  Colour,
  DecisionBlock,
  EventType,
  GameEvent,
  LiveFrame,
  StreamNotice,
  TurnBlock,
  TurnView,
  WaitingOn,
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

/**
 * A decision model's answer as a block, from a `decided` event or the `decision` frame that
 * predicted it — the two carry the same fields, so one reader serves both and a frame cannot
 * render differently from the event that settles it (ADR-0035, ADR-0049).
 *
 * **Absent is not zero.** A person playing the game is sent the payload without its
 * probabilities (invariant 8), and `null` is how the panel knows to say they are held back rather
 * than drawing an empty ranking as though the model had weighed nothing.
 */
export function decisionBlock(payload: Record<string, unknown>, seq: number): DecisionBlock {
  const ranked = Array.isArray(payload.probabilities)
    ? payload.probabilities.flatMap((entry): [string, number][] =>
        Array.isArray(entry) && typeof entry[0] === "string" && typeof entry[1] === "number"
          ? [[entry[0], entry[1]]]
          : [],
      )
    : null;
  const answers =
    payload.answers && typeof payload.answers === "object" && !Array.isArray(payload.answers)
      ? Object.fromEntries(
          Object.entries(payload.answers as Record<string, unknown>).filter(
            (entry): entry is [string, number] => typeof entry[1] === "number",
          ),
        )
      : null;
  return {
    kind: "decision",
    seq,
    action: asString(payload.action) || "move",
    choice: asString(payload.choice),
    options: asNumber(payload.options),
    offersDraw: payload.offers_draw === true,
    rankedFirst: typeof payload.ranked_first === "string" ? payload.ranked_first : null,
    probabilities: ranked,
    confidence: typeof payload.confidence === "number" ? payload.confidence : null,
    answers,
    durationMs: typeof payload.duration_ms === "number" ? payload.duration_ms : null,
  };
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
  /* **The last `turn` frame, and only what came after it.** A frame is a prediction, and a new
     `turn` frame supersedes every prediction before it — the server deletes its buffer on one for
     exactly that reason. The client keeps appending to a single list and clears it on a committed
     `turn_started`, which a *resumed* turn never appends (ADR-0045): without this slice the rounds
     of the attempt that was interrupted are drawn again beneath the retry, next to the committed
     record of those same rounds. */
  const started = [...frames].reverse().find((frame) => frame.frame === "turn");
  if (started === undefined) return null;

  const blocks = liveBlocks(frames.slice(frames.lastIndexOf(started) + 1));
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

  /* When the open reasoning run began, from the first fragment that started it. Cleared with the
     text it belongs to, so a second round of reasoning in the same turn times itself rather than
     inheriting the first one's clock. */
  let reasoningSince: number | null = null;

  /* Negative and descending, so a provisional block can never collide with a committed event's
     `seq` — which is what React keys on, and what would otherwise reuse a real block's DOM for a
     provisional one. */
  let key = -1;

  for (const frame of frames) {
    if (frame.frame === "turn") continue;
    if (frame.frame === "token") {
      if (frame.kind === "reasoning" && reasoningSince === null) {
        reasoningSince = frame.receivedAt ?? Date.now();
      }
      partial[frame.kind] += frame.text;
      continue;
    }

    if (frame.kind === "reasoning" || frame.kind === "output") {
      partial[frame.kind] = "";
      // The block frame *is* the finished thing, and it carries the real `duration_ms`. The
      // running count has done its job and must stop, or a closed block would go on ticking.
      if (frame.kind === "reasoning") reasoningSince = null;
    }

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
      case "decision":
        blocks.push(decisionBlock(frame as unknown as Record<string, unknown>, key--));
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
        ? {
            kind,
            seq: key--,
            text: partial[kind],
            tokens: 0,
            durationMs: null,
            startedAt: reasoningSince,
          }
        : { kind, seq: key--, text: partial[kind] },
    );
  }

  return blocks;
}

/**
 * Whether a committed event supersedes the frames that predicted it.
 *
 * Frames are a prediction of events that have not committed yet, so the moment the real ones
 * arrive the prediction has to go or the panel draws each step twice. Two events say that:
 *
 * - `turn_started` is the first thing a turn appends, so a turn's own events never clear its own
 *   frames and the next turn's arrival clears the last one's. `move_made` is the same boundary
 *   reached from the other side.
 * - `game_paused` is the boundary a **resumed** turn has instead. An interrupted turn commits the
 *   rounds it completed and appends the pause ([ADR-0045]); the retry appends no second
 *   `turn_started`, so without this the frames predicting those committed rounds stay on screen
 *   beside the record of them.
 *
 * Here rather than in the hook because it is half of one rule — `withLiveTurn` is the other half,
 * and a test can only hold the two to the same story if they are in the same place.
 */
export function supersedesFrames(type: EventType): boolean {
  return type === "turn_started" || type === "move_made" || type === "game_paused";
}

/**
 * The committed turns with the turn in flight drawn into them (ADR-0035).
 *
 * Three cases, and the middle one is the reason this is a function rather than a line:
 *
 * - **No committed row for the ply.** The turn is one transaction and its events do not exist
 *   until it ends, so the live frames are all there is — append them as a row of their own.
 * - **A committed row that has not moved.** An interrupted turn is written down with the rounds it
 *   completed and the pause that stopped it (ADR-0045), and the retry *continues* it. There is one
 *   turn here, so there is one header: the live rounds go beneath the ones already recorded.
 *   Appending instead drew the same turn twice — a committed `0 steps · 1 pause` above a second
 *   header with the resumed rounds under it — which is what a reader sees as the harness losing
 *   track of whose turn it is.
 * - **A committed row that moved.** The turn is finished. Its own events carry every step with
 *   real sequence numbers, so keeping the prediction beside them would draw each step twice, once
 *   as a guess and once as the record.
 */
export function withLiveTurn(turns: TurnView[], frames: LiveFrame[]): TurnView[] {
  const provisional = liveTurn(frames);
  if (provisional === null) return turns;

  /* The *last* row for the ply: a ply can hold more than one when an attempt was recorded and
     abandoned, and the turn in flight continues the newest of them, never the first. */
  const index = turns.findLastIndex((turn) => turn.ply === provisional.ply);
  if (index === -1) return [...turns, provisional];

  const committed = turns[index];
  if (committed.san !== null) return turns;

  /* **The attempt the frames predicted may already be in the record.** A turn commits its rounds
     all at once, so a round appearing in the row is proof that the attempt which produced it has
     ended — and everything the frames were predicting is now written down. The rounds of the
     attempt still running are the ones after the last pause: while there are none, the frames are
     all there is to show; once there are any, keeping them would draw each of those rounds twice,
     once as a guess and once as the record. */
  const pause = committed.blocks.findLastIndex((block) => block.kind === "paused");
  if (committed.blocks.slice(pause + 1).some((block) => block.kind !== "paused")) return turns;

  const merged = [...turns];
  merged[index] = {
    ...committed,
    /* The committed identity wins — its `key` and `seq` are what the timeline sorts and React
       keys on, and swapping them mid-turn would throw away the DOM of the blocks already drawn. */
    blocks: [...committed.blocks, ...provisional.blocks],
    reasoning: [...committed.reasoning, ...provisional.reasoning],
    output: [...committed.output, ...provisional.output],
    tools: [...committed.tools, ...provisional.tools],
    illegal: [...committed.illegal, ...provisional.illegal],
    said: [...committed.said, ...provisional.said],
    live: true,
  };
  return merged;
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

    /* **A turn goes on past its move** ([ADR-0037]): the model is asked once more after
       `make_move`, and what it does with that round is usually `say`. A message has no
       `turn_started` of its own, so one from the closing round looked like an event belonging to
       no turn and was given a fresh one — a second header for the seat that had just moved, with
       the move divider stranded between the two halves of a single turn and the closing round
       under a heading of its own.

       **A model's turn only.** A person's turn really is over when they move — the next thing they
       type may be minutes later and is a row of its own — but a model's closing round is the same
       provider call, still open, still inside the turn. */
    const player = asString(payload.player_id);
    if (current && !current.human && player && current.playerId === player) return current;

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

      case "decided": {
        // A decision model's whole turn in one event (ADR-0049). No reasoning precedes it and no
        // tool call follows: the answer *is* the thinking, and the move is the next event.
        if (current) current.blocks.push(decisionBlock(payload, event.seq));
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
          /* **Only an adjacent pause folds — the rule `foldPauses` holds between turns.** A pause
             joins the row directly above it when that row is the same wait; anything in between —
             the model's retry, a different pause — starts a new row. Resumes are not blocks, so
             pause, resume, pause is still adjacent.

             This once searched the whole block list and folded into the matching row *wherever it
             was*. `c4550202` drew its rate limits as one `×16` row anchored at the first refusal,
             *above* a halt from the evening before — a day of waits hidden behind yesterday, and
             the last row on the page was a halt that had long since lifted. A fold that reorders
             the log is not a summary of it. Matching on `text` still keeps a rate limit and a halt
             as two rows, because one row saying it happened twice would describe neither. */
          const run = current.blocks.at(-1);
          if (run?.kind === "paused" && run.text === reason) {
            run.count += 1;
            run.resumeAfter = resumeAfter;
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
           there is nothing else there to say the game came back.

           **Searched, not peeked at.** A halt can be written on top of a provider pause, so the
           pause a resume answers is not always the last block — the rate limit's resume arrives
           with the halt's row beneath it. Game `c2fd378a` once drew ten stray `RESUMED` rows
           because this peeked at `blocks.at(-1)` while the pause fold searched: two predicates
           that had to agree, and did not.

           Matched on the reason, so a resume cannot swallow itself against an unrelated wait — a
           rate limit and a halt are two rows, and ending one must not silence the other. `reason`
           is a field on the event now; every game recorded before that has it only inside
           `detail`, so it is recovered from the phrase for the archive's sake. */
        const resumeReason = asString(payload.reason) || reasonFromDetail(asString(payload.detail));
        const answered =
          current?.san === null
            ? current.blocks.findLast(
                (block) =>
                  block.kind === "paused" && (resumeReason ? block.text === resumeReason : true),
              )
            : undefined;

        if (!answered) {
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
  const stillPaused = ended ? null : paused;

  /**
   * Mark the one row the reader is *currently* waiting on.
   *
   * Every pause row looks alike, and all but one of them are history. Only this one may say what
   * the game is waiting for — a replay of a finished game must not announce that it is queued for a
   * slot, and an earlier ply's rate limit must not either. Searched from the end and matched on the
   * reason, because the folded row keeps the *first* pause's `seq` while the live pause is the
   * last event, so the two cannot be compared by number.
   */
  if (stillPaused) {
    const block = turns
      .flatMap((turn) => turn.blocks)
      .findLast((b) => b.kind === "paused" && b.text === stillPaused.text);

    if (block?.kind === "paused") block.live = true;
    else {
      const notice = notices.findLast(
        (n) => n.kind === "paused" && n.text === stillPaused.text,
      );
      if (notice) notice.live = true;
    }
  }

  return { turns, moves, ended, notices, paused: stillPaused };
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
/**
 * The pause reason inside a resume's prose, for events recorded before it was a field.
 *
 * `reconciler.resume` writes `reason` explicitly now. Every game already in the archive carries it
 * only as "the wait is over: <reason>", and those games still have to fold — so the phrase is the
 * fallback rather than the source. An unrecognised shape returns null, and the caller then matches
 * any open pause, which is what it did before the field existed.
 */
/**
 * How many times a turn was paused — pauses, not pause *rows*.
 *
 * A repeated refusal folds into one block carrying `count`, so counting the blocks answers a
 * different question than the one the summary line asks. `c2fd378a` summarised a turn that had been
 * rate-limited eleven times as "1 pause", directly above a row reading `×11`.
 *
 * In `lib` rather than beside the header that renders it, because that header lives in a component
 * and components are Playwright's — this is arithmetic, and arithmetic belongs where a unit test
 * can reach it.
 */
/**
 * What a paused game is waiting for, said the way a person would say it.
 *
 * **"retrying shortly" was a lie told by arithmetic.** The old copy ran every pause through a
 * relative clock that returned `"shortly"` for any wait of twenty seconds or less — *including
 * every negative one*. So a wait that expired twenty minutes ago read exactly like one about to
 * end, and it did that forever. Game `c2fd378a` came due at 11:05 and still said "retrying
 * shortly" at 11:26; it was not retrying, it was queued behind another game in its pool.
 *
 * Two different questions, and the row now keeps them apart. `pause_reason` is **why it stopped**
 * and stays true forever. This is **what it is waiting for**, which changes underneath a reason
 * that does not — and only the live pause row is entitled to answer it (`TurnBlock.live`).
 *
 * `null` means say nothing. That is the right answer for every historical pause: it ended, and a
 * row in a replay claiming to be waiting for anything is worse than a row that is simply quiet.
 */
export function waitText(
  resumeAfter: string | null,
  waitingOn: WaitingOn | null,
  now: number = Date.now(),
): string | null {
  /* The clock first, and from the timestamp rather than from `waitingOn.kind` — this row renders
     on a live page where the wait elapses between renders, and the server's answer is from
     whenever the page was fetched. */
  if (resumeAfter) {
    const seconds = Math.round((new Date(resumeAfter).getTime() - now) / 1000);
    if (Number.isFinite(seconds) && seconds > 20) {
      return seconds < 90 ? `retrying in ${seconds}s` : `retrying in ${Math.round(seconds / 60)} min`;
    }
  }

  if (!waitingOn) return null;

  switch (waitingOn.kind) {
    /* The wait has not elapsed after all — the server read it a moment before we did. Deliberately
       vague rather than recomputing a number that is about to be wrong again. */
    case "clock":
      return "retrying shortly";
    /* **A halt that knows when it lifts says so.** The free-model allowance is the halt that will
       actually happen, and it ends at a stated time — `X-RateLimit-Reset`, or the next UTC
       midnight. Two games held behind the same one used to read differently, and the one that fell
       through to this branch said only that it was held: true, and no use to somebody watching a
       board that will not move. An operator halt has no end, and still says so. */
    case "halt":
      return holdEnds(waitingOn.until, now) ?? "held until the harness is resumed";
    case "concurrency":
      return waitingOn.tournament
        ? `waiting for a slot in ${waitingOn.tournament}`
        : "waiting for a slot";
    case "due":
      return "due to resume";
    default:
      return null;
  }
}

/**
 * "held · back in 6h", for a halt with a stated end.
 *
 * Coarser than the retry countdown above, because the scale is different: a rate limit comes back
 * in seconds and a daily allowance in hours, and "back in 371 min" is a number nobody reads.
 * Relative rather than a wall-clock time, which would need a timezone the server does not know.
 */
function holdEnds(until: string | null, now: number): string | null {
  if (!until) return null;

  const seconds = Math.round((new Date(until).getTime() - now) / 1000);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;

  if (seconds < 90 * 60) return `held · back in ${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 48 * 3600) return `held · back in ${Math.round(seconds / 3600)}h`;
  return `held · back in ${Math.round(seconds / 86400)} days`;
}

/**
 * How long a model spent on one block of reasoning, said the way a person would say it.
 *
 * The number is real and it is often startling: a single round of `e601f9af`'s ply 8 took **369
 * seconds**. That figure was in `llm_calls.latency_ms` from the first paid game and had never
 * reached the page, so a reader watching a board not move had no way to tell a slow model from a
 * stuck harness.
 */
export function thoughtFor(durationMs: number | null): string | null {
  if (durationMs === null || durationMs <= 0) return null;
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}

/** 18687 → "18.7k". A reader comparing two reasoning blocks does not want five digits of either. */
function compactTokens(tokens: number): string {
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
}

/**
 * The label on a closed reasoning block.
 *
 * Duration first, because it is what a reader is actually asking — *what took so long* — and the
 * token count second as the size hint. A block with neither still says "reasoning", which is the
 * honest floor for the whole archive written before either number was carried on the event.
 */
export function reasoningLabel(
  block: { tokens: number; durationMs: number | null },
  /** Milliseconds so far, for a block still being written. Wins over `durationMs`, which is null there. */
  elapsedMs: number | null = null,
): string {
  /* **Tense carries the whole meaning.** "reasoned for 12s" is a fact about something finished;
     "reasoning for 12s" is a count still going up. A model that thinks for six minutes under a
     static "reasoning" label is indistinguishable from a stuck harness, which is the thing a
     reader watching a board not move is actually trying to work out. */
  const live = thoughtFor(elapsedMs);
  if (live) return `reasoning for ${live}`;

  const spent = thoughtFor(block.durationMs);
  const head = spent ? `reasoned for ${spent}` : "reasoning";
  return block.tokens > 0 ? `${head} · ${compactTokens(block.tokens)} tokens` : head;
}

export function pauseCount(turn: TurnView): number {
  return turn.blocks.reduce(
    (total, block) => total + (block.kind === "paused" ? block.count : 0),
    0,
  );
}

const RESUMED_PREFIX = "the wait is over: ";

export function reasonFromDetail(detail: string): string | null {
  return detail.startsWith(RESUMED_PREFIX) ? detail.slice(RESUMED_PREFIX.length) : null;
}

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
