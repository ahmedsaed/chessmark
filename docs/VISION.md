# Vision

## The one-liner

**Chessmark makes LLM agents play chess — against each other and against you — and records
absolutely everything.**

## Why chess

Most agentic benchmarks are either trivially saturated or impossible to score. Chess is neither,
and it has a rare combination of properties:

| Property | Why it matters for a benchmark |
| --- | --- |
| **Unambiguous ground truth** | A move is legal or it isn't. A game is won, lost, or drawn. No LLM judge, no rubric, no human rater. |
| **Long horizon** | 40–80 turns of maintained state. Context management, memory, and consistency are tested by construction, not by contrivance. |
| **Adversarial** | The difficulty scales with the opponent. It cannot be saturated by memorising a fixed test set. |
| **Tool-mediated** | The model cannot act on the world except through tools. Tool-calling discipline is measured on every single turn. |
| **Externally calibrated** | Chess has a century-old rating system and freely available superhuman engines. Model skill can be anchored to an absolute scale. |
| **Legible** | A blunder is obvious to a spectator. The benchmark explains itself. |

Chess is not the point. **Long-horizon, tool-mediated, adversarial agentic behaviour is the point.**
Chess is the cleanest available instrument for measuring it.

## The two products

Chessmark is deliberately one codebase serving two purposes. They reinforce each other: the
benchmark supplies credibility, the show supplies traffic and data.

### 1. The benchmark

A public leaderboard answering questions nobody has clean data on:

- Which models can complete a 60-turn tool-driven task **without ever emitting an invalid action**?
- Does illegal-move rate degrade as context grows? (The interesting hypothesis: yes, sharply.)
- Does reasoning-token spend correlate with move quality, or just with cost?
- How much does the system prompt move the needle vs. the underlying model?
- What does a single completed game actually cost, per model?

### 2. The show

A place people come back to for fun:

- Watch two frontier models trash-talk each other while blundering a rook.
- Play a model yourself and read its reasoning after the game.
- Share a replay link of the moment a model hallucinated a piece that wasn't there.

## Audiences

| Audience | Comes for | Needs |
| --- | --- | --- |
| **AI practitioners** | The leaderboard and the raw transcripts | Rigour, methodology transparency, exportable data |
| **Chess players** | Playing and beating an LLM | A board that feels good, legal-move enforcement, no lag |
| **Casual visitors** | The trash talk and the drama | Instant "watch a game" with zero setup, shareable clips |
| **Us** | Understanding how agents fail over long horizons | Complete, replayable logs of every run |

## What success looks like

**Six months in, Chessmark is successful if:**

1. Somebody cites the illegal-move-rate table in an argument about agentic reliability.
2. A model provider looks at their own row and is annoyed by it.
3. A person who does not care about AI watches a full game because the banter was funny.
4. Every claim on the leaderboard can be traced to a stored transcript that reproduces it.

**Leading indicators:** games completed per day, share-link click-through, returning spectators,
median cost per game trending down as the caching layer matures.

## Principles

1. **Log everything, verbatim.** Store the exact request and response bodies, not a summary. Storage
   is cheap; an un-reproducible benchmark result is worthless. If a number appears on the
   leaderboard, the transcript that produced it must be one click away.
2. **The referee is not the model.** The server owns board state and legality, always. Models
   propose; `python-chess` disposes. A model can never corrupt the game record.
3. **Fair by construction.** Every model gets the same tools, the same prompt scaffolding, the same
   retry budget, and the same information. Any deviation is a recorded, versioned experiment.
4. **Cost is a first-class metric, not an afterthought.** A model that plays 50 Elo stronger for 10×
   the price should look worse in the ranking, not better.
5. **The benchmark run and the fun run are separate.** Trash talk, personas, and custom prompts are
   great for the show and must never contaminate ranked results. Ranked games run one fixed,
   versioned configuration.
6. **Reproducibility over convenience.** Prompt templates, tool schemas, and scoring code are
   versioned, and every game records which version it ran under.

## Non-goals

Explicitly out of scope, so we don't drift:

- **Being a chess site.** No puzzles, no lessons, no human-vs-human matchmaking, no opening explorer.
- **Making models good at chess.** No fine-tuning, no engine assistance, no scaffolding tuned to
  boost scores. We measure what's there.
- **A general agent-eval framework.** Chessmark does chess. Generalising the harness is a possible
  future, not a design constraint today.
- **Real-time under 100ms.** Model turns take seconds. The UX is built around thinking time, and
  should make the wait interesting rather than pretend it isn't there.

## Open questions

Deliberately unresolved, to be answered with data rather than argument:

- Does board state as **FEN**, an **ASCII diagram**, or a **move list** produce stronger play? This is
  a prompt-representation experiment worth running properly — and it threatens fairness, since
  different models may prefer different representations.
- Should reasoning-effort settings be normalised across providers, or reported as-is?
- Is a draw against a strong model worth more benchmark signal than a win against a weak one?
  (Glicko-2 says yes; the leaderboard presentation must not obscure it.)
- How do we stop a model from simply *knowing* the opening book, and does that even matter?
