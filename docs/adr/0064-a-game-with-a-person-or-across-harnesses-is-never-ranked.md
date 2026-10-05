# 0064. A game with a person, or between a chat and a decision model, is never ranked

**Status:** Accepted
**Date:** 2026-10-05
**Amends:** [0063](0063-chat-and-decision-models-are-ranked-on-separate-leaderboards.md), which left
games between the two kinds of model undecided. Extends BENCH-03.

## Context

ADR-0063 split the leaderboard into chat models and decision models. It left one question open: what
happens to a game *between* the two?

The API allowed one to be ranked: `POST /games` passes the caller's `is_ranked` through, and nothing
in the rating rules looked at which kinds of model played. None had been played yet, but the first
would have entered the one fit and coupled the two groups that ADR-0063 had just separated. Such
games would be harmful, not harmless, for two reasons:

- **The caller chooses the pairing.** A pool's matchmaker picks games for what they teach. A person
  might pit the strongest chat model against one decision model ten times, and whether a model was
  picked would decide how hard it was pulled towards the other group: selection bias in the ratings.
- **It would move places within a group, not only link the groups.** A decision model that lost five
  such games would drop below decision models nobody picked, by an amount mostly set by the
  imaginary-draw prior, because results across the groups are likely to be one-sided (ADR-0063's
  anchor trial).

Games with a person were already created unranked by the `/play` route, with the comment "a person
is not a contestant". But nothing deeper held it. A ranked game with a person, created any other
way, would have been dropped from the ratings only by accident, because its human seat has no
model.

## Decision

**Two pairings can never make a ranked game:**

- **A person in either seat.** People are not contestants.
- **A chat model against a decision model.** They are ranked on separate leaderboards.

**One rule, `bench.ratable.unrankable`, enforced in two places:**

- **When a game is created.** `create_match` refuses `is_ranked=True` for these pairings with
  `UnrankableMatchError`, and `POST /games` returns it as a 422 with the reason. It is refused
  rather than quietly made unranked, because changing what someone asked for without saying so is
  worse than telling them why.
- **When ratings are computed.** `judge` excludes any such game, ranked or not, with the same
  sentence, and the reason is listed with the excluded games (BENCH-10). This is the guarantee: the
  "ranked" flag is set when a game is created, while `judge` is the rule every game passes through,
  scripts and old rows included.

The same games are still played, recorded and replayable. Only the ranking ignores them.

## Alternatives considered

**Let them count, as a link between the groups.** Rejected for the two reasons above.

**Silently create them unranked.** Simpler for the caller, and it would mean a person asking for a
ranked game gets an unranked one without knowing. Through the site this does not arise: `/play` has
no ranked option, and its games are created unranked.

**Only the creation check.** It covers games made through the API. A script, a tournament, or a row
written before this change would still slip through, and the first one would couple the boards.

## Consequences

- **No numbers change today.** No such game had been ranked: there were no games across the
  harnesses, and every game with a person was already unranked.
- **Each model's record against the other kind of model is information, not a ranking.** Showing it
  on model pages is a possible follow-up.
