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

**A rematch is counted from every pairing attempted, not only from the ones that produced a
result.** The rematch penalty used to read `results` alone, and a result is exactly what a dead
pairing never has — so a fixture that could not be played was permanently *unmet*, and permanently
the most attractive thing to schedule. `gemma-4-26b` and `gemma-4-31b`, both unrated and both on
the same rate-limited Google pool, were paired against each other **seven times over five days and
never made a single move**: each game paused until the 24-hour window ran out, was abandoned,
recorded nothing, and was chosen again within the hour. Attempting a pairing is what makes it a
rematch; whether it survived is a separate question.

## Two policies, one driver (ADR-0041)

Everything above describes `Policy.INFORMATION`, and it optimises the right thing for a young
pool and the wrong thing for a leaderboard. It contains no fairness term at all, so after 123
pairings `pool-free` had **44% pair coverage**, one entrant on 25 pairings and another on 1.

`Policy.BALANCE` — the default — asks a different question: *who is furthest behind?* Take the
entrant with the fewest pairings, then its least-met opponent. It is a greedy incremental round
robin, and over a dynamic field it converges on what a fixed schedule would give without ever
writing one down, which is what a pool needs: its entrants come and go with the catalogue, so
there is no fixture list to freeze.

The driver below — batching, colours, round numbers, availability — is shared. Only the two
comparisons differ, which is the whole reason this is a policy rather than a second function.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from chessmark.tournament.pairing import _colour_balance, ordered
from chessmark.tournament.types import Entrant, Pairing, Result

#: A rating point of separation is worth this much when weighed against a rematch. Set so that any
#: unmet opponent beats any already-met one: no plausible rating gap reaches it.
_REMATCH_PENALTY = 100_000.0


class Policy(StrEnum):
    """Which question the matchmaker asks when it chooses the next game.

    The driver is the same either way. What differs is two comparisons, and the difference between
    them is the difference between a pool that measures well and a pool that measures fairly.
    """

    #: *Who is furthest behind?* Fewest pairings first, then the least-met opponent. A greedy
    #: incremental round robin: over a changing field it converges on the coverage a fixed schedule
    #: would give, without a schedule to invalidate when an entrant joins or leaves.
    BALANCE = "balance"

    #: *Whose next game teaches us most?* Highest rating deviation first, then the nearest-rated
    #: opponent who is not a rematch. Right for a young pool where most entrants are unrated;
    #: wrong for a leaderboard, because it contains no fairness term at all.
    INFORMATION = "information"


@dataclass(frozen=True, slots=True)
class _Board:
    """Everything the policies compare, and nothing else.

    Assembled once per call and mutated as a batch is built, so the second game of a batch sees the
    first. `played` is derived from `met` rather than carried separately: a pairing is a pairing
    whether or not it produced a result, which is the count that matters here and the one that
    keeps counting when a model abandons every game it is given.
    """

    known: dict[str, Form]
    met: dict[frozenset[str], int]
    played: dict[str, int]

    def meetings(self, home: str, away: str) -> int:
        return self.met.get(frozenset({home, away}), 0)

    def record(self, home: str, away: str) -> None:
        pair = frozenset({home, away})
        self.met[pair] = self.met.get(pair, 0) + 1
        self.played[home] = self.played.get(home, 0) + 1
        self.played[away] = self.played.get(away, 0) + 1


#: How a policy ranks the entrant to build a game around, and then its opponent. Both return a
#: sort key for `min`, so ties always fall through to the entrant key and the choice is
#: reproducible — a pool that paired differently on a replay would be untestable.
_HomeKey = Callable[[_Board, str], tuple[object, ...]]
_AwayKey = Callable[[_Board, str, str], tuple[object, ...]]


def _balance_home(board: _Board, key: str) -> tuple[object, ...]:
    return (board.played.get(key, 0), key)


def _balance_away(board: _Board, home: str, key: str) -> tuple[object, ...]:
    # Rating proximity survives as the **last** tie-break, where it costs nothing: among opponents
    # equally unmet and equally under-played, the nearer-rated game is still the better one.
    return (
        board.meetings(home, key),
        board.played.get(key, 0),
        abs(board.known[home].rating - board.known[key].rating),
        key,
    )


