# 0013. Design system: Board & Amber, dark only, conversation-led

**Status:** Accepted
**Date:** 2026-08-15

## Context

The live game page *is* the product. The leaderboard, replays, and share links are all downstream
of whether watching two models fight is genuinely compelling. Three rounds of rendered mockups were
reviewed rather than described, because layout and palette can only be judged in situ.

Two audiences pull in opposite directions: practitioners want depth (reasoning, tool calls, raw
transcripts), casual visitors want narrative (moves and taunts). A design that serves only one
fails the vision, which explicitly asks for both.

## Decision

### Layout — the game is a conversation

Three columns: **stats left · board centre · conversation right**. The two side columns are the
same width, driven by a single `--col` variable; the board is `minmax(0, 1fr)` and absorbs
everything left over.

The conversation panel borrows the messaging-app grammar, because chess has exactly two parties and
a messaging app already solved how to make a two-party timeline readable:

- **Alignment does the labelling.** One model left, one right. Names stop being read after ten
  seconds.
- **The move is the date separator.** A full-width divider closes each turn, so everything above it
  is one coherent thought. This is the piece none of the earlier layouts had.
- **Finished turns fold to one line** (`Thought 2.1s · 1 tool · 288 tok`); the turn in progress
  stays expanded. Reasoning is long and only intermittently interesting — folding is what keeps
  move 60 as readable as move 3.
- **A filter row** (`All / Moves + talk / Talk only`) lets a spectator strip telemetry entirely.

Six event types, six distinct registers — `say` is the only object shaped like a message (filled,
tailed, rounded, largest type), so taunts survive being surrounded by telemetry. An illegal attempt
puts a marker on the *folded* summary, so collapsing can never hide the benchmark's headline number.

### Palette — "Board & Amber", dark only

A wooden board in low light, read by an instrument.

| Role | Token | Value |
| --- | --- | --- |
| Ground | `--color-ground` | `#16130E` — warm brown-black, not neutral |
| Accent (chess) | `--color-accent` | `#DCA84B` — lifted from a real light square |
| Machine (agent) | `--color-machine` | `#63BCC8` |
| Failure | `--color-bad` | `#C86A55` |
| Board squares | `--color-sq-light` / `-dark` | `#9C8869` / `#4B3F2F` |

Board colours are **independent tokens** so board theme can become a user preference later, the way
every chess site does it, without touching the rest of the design.

Typography pairs a serif for headline moments with monospace for all telemetry and notation.
Monospace is honest here rather than decorative — algebraic notation is monospace-native.

## Alternatives considered

**Layout.** Four earlier options were built and rejected: board-dominant with flanking panels
(reasoning too cramped), equal thirds (board too small to read), broadcast feed (no side-by-side
comparison), and board-plus-tabbed-panel (hides the trash talk, which is the hook). A two-lane
timeline with a centre spine was also built — it reads as a match record rather than a conversation,
but the spine costs too much horizontal space.

**Palette.** Three alternatives were rendered in identical components:

- *Analyst* (cold blue-grey console) — most credible for the leaderboard, worst for everything else.
  Trash talk in cold blue-grey isn't funny.
- *Ink & Vermilion* (monochrome, colour reserved exclusively for failure) — the strongest concept
  and the most memorable, but it gives both models the same voice, and the two-party colour split is
  what makes the conversation readable at a glance.
- *Felt & Brass* (tournament green and buff) — the recommended option, rejected in favour of the
  warmth of Board & Amber.

## Consequences

- Tokens live in `apps/web/src/app/globals.css` as Tailwind 4 `@theme` variables. **No component
  hard-codes a colour**; changing direction later is a token edit, not a redesign.
- Dark only means no `prefers-color-scheme` block and no theme stamps — but every colour is painted
  explicitly, so the page holds on any host ground. Adding a light theme later means designing it
  properly, not inverting this one.
- Folding reasoning by default hides the most novel thing on the page. Mitigated by keeping the live
  turn expanded, and by the filter row. Worth revisiting once real games exist and we can see how it
  actually reads.
- Streaming reasoning token-by-token is a **backend commitment**, not a styling choice: turns must
  stream, not post a completed block when the turn ends. This constrains Phase 6's SSE event shapes.
- Board & Amber sits close to Lichess's dark theme. Accepted knowingly; the conversation panel is
  what distinguishes the product visually, not the board.
- The board is large enough that rank and file gutters are worth rendering.

## Alignment is viewer-relative

**Settled.** Bubble alignment is a function of the *viewer*, not the piece colour:

- **Human vs model** — the human is always on the **right**, the model on the left, matching the
  convention of every messaging app.
- **Model vs model** — there is no viewer in the game, so it falls back to White left, Black right.

The conversation component therefore takes `side: "self" | "opponent"` rather than
`color: "white" | "black"`, and the caller resolves which is which from the viewer's identity.
Board orientation follows the same rule: in a human game the viewer's own colour sits at the
bottom, which flips the board and its rank gutter with it.

## Open

- Nothing blocking. Revisit whether folding-by-default is right once real games exist and we can
  see how a 60-move transcript actually reads.
