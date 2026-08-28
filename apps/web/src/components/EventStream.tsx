"use client";

/**
 * The event stream (ADR-0013) — the right-hand column of a live game and of a replay.
 *
 * **Named for what it is.** It was the "conversation" panel, from when trash talk was the
 * interesting thing in it, and that name had stopped being true: most of what it shows is
 * reasoning, tool calls, illegal attempts and now the harness interrupting itself. Calling it a
 * conversation invited the reading that anything not addressed to the opponent did not belong.
 *
 * A messaging-app timeline: side identifies the player, and the move acts as a date separator
 * closing each turn. Finished turns fold to one line; the open turn shows what the model actually
 * did, which is what keeps move 60 as readable as move 3.
 *
 * **Five registers, deliberately distinct**, because they are five different kinds of thing:
 *
 * | | |
 * | --- | --- |
 * | `reasoning` | what the model was thinking — quiet, bordered, low contrast |
 * | `output` | what it wrote outside a tool call — plain prose |
 * | tools | machinery — monospace, with the arguments and the result |
 * | `say` | addressed to the opponent — the only thing shaped like a message |
 * | notices | the harness, not a player: a pause and why, spanning the full width |
 *
 * Reasoning and output are separate for a reason found the hard way: DeepSeek puts everything in
 * `reasoning` and Gemini puts everything in `content`, so a panel that renders only one of them
 * makes an entire model look silent.
 *
 * Notices are separate from all four because they have no side. A rate limit is not something
 * either model did, and drawing it as one player's message would attribute the harness's failure
 * to a contestant.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import type { Player, StreamNotice, ToolCallView, TurnView } from "@/lib/types";

type Filter = "all" | "moves-talk" | "talk" | "moves";

/** The three registers a turn can be unrolled into, each with its own disclosure. */
type Section = "reasoning" | "output" | "tools";

/**
 * Ply number to chess notation. Ply 1 is "1.", ply 2 is "1…", ply 3 is "2." — a full move is two
 * plies, and Black's half is written with an ellipsis. Printing the raw ply here would read as
 * nonsense to anyone who plays chess.
 */
function moveLabel(ply: number, san: string): string {
  const moveNumber = Math.ceil(ply / 2);
  return ply % 2 === 1 ? `${moveNumber}. ${san}` : `${moveNumber}… ${san}`;
}

const FILTERS: { id: Filter; label: string; title: string }[] = [
  { id: "all", label: "All", title: "Everything: reasoning, output, tools, talk" },
  { id: "moves-talk", label: "Moves + talk", title: "Moves and trash talk only" },
  { id: "talk", label: "Talk", title: "Trash talk only" },
  { id: "moves", label: "Moves", title: "The move list alone" },
];

