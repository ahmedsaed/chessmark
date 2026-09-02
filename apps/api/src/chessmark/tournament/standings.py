"""The table.

Separate from pairing because a standings table is what people argue about, and it should be
computable from the results alone — no scheduler state, no database, nothing that could make two
views of one tournament disagree.

Tiebreaks are **Sonneborn-Berger** then **direct encounter**, both computed here rather than left
to whoever renders the page. Raw score alone leaves ties constantly in a small field, and breaking
them by seed would rank models by the order we happened to fetch them in.

**Points rank a closed event; a rating ranks a pool.** A round robin or a Swiss gives everybody the
same number of games, which is exactly what makes a sum of points a ranking. A pool does not: it
runs indefinitely, its field changes as the catalogue does, and in `pool-free` entrants had played
between **0 and 10** games. A sum then partly measures how many games a model was handed —
`dots-3-note-preview` won all five of its games and stood third behind a model on 6.0 from eight.
Sonneborn-Berger does not rescue it, being another sum.

So a caller with ratings passes them in and the table is ordered by rating, deviation second. They
arrive as plain numbers rather than as a rating engine, which is what keeps this module pure: it is
told what the standing is, and never how it was arrived at.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from chessmark.tournament.types import Entrant, Result


@dataclass(frozen=True, slots=True)
class Standing:
    entrant: Entrant
    played: int
    wins: int
    draws: int
    losses: int
    byes: int
    score: float
    #: Sum of the scores of every opponent beaten, plus half of each opponent drawn with.
    sonneborn_berger: float
    place: int = 0
    #: Glicko-2 over this event's games alone, when the caller computed one. `None` for a closed
    #: event, where points are the answer and a rating would be a second one nobody asked for.
    rating: float | None = None
    #: The honest half. A rating without it says "1650" where the truth is "1650, and we have seen
    #: four games".
    rating_deviation: float | None = None
    #: Whether that rating is still too unsure to read as a placing. The threshold belongs to the
    #: rating system, not to a table, so the caller decides it and hands the answer in — the same
    #: bargain as the rating itself, and what keeps this module free of `bench`.
    rating_provisional: bool = False

    @property
    def key(self) -> str:
        return self.entrant.key


def standings(
    entrants: Sequence[Entrant],
    results: Sequence[Result],
    ratings: Mapping[str, tuple[float, float, bool]] | None = None,
) -> list[Standing]:
    """The table, best first, with places assigned.

    Results naming an entrant outside the field are ignored rather than raising: a tournament may
    be recomputed after a model was withdrawn, and losing the whole table over one stale row would
    be a worse failure than omitting it.

    `ratings` maps an entrant key to `(rating, deviation, provisional)` and, when given, decides
    the order:
    highest rating first, then narrowest deviation, then seed. Pass it for a pool, where games
    played are unequal and a sum of points ranks partly by volume; omit it for a closed event,
    where every entrant plays the same schedule and points are what the event is for.

    An entrant the ratings do not name — one who has not yet completed a ratable game — sorts last
    rather than at 1500. An unrated model is not an average model; it is an unmeasured one, and
    seating it mid-table would be the same claim the deviation exists to avoid making.
    """
    known = {e.key: e for e in entrants}
    relevant = [r for r in results if all(k in known for k in r.players)]

    scores = dict.fromkeys(known, 0.0)
    played = dict.fromkeys(known, 0)
    wins = dict.fromkeys(known, 0)
    draws = dict.fromkeys(known, 0)
    losses = dict.fromkeys(known, 0)
    byes = dict.fromkeys(known, 0)

    for result in relevant:
        if result.black is None:
            byes[result.white] += 1
            scores[result.white] += result.white_score
            continue
        for key in result.players:
            got = result.score_for(key)
            scores[key] += got
            played[key] += 1
            if got == 1.0:
                wins[key] += 1
            elif got == 0.5:
                draws[key] += 1
            else:
                losses[key] += 1

    table = [
        Standing(
            entrant=known[key],
            played=played[key],
            wins=wins[key],
            draws=draws[key],
            losses=losses[key],
            byes=byes[key],
            score=scores[key],
            sonneborn_berger=_sonneborn_berger(key, relevant, scores),
            rating=ratings[key][0] if ratings and key in ratings else None,
            rating_deviation=ratings[key][1] if ratings and key in ratings else None,
            rating_provisional=ratings[key][2] if ratings and key in ratings else False,
        )
        for key in known
    ]

    if ratings is not None:
        # `math.inf` for the unrated, so they sort last under a descending rating rather than
        # landing wherever 1500 happens to fall in this field.
        table.sort(
            key=lambda s: (
                -(s.rating if s.rating is not None else -math.inf),
                s.rating_deviation if s.rating_deviation is not None else math.inf,
                s.entrant.seed,
                s.key,
            )
        )
        return [_placed_by_rating(standing, index, table) for index, standing in enumerate(table)]

    table.sort(key=lambda s: (-s.score, -s.sonneborn_berger, s.entrant.seed, s.key))
    table = _break_ties_head_to_head(table, relevant)
    return [_placed(standing, index, table) for index, standing in enumerate(table)]


def _break_ties_head_to_head(table: list[Standing], results: Sequence[Result]) -> list[Standing]:
    """Reorder entrants level on score *and* Sonneborn-Berger by their game against each other.

    Applied per tied group rather than across the field — points taken from everyone is just the
    score again, which would make this tiebreak do nothing at all.
    """
    ordered: list[Standing] = []
    index = 0
    while index < len(table):
        end = index + 1
        while end < len(table) and (table[end].score, table[end].sonneborn_berger) == (
            table[index].score,
            table[index].sonneborn_berger,
        ):
            end += 1

        group = table[index:end]
        if len(group) > 1:
            peers = {s.key for s in group}
            group.sort(key=lambda s: (-_against(s.key, peers, results), s.entrant.seed, s.key))
        ordered.extend(group)
        index = end
    return ordered


def _against(key: str, peers: set[str], results: Sequence[Result]) -> float:
    """Points this entrant took from a given set of opponents."""
    total = 0.0
    for result in results:
        if result.black is None or key not in result.players:
            continue
        opponent = result.black if key == result.white else result.white
        if opponent in peers and opponent != key:
            total += result.score_for(key)
    return total


def _placed(standing: Standing, index: int, table: list[Standing]) -> Standing:
    """Assign a place, sharing it when two entrants are genuinely inseparable.

    Two models on the same score *and* the same tiebreaks are joint — printing 3rd and 4th would
    claim a distinction the games did not produce.
    """
    place = index + 1
    for earlier in table[:index]:
        if (earlier.score, earlier.sonneborn_berger) == (
            standing.score,
            standing.sonneborn_berger,
        ):
            place = table.index(earlier) + 1
            break
    return Standing(
        entrant=standing.entrant,
        played=standing.played,
        wins=standing.wins,
        draws=standing.draws,
        losses=standing.losses,
        byes=standing.byes,
        score=standing.score,
        sonneborn_berger=standing.sonneborn_berger,
        place=place,
    )


def _placed_by_rating(standing: Standing, index: int, table: list[Standing]) -> Standing:
    """Assign a place under a rating order.

    No joint places. Two ratings are floats computed from different games and will not be equal
    except by accident, so sharing a place on exact equality would claim an inseparability the
    arithmetic never actually found — the opposite of what `_placed` does for scores, where a tie
    on 5.0 is a real tie two models genuinely produced.

    The unrated do share one, and last: an entrant with no completed ratable game is not ranked
    below another such entrant, it is simply not ranked.
    """
    if standing.rating is None:
        first_unrated = next(i for i, s in enumerate(table) if s.rating is None)
        return replace(standing, place=first_unrated + 1)
    return replace(standing, place=index + 1)


def _sonneborn_berger(key: str, results: Sequence[Result], scores: dict[str, float]) -> float:
    """Beating strong opponents counts for more than beating weak ones.

    A bye contributes nothing: there was no opponent, so there is no strength to credit.
    """
    total = 0.0
    for result in results:
        if result.black is None or key not in result.players:
            continue
        opponent = result.black if key == result.white else result.white
        got = result.score_for(key)
        if got == 1.0:
            total += scores.get(opponent or "", 0.0)
        elif got == 0.5:
            total += scores.get(opponent or "", 0.0) / 2
    return total
