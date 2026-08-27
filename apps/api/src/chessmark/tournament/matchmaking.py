"""Choosing the next game in a pool that never ends.

A closed event knows its fixture list from the field. A **pool** does not: it runs indefinitely,
its field changes as the catalogue does, and there is no winner — only ratings that get sharper.
So instead of a schedule it needs a policy, and the policy follows from what Glicko-2 actually
measures.

Two facts drive it:

- **A rating deviation is the point.** A model at 1500 ± 350 has told us nothing yet; one at
  1500 ± 40 has. The most valuable next game is the one involving whoever we know least about,
  which is exactly what makes a newly-listed model settle quickly rather than sitting unrated.
- **A game between mismatched players teaches little.** If the result is a foregone conclusion,
  it moves neither rating much. Pairing near-equals is what converts a game into information.

So: take the least-known entrant, and give them the closest-rated opponent who is not a rematch.
Pure, like the rest of this package — ratings are handed in rather than read, so the policy can be
tested against fixtures without a database or a rating engine.

**And skip whoever cannot play right now**, which is not a refinement of the policy but a
correction to it. The two facts above pull toward whoever we know least about, and a model whose
games keep failing is *permanently* the one we know least about: an abandoned game is excluded from
ratings, so its deviation never moves, so it is chosen again. One free model went dark for ninety
minutes and the pool paired it fourteen consecutive times, each pairing dying at ply 0. Nothing in
"least known first" can escape that on its own — the policy has to be told who is unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from chessmark.tournament.pairing import _colour_balance, ordered
from chessmark.tournament.types import Entrant, Pairing, Result

#: A rating point of separation is worth this much when weighed against a rematch. Set so that any
#: unmet opponent beats any already-met one: no plausible rating gap reaches it.
_REMATCH_PENALTY = 100_000.0


@dataclass(frozen=True, slots=True)
class Form:
    """What is known about an entrant, as far as matchmaking cares.

    Deliberately not a Glicko-2 type. The pool policy needs a number for strength and a number for
    confidence; where they came from is the caller's business, and keeping it that way is what lets
    this be tested with hand-written fixtures.
    """

    key: str
    rating: float = 1500.0
    #: Glicko-2's rating deviation. 350 is "never seen"; a settled model is nearer 50.
    deviation: float = 350.0
    games: int = 0


def matchmake(
    entrants: Sequence[Entrant],
    results: Sequence[Result],
    form: dict[str, Form],
    *,
    count: int = 1,
    round_number: int = 1,
    unavailable: frozenset[str] | set[str] = frozenset(),
) -> list[Pairing]:
    """The next `count` games to play.

    Nobody is paired twice in one batch — those games would run concurrently, and a model cannot
    play itself in two places at once.

    `unavailable` names entrants that cannot be played at this moment — today, models whose only
    endpoint is resting off a rate limit (`core/cooldown.py`). They are **skipped, not withdrawn**:
    the distinction is the whole point. A withdrawal is a statement about the event, and it
    abandons that entrant's remaining pairings; this is a statement about the next few minutes, and
    the entrant returns by itself when its cooldown expires. A pool has no deadline, so the cost of
    waiting is nothing and the cost of pairing a model that cannot play is a wasted slot.

    Fewer than two available entrants returns no games rather than pairing regardless. The pool
    holds for a tick, which is correct: there is no game worth starting.
    """
    field = [e.key for e in ordered(entrants)]
    if len(field) < 2:
        return []

    met = _meetings(results)
    balance = _colour_balance(results)
    # Form is built over the whole field, not the available subset. A resting entrant's rating is
    # still real and is still what an opponent is chosen for proximity to; only its own turn to
    # play is deferred.
    known = {key: form.get(key, Form(key=key)) for key in field}

    available = set(field) - set(unavailable)
    games: list[Pairing] = []

    for _ in range(count):
        if len(available) < 2:
            break

        # The least-known entrant goes first: their next game is worth the most. Ties break on
        # fewest games played, then on key so the choice is reproducible.
        home = min(
            available,
            key=lambda key: (-known[key].deviation, known[key].games, key),
        )
        away = _closest(home, available - {home}, known, met)
        if away is None:  # pragma: no cover - unreachable while two entrants remain
            break

        white, black = _colours(home, away, balance)
        games.append(Pairing(white=white, black=black, round_number=round_number))

        # Reflect this game before choosing the next, so a batch does not hand the same model
        # White three times or repeat a pairing it just made.
        met[frozenset({home, away})] = met.get(frozenset({home, away}), 0) + 1
        balance[white] = balance.get(white, 0) + 1
        balance[black] = balance.get(black, 0) - 1
        available -= {home, away}

    return games


def _closest(
    home: str, candidates: set[str], known: dict[str, Form], met: dict[frozenset[str], int]
) -> str | None:
    """The most informative opponent: unmet if possible, then nearest in rating."""
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda key: (
            met.get(frozenset({home, key}), 0) * _REMATCH_PENALTY
            + abs(known[home].rating - known[key].rating),
            key,
        ),
    )


def _colours(home: str, away: str, balance: dict[str, int]) -> tuple[str, str]:
    """White to whoever is more owed it.

    Over a pool that runs for months this matters more than in a single event: White scores
    better, and a model that drew it two thirds of the time would carry a rating partly measuring
    that.
    """
    if balance.get(home, 0) > balance.get(away, 0):
        return away, home
    return home, away


def _meetings(results: Sequence[Result]) -> dict[frozenset[str], int]:
    """How many times each pair has already played.

    A count rather than a set: a pool runs long enough that every pair eventually meets, so the
    question stops being *whether* and becomes *how recently and how often*.
    """
    counts: dict[frozenset[str], int] = {}
    for result in results:
        if result.black is None:
            continue
        pair = frozenset(result.players)
        counts[pair] = counts.get(pair, 0) + 1
    return counts
