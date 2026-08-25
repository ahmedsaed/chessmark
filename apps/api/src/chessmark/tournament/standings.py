"""The table.

Separate from pairing because a standings table is what people argue about, and it should be
computable from the results alone — no scheduler state, no database, nothing that could make two
views of one tournament disagree.

Tiebreaks are **Sonneborn-Berger** then **direct encounter**, both computed here rather than left
to whoever renders the page. Raw score alone leaves ties constantly in a small field, and breaking
them by seed would rank models by the order we happened to fetch them in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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

    @property
    def key(self) -> str:
        return self.entrant.key


def standings(entrants: Sequence[Entrant], results: Sequence[Result]) -> list[Standing]:
    """The table, best first, with places assigned.

    Results naming an entrant outside the field are ignored rather than raising: a tournament may
    be recomputed after a model was withdrawn, and losing the whole table over one stale row would
    be a worse failure than omitting it.
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
        )
        for key in known
    ]

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