export function EventStream({
  turns,
  notices = [],
  focusKey,
  onInspect,
  emptyMessage = "Waiting for the first turn…",
  header,
  footer,
  players = [],
}: {
  turns: TurnView[];
  /**
   * Pauses and resumes — the harness interrupting itself. Interleaved with the turns by `seq`
   * rather than appended, so a game that paused at move 12 reads in the order it happened.
   */
  notices?: StreamNotice[];
  /**
   * The turn to open by default. Replay passes the turn that produced the current ply so scrubbing
   * always lands on something readable. It seeds the open set rather than overriding it — a turn
   * forced permanently open is a turn the reader cannot get out of the way.
   */
  focusKey?: string | null;
  /** Opens the raw payloads behind a turn. Absent while a game is live — the API refuses them. */
  onInspect?: (turn: TurnView) => void;
  emptyMessage?: string;
  /** Rendered above the filters. The replay transport lives here, next to what it scrubs. */
  header?: React.ReactNode;
  /**
   * Pinned under the timeline: resign, draw, and the message box. They belong to the conversation
   * rather than to the board — everything said to an opponent is already in this column.
   */
  footer?: React.ReactNode;
  /**
   * The seats, so a turn can be labelled with who took it. A person's turn carries no model name,
   * and without this every human ply was headed "white" or "black" rather than by the player.
   */
  players?: Player[];
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  /** Whether the reader is at the bottom. Starts true: a fresh panel is scrolled to the newest. */
  const pinned = useRef(true);

  /* Follow the conversation only while the reader is already at the bottom — the rule every
     messaging app follows, and the reason is the same: someone who has scrolled up to read an
     earlier turn is *reading it*, and yanking them back on the next event loses their place.
     A `ResizeObserver` rather than an effect on `turns.length`, because a turn does not arrive at
     its full height: it lands folded and then grows as its tool calls stream in. Following the
     length alone scrolled to a bottom that immediately moved, leaving the panel short of it —
     and the gap was enough to read as "the reader scrolled up", so it stopped following after
     one turn. Watching the content's height instead follows whatever makes it taller. */
  useEffect(() => {
    const element = content.current;
    const view = scroller.current;
    if (!element || !view) return;

    const stick = () => {
      if (!pinned.current) return;
      /* Instant, not smooth. A smooth scroll animates through positions far from the bottom, and
         the scroll events it emits on the way are indistinguishable from a reader scrolling up —
         so the panel unpinned itself mid-animation. */
      view.scrollTop = view.scrollHeight;
    };

    stick();
    const observer = new ResizeObserver(stick);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const visible = useMemo(() => {
    if (filter === "talk") return turns.filter((turn) => turn.said.length > 0);
    if (filter === "moves") return turns.filter((turn) => turn.san !== null);
    return turns;
  }, [turns, filter]);

  /* Turns and notices in one list, ordered by `seq`. Built here rather than by the caller so the
     filters keep working: "talk" hides turns and must hide the notices between them too, or the
     panel would show a rate limit with no play around it to give it a place. */
  const timeline = useMemo(() => {
    const entries: ({ kind: "turn"; turn: TurnView } | { kind: "notice"; notice: StreamNotice })[] =
      visible.map((turn) => ({ kind: "turn" as const, turn }));
    if (filter === "all") {
      for (const notice of notices) {
        const at = entries.findIndex(
          (entry) => entry.kind === "turn" && entry.turn.seq > notice.seq,
        );
        const item = { kind: "notice" as const, notice };
        if (at === -1) entries.push(item);
        else entries.splice(at, 0, item);
      }
    }
    return entries;
  }, [visible, notices, filter]);

  /**
   * Whether one section of one turn is open.
   *
   * **Three disclosures per turn, not one.** A single toggle meant that reading a tool call also
   * unrolled several thousand words of reasoning, so the thing you wanted was pushed off the
   * screen by the thing you did not.
   *
   * Defaults follow what a reader is there for: on the live turn, reasoning and tools are open so
   * the panel shows the model working. `output` is closed everywhere — it is the model's prose
   * *about* its move, which is worth having and not worth being handed unasked.
   */
  function isOpen(turn: TurnView, section: Section): boolean {
    const explicit = toggled[`${turn.key}:${section}`];
    if (explicit !== undefined) return explicit;
    if (section === "output") return false;
    return turn.live || turn.key === focusKey;
  }

  return (
    <section
      aria-label="Event stream"
      className="flex min-h-0 flex-col border border-line bg-surface-2"
    >
      {header}

      <div className="flex flex-none flex-wrap items-center gap-1.5 border-b border-line bg-surface-3 px-3 py-2">
        {FILTERS.map(({ id, label, title }) => (
          <button
            key={id}
            type="button"
            onClick={() => setFilter(id)}
            aria-pressed={filter === id}
            title={title}
            className={`font-mono text-[10px] uppercase tracking-[0.1em] border px-2 py-1 transition-colors ${
              filter === id
                ? "border-accent bg-accent text-on-accent"
                : "border-line bg-surface text-ink-faint hover:text-ink-dim"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div
        ref={scroller}
        onScroll={(event) => {
          /* Slack, because "at the bottom" is never exactly zero: sub-pixel heights, a growing
             live turn, and the browser's own rounding all leave a few pixels. Too strict a test
             unpins a panel that is in fact at the bottom, and then it stops following. */
          const element = event.currentTarget;
          const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
          pinned.current = distance < 80;
        }}
        className="min-h-0 flex-1 overflow-y-auto p-3"
      >
        <div ref={content} className="flex flex-col gap-3">
        {visible.length === 0 && (
          <p className="font-mono text-xs text-ink-faint">{emptyMessage}</p>
        )}

        {filter === "moves" ? (
          <MoveList turns={visible} />
        ) : (
          timeline.map((entry) =>
            entry.kind === "notice" ? (
              <Notice key={entry.notice.key} notice={entry.notice} />
            ) : (
              <Turn
                key={entry.turn.key}
                turn={entry.turn}
                name={turnName(entry.turn, players)}
                filter={filter}
                isOpen={(section) => isOpen(entry.turn, section)}
                onToggle={(section) =>
                  setToggled((previous) => ({
                    ...previous,
                    [`${entry.turn.key}:${section}`]: !isOpen(entry.turn, section),
                  }))
                }
                onInspect={onInspect && (() => onInspect(entry.turn))}
              />
            ),
          )
        )}
        </div>
      </div>

      {footer && <div className="flex-none border-t border-line bg-surface-3 p-2">{footer}</div>}
    </section>
  );
}

/**
 * One disclosure, with a size hint.
 *
 * The hint is the point: the complaint that started this was reasoning being *long*, and a reader
 * deciding whether to unroll it wants to know whether that is two lines or two thousand words
 * before they find out the hard way.
 */
function Disclosure({
  label,
  hint,
  tone,
  open,
  onToggle,
}: {
  label: string;
  hint?: string;
  tone?: "bad";
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={`inline-flex items-center gap-1.5 border bg-surface px-2 py-1 font-mono text-[10px] transition-colors hover:text-ink-dim ${
        open ? "border-machine-dim text-ink-dim" : "border-line text-ink-faint"
      }`}
    >
      <span aria-hidden className="text-machine">
        {open ? "▾" : "▸"}
      </span>
      <span className="text-ink-dim">{label}</span>
      {hint && <span className={tone === "bad" ? "text-bad" : "text-ink-faint"}>· {hint}</span>}
    </button>
  );
}

/**
 * How much text is behind a disclosure, in the roundest terms that are still useful.
 *
 * Characters rather than tokens: the panel has no token count for a *section* — the turn's total
 * is on the stats rail — and "2.4k" answers the only question being asked, which is whether this
 * is a glance or a scroll.
 */
function sizeOf(blocks: string[]): string {
  const characters = blocks.reduce((total, text) => total + text.length, 0);
  return characters < 1000 ? `${characters}` : `${(characters / 1000).toFixed(1)}k`;
}

/**
 * The harness, not a player.
 *
 * Full width and sideless, because a pause belongs to neither model. A rate limit is not something
 * a contestant did, and drawing it as one player's message would attribute the harness's failure to
 * a model.
 *
 * `bad` for the pause, matching how every other fault in this app is drawn, and the quiet `line` /
 * `ink-faint` pair for the resume — one is a thing to know about, the other is only the
 * reassurance that it ended. Tokens throughout; no component hard-codes a colour (ADR-0013).
 */
const NOTICE_LABEL: Record<StreamNotice["kind"], string> = {
  paused: "paused",
  resumed: "resumed",
  compacted: "compacted",
};

function Notice({ notice }: { notice: StreamNotice }) {
  const paused = notice.kind === "paused";
  /* A compaction is machinery, not a fault — the model doing its own housekeeping — so it reads in
     the `machine` register the tool calls use rather than in `bad`. Only a pause is a problem. */
  const tone = paused
    ? "border-bad-deep bg-surface text-bad"
    : notice.kind === "compacted"
      ? "border-machine-deep bg-surface text-machine"
      : "border-line bg-surface-3 text-ink-faint";

  return (
    <div role="status" className={`border px-3 py-2 font-mono text-[11px] leading-relaxed ${tone}`}>
      <span className="uppercase tracking-[0.1em]">{NOTICE_LABEL[notice.kind]}</span>
      <span className="text-ink-dim"> · {notice.text}</span>
      {notice.resumeAfter && (
        <span className="text-ink-faint"> · retrying {relativeTime(notice.resumeAfter)}</span>
      )}
    </div>
  );
}

/**
 * "in 4 minutes", or "shortly" once it is due.
 *
 * Rendered client-side on purpose: a server-rendered "in 4 minutes" is wrong by the time anybody
 * reads it, and this column is already a client component following a live stream.
 */
function relativeTime(iso: string): string {
  const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  if (!Number.isFinite(seconds) || seconds <= 20) return "shortly";
  if (seconds < 90) return `in ${seconds}s`;
  return `in ${Math.round(seconds / 60)} min`;
}

/**
 * Who took this turn.
 *
 * A model's turn is headed by its slug, which is the thing worth knowing about it. A person's has
 * no slug — `model` is empty — so it falls back to the seat's display name, and only then to the
 * bare colour. Before this, every human ply was headed "white".
 */
function turnName(turn: TurnView, players: Player[]): string {
  if (turn.model) return turn.model;
  const seat = players.find((player) => player.id === turn.playerId);
  return seat?.display_name || turn.colour;
}

/** The move list, as a filter rather than a separate panel — it is part of the same timeline. */
function MoveList({ turns }: { turns: TurnView[] }) {
  const moves = turns.filter((turn) => turn.san).map((turn) => ({ ply: turn.ply, san: turn.san! }));

  return (
    <ol className="tabular grid grid-cols-[2rem_1fr_1fr] gap-x-3 gap-y-1 font-mono text-xs text-ink">
      {Array.from({ length: Math.ceil(moves.length / 2) }, (_, index) => (
        <li key={index} className="contents">
          <span className="text-ink-faint">{index + 1}</span>
          <span>{moves[index * 2]?.san ?? ""}</span>
          <span>{moves[index * 2 + 1]?.san ?? ""}</span>
        </li>
      ))}
    </ol>
  );
}

function Turn({
  turn,
  name,
  filter,
  isOpen,
  onToggle,
  onInspect,
}: {
  turn: TurnView;
  /** The model slug, or the person's name for a human turn. */
  name: string;
  filter: Filter;
  isOpen: (section: Section) => boolean;
  onToggle: (section: Section) => void;
  onInspect?: () => void;
}) {
  const isWhite = turn.colour === "white";
  const detail = filter === "all";
  const show = (section: Section) => detail && isOpen(section);

  /* White reads from the left, Black from the right — the side is what identifies the player, so
     no bubble needs a name on it. Only the *block* is mirrored: the text inside stays
     left-aligned, because right-aligned prose in a left-to-right language is hard to read and
     mirroring the layout was never meant to mirror the language. */
  const align = isWhite ? "items-start" : "items-end";
  const edge = isWhite ? "border-l-2 pl-2.5" : "border-r-2 pr-2.5";

  return (
    <div className="flex flex-col gap-2">
      {turn.san && (
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
          <span className="h-px bg-line" />
          <span className="tabular border border-accent-deep bg-surface px-2 py-0.5 font-mono text-[11px] text-accent">
            {moveLabel(turn.ply, turn.san)}
          </span>
          <span className="h-px bg-line" />
        </div>
      )}

      <div className={`flex flex-col gap-1.5 ${align}`}>
        <div
          className={`flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint ${
            isWhite ? "" : "flex-row-reverse"
          }`}
        >
          <i
            aria-hidden
            className={`block h-2 w-2 border border-line ${
              isWhite ? "bg-piece-white" : "bg-piece-black"
            }`}
          />
          {name}
          {turn.live && <span className="text-accent">· thinking</span>}
        </div>

        {filter !== "talk" && !turn.human && (
          <div className={`flex flex-wrap items-center gap-1.5 ${isWhite ? "" : "justify-end"}`}>
            {turn.reasoning.length > 0 && (
              <Disclosure
                label="reasoning"
                hint={sizeOf(turn.reasoning)}
                open={isOpen("reasoning")}
                onToggle={() => onToggle("reasoning")}
              />
            )}

            {/* Not a disclosure: there is nothing behind it to open. The count is the honest
                thing to show — it says the model *is* reasoning, and how much, without saying
                what about (invariant 8). Revealed in full once the game is over. */}
            {turn.reasoning.length === 0 && turn.withheldReasoning > 0 && (
              <span
                title="Reasoning is withheld while you are playing this game — it is published when the game ends"
                className="inline-flex items-center gap-1.5 border border-line bg-surface px-2 py-1 font-mono text-[10px] text-ink-faint"
              >
                <span aria-hidden className="text-machine">
                  ·
                </span>
                thinking
                <span className="text-ink-faint">· {turn.withheldReasoning.toLocaleString()}</span>
              </span>
            )}

            {turn.output.length > 0 && (
              <Disclosure
                label="output"
                hint={sizeOf(turn.output)}
                open={isOpen("output")}
                onToggle={() => onToggle("output")}
              />
            )}

            {(turn.tools.length > 0 || turn.illegal.length > 0) && (
              <Disclosure
                label={`${turn.tools.length} tool${turn.tools.length === 1 ? "" : "s"}`}
                hint={turn.illegal.length > 0 ? `${turn.illegal.length} illegal` : undefined}
                tone={turn.illegal.length > 0 ? "bad" : undefined}
                open={isOpen("tools")}
                onToggle={() => onToggle("tools")}
              />
            )}

            {/* Every number on this page traces to a payload; this is the link (LOG-07). Shown
                whenever it exists rather than only while something is unrolled: it is an action on
                the turn, and there is no longer one "open" for it to hang off. */}
            {onInspect && (
              <button
                type="button"
                onClick={onInspect}
                className="inline-flex items-center border border-machine-deep bg-surface px-2 py-1 font-mono text-[9px] uppercase tracking-[0.1em] text-machine transition-colors hover:border-machine hover:text-ink-dim"
              >
                raw
              </button>
            )}
          </div>
        )}

        {show("reasoning") &&
          turn.reasoning.map((text, index) => (
            <p
              key={`${turn.key}-r${index}`}
              title="reasoning"
              className={`max-w-[94%] whitespace-pre-wrap text-xs leading-relaxed text-ink-dim border-machine-deep ${edge}`}
            >
              {text}
            </p>
          ))}

        {show("output") &&
          turn.output.map((text, index) => (
            <p
              key={`${turn.key}-o${index}`}
              title="model output"
              className={`max-w-[94%] whitespace-pre-wrap border-accent-deep text-[13px] leading-relaxed text-ink ${edge}`}
            >
              {text}
            </p>
          ))}

        {show("tools") &&
          turn.tools.map((tool, index) => (
            <Tool key={`${turn.key}-t${index}`} tool={tool} align={isWhite ? "left" : "right"} />
          ))}

        {/* Illegal attempts unroll with the tools: an illegal move *is* a failed `make_move`,
            and the trigger already carries the count in `bad`. */}
        {show("tools") &&
          turn.illegal.map((attempt, index) => (
            <p
              key={`${turn.key}-i${index}`}
              className="max-w-[94%] border border-bad-deep bg-surface px-2 py-1 font-mono text-[10px] leading-relaxed text-bad"
            >
              {attempt.move} → illegal · attempt {attempt.attempt}
              {attempt.detail && <span className="block text-ink-faint">{attempt.detail}</span>}
            </p>
          ))}

        {turn.said.map((message, index) => (
          <p
            key={`${turn.key}-s${index}`}
            className={`max-w-[94%] rounded-[13px] px-3 py-2 text-[13px] font-medium leading-snug text-on-accent ${
              isWhite ? "rounded-bl-[3px] bg-accent" : "rounded-br-[3px] bg-machine"
            }`}
          >
            {message}
          </p>
        ))}
      </div>
    </div>
  );
}

/**
 * One tool call: the name, its arguments, and its result behind a disclosure.
 *
 * The arguments are on the face because they are short and they are the interesting half —
 * `make_move(e4)` says what happened; `make_move()` says nothing. Results are folded away because
 * `get_legal_moves` returns forty entries and would bury everything around it.
 */
function Tool({ tool, align }: { tool: ToolCallView; align: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const args = Object.entries(tool.args)
    .map(([key, value]) => `${key}: ${format(value)}`)
    .join(", ");
  const hasResult = tool.result !== null && Object.keys(tool.result).length > 0;

  return (
    <div className={`flex max-w-[94%] flex-col gap-1 ${align === "right" ? "self-end" : ""}`}>
      <button
        type="button"
        onClick={() => hasResult && setOpen(!open)}
        aria-expanded={hasResult ? open : undefined}
        disabled={!hasResult}
        className={`inline-flex items-center gap-1.5 border bg-surface px-2 py-1 text-left font-mono text-[10px] transition-colors ${
          tool.ok
            ? "border-machine-deep text-machine hover:border-machine"
            : "border-bad-deep text-bad"
        } ${hasResult ? "cursor-pointer" : "cursor-default"}`}
      >
        {hasResult && (
          <span aria-hidden className="text-ink-faint">
            {open ? "▾" : "▸"}
          </span>
        )}
        <span>
          {tool.name}(<span className="text-ink-dim">{args}</span>)
        </span>
      </button>

      {open && tool.result && (
        <pre className="max-h-56 overflow-auto border border-line bg-surface p-2 font-mono text-[10px] leading-relaxed text-ink-dim">
          {JSON.stringify(tool.result, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** Arguments inline, compactly. A long value is elided rather than allowed to wrap for ten lines. */
function format(value: unknown): string {
  if (typeof value === "string") return value.length > 40 ? `${value.slice(0, 40)}…` : value;
  if (value === null || value === undefined) return "—";
  const text = JSON.stringify(value);
  return text.length > 40 ? `${text.slice(0, 40)}…` : text;
}