def _information_home(board: _Board, key: str) -> tuple[object, ...]:
    return (-board.known[key].deviation, board.played.get(key, 0), key)


def _information_away(board: _Board, home: str, key: str) -> tuple[object, ...]:
    return (
        board.meetings(home, key) * _REMATCH_PENALTY
        + abs(board.known[home].rating - board.known[key].rating),
        key,
    )


_POLICIES: dict[Policy, tuple[_HomeKey, _AwayKey]] = {
    Policy.BALANCE: (_balance_home, _balance_away),
    Policy.INFORMATION: (_information_home, _information_away),
}


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

    #: **There was a `games` counter here and nothing ever set it.** `_form` in
    #: `orchestration/tournament.py` builds every `Form` from a rating and a deviation, so it was
    #: `0` for every entrant in production, for the whole life of the pool — while sitting as the
    #: *second* sort key of the home choice, which therefore broke ties alphabetically rather than
    #: on who had played least. Removed rather than populated: how many times a pair has been put
    #: on the board is already known from `_meetings`, and deriving the count from that keeps one
    #: source instead of two that can disagree (ADR-0041).


def matchmake(
    entrants: Sequence[Entrant],
    results: Sequence[Result],
    form: dict[str, Form],
    *,
    count: int = 1,
    round_number: int = 1,
    unavailable: frozenset[str] | set[str] = frozenset(),
    attempts: Sequence[Pairing] = (),
    policy: Policy = Policy.BALANCE,
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

    `attempts` names pairings that were scheduled and produced **no** result — abandoned, or still
    in flight. They count as meetings, so a fixture the harness could not play is not offered again
    as though it were fresh. It is the complement of `results`, and passing a pairing in both would
    count it twice: `results` is what was settled, `attempts` is what was not.

    Fewer than two available entrants returns no games rather than pairing regardless. The pool
    holds for a tick, which is correct: there is no game worth starting.

    `policy` chooses between the two questions in `Policy`. It defaults to `BALANCE`, which is what
    a public leaderboard needs: `INFORMATION` ran `pool-free` to 44% pair coverage with one entrant
    on 25 pairings and another on 1 (ADR-0041).
    """
    field = [e.key for e in ordered(entrants)]
    if len(field) < 2:
        return []

    met = _meetings(results, attempts)
    balance = _colour_balance(results)
    whites = _whites_against(results, attempts)
    # Form is built over the whole field, not the available subset. A resting entrant's rating is
    # still real and is still what an opponent is chosen for proximity to; only its own turn to
    # play is deferred.
    board = _Board(
        known={key: form.get(key, Form(key=key)) for key in field},
        met=met,
        played=_pairings_each(met),
    )
    home_key, away_key = _POLICIES[policy]

    available = set(field) - set(unavailable)
    games: list[Pairing] = []

    for _ in range(count):
        if len(available) < 2:
            break

        home = min(available, key=lambda key: home_key(board, key))
        away = min(available - {home}, key=lambda key: away_key(board, home, key))

        white, black = _colours(home, away, balance, whites)
        # **One round number per game, not per batch.** Every game in a batch used to carry
        # `round_number` unchanged, which is right for a Swiss round — those games *are* one round,
        # paired together off one set of standings. A pool has no such thing: each game is matched
        # independently, against ratings that the previous game in the same batch has already
        # changed, and the batch size is only however many concurrency slots happened to be free.
        # Calling them a round grouped two unrelated fixtures under one heading, and the schedule
        # showed it — 63 rounds of one game and a single round of two, on the tick after
        # concurrency went to 2.
        games.append(Pairing(white=white, black=black, round_number=round_number + len(games)))

        # Reflect this game before choosing the next, so a batch does not hand the same model
        # White three times, repeat a pairing it just made, or ignore that one of the two is no
        # longer the entrant furthest behind.
        board.record(home, away)
        balance[white] = balance.get(white, 0) + 1
        balance[black] = balance.get(black, 0) - 1
        # The pair ledger too, for the same reason: a batch that paired the same two twice would
        # otherwise read its own first game as never having happened and repeat the colours.
        whites[(white, black)] = whites.get((white, black), 0) + 1
        available -= {home, away}

    return games


def _pairings_each(met: dict[frozenset[str], int]) -> dict[str, int]:
    """How many pairings each entrant has been given, from the meetings table.

    **Pairings, not settled games**, and the distinction is the whole point of balancing on it: a
    model whose endpoint abandons everything still has to take its turn, and counting only what
    finished would send the pool back to it for ever.
    """
    played: dict[str, int] = {}
    for pair, count in met.items():
        for key in pair:
            played[key] = played.get(key, 0) + count
    return played


def _whites_against(
    results: Sequence[Result], attempts: Sequence[Pairing] = ()
) -> dict[tuple[str, str], int]:
    """How many times the first entrant has had White against the second.

    Ordered, unlike `_meetings`, because that is the whole question: a pair can be perfectly
    balanced on *meetings* and still have put the same model on White every time.

    Counts attempts as well as results, for the reason `_meetings` does. A fixture that keeps being
    abandoned is still a fixture that keeps being scheduled one way round, and it is exactly the
    pairing this most needs to catch.
    """
    counts: dict[tuple[str, str], int] = {}
    for result in results:
        if result.black is None:
            continue
        counts[(result.white, result.black)] = counts.get((result.white, result.black), 0) + 1
    for attempt in attempts:
        if attempt.black is None:
            continue
        counts[(attempt.white, attempt.black)] = counts.get((attempt.white, attempt.black), 0) + 1
    return counts


def _colours(
    home: str,
    away: str,
    balance: dict[str, int],
    whites: dict[tuple[str, str], int] | None = None,
) -> tuple[str, str]:
    """White to whoever is more owed it, then to whoever is more owed it *by this opponent*.

    Over a pool that runs for months this matters more than in a single event: White scores
    better, and a model that drew it two thirds of the time would carry a rating partly measuring
    that. The per-entrant balance is therefore the primary rule and stays so — it is the split that
    actually biases a rating, and it is already even to within a game across the live pool.

    **The gap was games that never settle.** A finished game moves both balances, so the next
    meeting of that pair swaps colours on its own — that always worked. An *abandoned* one produces
    no `Result`, moves nothing, and left the comparison level; `_colours` then fell through to
    `return home, away`, so White went to `home`, which under `BALANCE` is
    `min(available, fewest pairings)`. A pairing that kept being abandoned kept being scheduled the
    same way round, and an entrant whose games never settle kept collecting White.

    That is not hypothetical here: `pool-free` has five abandoned pairings in the current era, and
    the entrants furthest behind are the ones whose endpoints rarely serve — so they are `home`
    most often *and* the least likely to produce the result that would correct it.

    Three keys now, each neutral where the one before it is level:

    1. the per-entrant balance, as before;
    2. **this pair's own history** — whoever has had White fewer times against *this* opponent, so a
       rematch alternates without the fixture space having to double;
    3. the key, which decides nothing but the very first meeting of a pair and decides it the same
       way every time. Rule 2 swaps it on the next meeting.
    """
    home_balance, away_balance = balance.get(home, 0), balance.get(away, 0)
    if home_balance != away_balance:
        return (away, home) if home_balance > away_balance else (home, away)

    ledger = whites or {}
    home_whites = ledger.get((home, away), 0)
    away_whites = ledger.get((away, home), 0)
    if home_whites != away_whites:
        return (away, home) if home_whites > away_whites else (home, away)

    return (home, away) if home < away else (away, home)


def _meetings(
    results: Sequence[Result], attempts: Sequence[Pairing] = ()
) -> dict[frozenset[str], int]:
    """How many times each pair has already been put on the board.

    A count rather than a set: a pool runs long enough that every pair eventually meets, so the
    question stops being *whether* and becomes *how recently and how often*.

    **A pairing counts once it is attempted, whether or not it finished.** Reading results alone
    made a fixture that could never complete look permanently unmet — the one shape of pairing the
    penalty most needs to catch, because it is also the one the "least known first" rule keeps
    reaching for. Seven dead `gemma-4-26b` v `gemma-4-31b` schedulings, nought plies between them.
    """
    counts: dict[frozenset[str], int] = {}
    for result in results:
        if result.black is None:
            continue
        pair = frozenset(result.players)
        counts[pair] = counts.get(pair, 0) + 1
    for attempt in attempts:
        if attempt.black is None:
            continue
        counts[attempt.pair] = counts.get(attempt.pair, 0) + 1
    return counts
