# 0070. The hero shows a picked game between live ones

**Status:** Accepted
**Date:** 2026-10-20

## Context

The lobby's hero plays a running game. Most of the time on a small deployment nothing is running,
and the hero fell back to the most recent finished game, whatever it was. Often that was two free
models shuffling kings towards a draw (`… 63.Kb1 Bb4 64.Ka1 Ba5 65.Kb1`). It was the first thing a
visitor saw.

Some games are worth showing. Claude Opus 5.5 wrote to itself after 31… Qd8, *"I made this move
without checking White's replies properly. Next turn I must check Qh7, Bxh6 and Nxh6"*, and GPT-6.1
Sol mated it with Qg8. Being able to show both sides' words around the same move is what this site
has and others do not.

The owner asked for a way to show games like that. A first mock was a whole "featured match"
section, with a series score and stat bars. It took three screens, and two games are noise as a
benchmark: "5 illegal attempts to 0" from two unranked games reads like a finding and is not one.

## Decision

**When nothing is live, the hero shows a game the operator picked.** The order is a live game,
then the latest pick, then the most recent finished game as before (`lib/featured.ts`,
`chooseHero`). A live game always wins: the pick is for the time between games.

**A pick is a headline and up to one quote per side, no statistics.** The card under the site's
own headline carries the operator's line about the game and each side's words, labelled with the
move they were written around. The board loops the last ten plies, which is the ending the game
was picked for. The hero stays the same height, and the lobby gains no section.

**A quote is checked against the game's log, not trusted** (`db/featured.py`). It is accepted only
if it is a contiguous slice of that side's own reasoning or notes in that game, and what is stored
is the record's slice, not what was typed. Typographic quotes and apostrophes are matched as their
plain forms so a person can type what they read. A quote that is not word for word is refused with
the reason. This is invariant 3 applied to the front page: words in quotation marks are the model's.

**Only a finished game, and only one no person played in.** A featured game shows reasoning, which
is never exposed mid-game (invariant 8). A game with a human seat is that person's, and the front
page is not where it gets published.

**Picked from the operator's shell.** `./chessmark feature <game> "headline" --white-quote …
--black-quote …`, with `--list` and `--remove`. The same thing is available as `POST /admin/featured`
for an admin session. There is no picking UI on the site.

**Stored in its own table, `featured_games`.** A headline is somebody's words about a game, not a
fact of it, so it does not sit on `games` beside the result. One row per game: featuring a game again
refreshes its row. Removing a pick sets `removed_at`, and the pick before it takes the hero back.
The public read, `GET /games/featured`, is one statement, cached under its own `featured` tag, and
the API names that tag when a pick changes (ADR-0046).

## Alternatives considered

- **A "featured match" section below the hero.** Mocked twice: once as a full match with a series
  score and comparisons, and once as a compact card. The full version was three screens and
  presented two games as a result. The compact card cost a row of the lobby on every visit and put
  a second animated board under the first. The hero slot was going spare between games anyway.
- **Quotes picked automatically from the digest** (ADR-0069). That costs the operator no effort, but
  the digest's excerpts run to 600 characters and are chosen by position in the game, not by
  whether they say anything. The card has room for a line.
- **Columns on `games`.** These would be purely additive, but they put editorial text on the core
  record.
- **Rotating through picks.** Rotating would make the cached lobby depend on the time, which
  ADR-0046 removed. The latest pick is shown; older ones stay in `--list`.

## Consequences

- **The hero is curated when the site is quiet.** That is the point. It also means the hero can go
  stale: a pick stays until it is replaced or removed.
- **A featured game says nothing about ratings.** The card shows no score or statistics, and a pick
  can be unranked. The leaderboard remains the only claim about strength.
- **One side may have nothing to quote.** Some providers return reasoning encrypted, and some write
  none. That side's seat is still named on the card, without a quote.
- **The pick is not on `/games/{id}`.** A featured game's own page does not say it was featured.
  The pick is a lobby decision.
