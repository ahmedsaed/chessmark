# 0069. A page says what its game or model did, in words

**Status:** Accepted
**Date:** 2026-10-18
**Follows:** [0068](0068-a-page-asks-to-be-indexed-only-when-it-says-something.md), which removed the
template pages from the index. This gives the pages that remain something to say.

## Context

After ADR-0068, the pages left in the index were still thin. A game page's readable text was its
move list, two model ids and the step counts. A model page's was a grid of numbers. The models'
reasoning, which is the one text no other site has, was fetched only when a reader opened a turn,
so it never reached the HTML. A search engine reading four hundred game pages saw one page.

## Decision

**A finished game's page opens with a paragraph about it, and quotes the models at up to three
moments.** The paragraph says who had which colour and in which event, the opening reached, how
the game ended, the material at the end, the illegal attempts on each side and what the model
calls cost. The moments are the first illegal attempt, the biggest capture before the end (a
minor piece or more), and the last turn. Each is quoted from the model's own reasoning, or from
its reply if it wrote no reasoning.

**A model page shows its record as tables and bars, not sentences**: results by colour and by
how its games ended (stacked won/drawn/lost bars), its illegal-move rate beside every model's,
a head-to-head table of **every** opponent (linked), and its three most frequent openings per
colour. A first version said this in prose, and the owner rejected it: a paragraph of numbers is
the wrong form for a reader comparing them. The tables are text, so they serve a search engine and
a screen reader as they are. Only the bars are pictures, and each takes its sentence from
`lib/prose.ts` as its accessible name — one sentence per bar, never a hidden copy of the page.

**The opening comes from Lichess's table** (`game/openings.tsv`, CC0, pinned to a commit).
It is matched by position, so a transposition is named for where it arrived, and the deepest named
position wins.

**A game's facts are stored, and a model's are computed per request.**

* A game is immutable once over, and its page reads the API live on every view. The facts need
  every move replayed and every reasoning trace read. So they are computed on the first read after
  the game ends and stored in `games.digest`, versioned by `DIGEST_VERSION`. A stale or missing
  digest is recomputed on its next read, and `./chessmark digests` does the archive ahead of time.
* A model's profile changes with every game it plays. A stored copy would be a second derived table
  to keep current. The page's read is already cached by the web tier and expired when a game moves
  (ADR-0046), and the profile is five grouped statements however many games there are.

**Facts are stored, sentences are not.** The API stores and returns facts. The wording lives in
`apps/web/src/lib/prose.ts`, so a change of phrasing needs no backfill.

**A retired model stays listed.** `/models` listed only what can be started, so when OpenRouter
dropped a model its page lost every link to it, the sitemap's included: twelve models with
finished games, Qwen3.8 27B among them. The catalogue, the sitemap and the archive's filter now
read `GET /models?played=true`, every playable model plus every one with a finished game, and a
retired one carries a badge there and on its page. The game form still lists only what it can
start.

### What the prose may say

* **Only what a field says.** No sentence judges a move. The biggest capture is not "the blunder"
  until an engine has said so.
* **A quote is a slice, never a summary** (invariant 3). It is the end of the turn's text, cut at a
  sentence start, at most 600 characters, and marked when cut. The whole turn stays in the replay.
* **A harness stop names no player** (invariant 11). A ply cap is "the game was stopped", never a
  result about either model.
* **A person is never named or linked.** People are one opponent, "people on this site".
* **Nothing is built for a live game** (invariant 8). A digest quotes reasoning, so it is computed
  only once the game is finished or aborted, the same rule `api/redaction.py` applies.

## Alternatives considered

* **LLM-written commentary.** Rejected with the owner. It costs money per game, and it makes claims
  about a game that nothing checks against the record.
* **Store the sentences.** Every change of wording would need a backfill of the whole archive.
* **Show every reasoning trace in the HTML.** That is the whole transcript on every page view, and
  page weight the replay already avoids by loading turns when opened.
* **Engine analysis** to find the real turning point. More useful and more work: a binary in the
  worker and per-ply evaluations. Not part of this decision.

## Consequences

* `games.digest` is a derived value. Where it and the record disagree, the record wins
  (invariant 1), and recomputing it is always safe.
* The column is deferred and raises if touched unloaded, so a list endpoint cannot start shipping
  quoted reasoning for every row by accident.
* Model openings count only games that have a digest. Run `./chessmark digests` once after the
  deploy that adds the column, or they fill in only as old games are opened.
* `GET /games/{id}` costs one more statement for a finished game (reading the stored digest), and
  the first read after a game ends costs two more (plies and events).
