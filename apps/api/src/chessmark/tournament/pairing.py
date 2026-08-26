"""Who plays whom, and with which colour.

Pure: pairings are decided from a field, the results so far, and nothing else. That makes the
awkward cases — an odd field, a rematch that must be avoided, a player owed White — testable
against hand-computed fixtures, which is the only way anyone will believe a standings table.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from chessmark.tournament.types import Entrant, Format, Pairing, Result, TournamentConfig

#: The placeholder that sits in the circle when the field is odd. Whoever faces it takes a bye.
_BYE = None


def ordered(entrants: Iterable[Entrant]) -> list[Entrant]:
    """Seeded order: by seed, then by key so a tie is still deterministic."""
    return sorted(entrants, key=lambda e: (e.seed, e.key))


# ====================================================================== round robin


def round_robin(entrants: Sequence[Entrant], *, double: bool = False) -> list[list[Pairing]]:
    """Every entrant against every other, as a list of rounds.

    The circle method: fix the first player and rotate the rest. An odd field gets a phantom, and
    whoever draws it sits that round out — so with `n` odd there are `n` rounds of `(n-1)/2` games
    rather than a shorter schedule that would leave someone playing twice in a round.

    Colours are assigned afterwards rather than by a parity rule on the slot or the round. Every
    such rule that looks reasonable leaves somebody badly lopsided — slot parity gave one entrant
    White twice and Black eleven times in a field of twelve — because the circle rotates players
    past positions at different rates. Assigning greedily and then repairing reaches the best
    achievable spread: one for an even field, where the odd number of rounds makes zero impossible.

    `double` replays the schedule with colours reversed, which balances them exactly and is the
    only honest way to compare two models when White scores better than Black.
    """
    field: list[str | None] = [e.key for e in ordered(entrants)]
    if len(field) < 2:
        return []
    if len(field) % 2:
        field.append(_BYE)

    half = len(field) // 2
    rotating = field[1:]
    draft: list[list[list[str | None]]] = []

    for _ in range(len(field) - 1):
        arrangement = [field[0], *rotating]
        games: list[list[str | None]] = []
        for slot in range(half):
            home, away = arrangement[slot], arrangement[-(slot + 1)]
            if home is None:
                home, away = away, home
            games.append([home, away])
        draft.append(games)
        rotating = [rotating[-1], *rotating[:-1]]

    _assign_colours(draft)
    _repair_colours(draft)

    rounds = [
        [Pairing(white=str(game[0]), black=game[1], round_number=number + 1) for game in games]
        for number, games in enumerate(draft)
    ]

    if double:
        # `first_half` is materialised deliberately. Passing a generator over `rounds` to
        # `rounds.extend` reads the list while appending to it, which never terminates and
        # allocates until the machine gives up — it took an editor down before it was caught.
        first_half = list(rounds)
        played = len(first_half)
        rounds.extend(
            [
                Pairing(
                    white=game.black if game.black is not None else game.white,
                    black=game.white if game.black is not None else None,
                    round_number=played + number + 1,
                )
                for game in games
            ]
            for number, games in enumerate(first_half)
        )

    return rounds


def _assign_colours(draft: list[list[list[str | None]]]) -> None:
    """First pass: give White to whoever is currently more owed it."""
    balance: dict[str, int] = defaultdict(int)
    for games in draft:
        for game in games:
            home, away = game
            if away is None or home is None:
                continue
            if balance[home] > balance[away]:
                game[0], game[1] = away, home
                home, away = away, home
            balance[home] += 1
            balance[away] -= 1


def _repair_colours(draft: list[list[list[str | None]]], *, passes: int = 50) -> None:
    """Second pass: flip any game that reduces the overall spread.

    The greedy pass is myopic — it cannot know that a colour handed out early will be needed
    later — and on its own leaves an imbalance of three in a field of twelve. Local search over
    single flips closes that to one; it is bounded and converges in a handful of passes at these
    field sizes.
    """
    for _ in range(passes):
        balance = _balance_of(draft)
        cost = sum(value * value for value in balance.values())
        improved = False

        for games in draft:
            for game in games:
                home, away = game
                if away is None or home is None:
                    continue
                candidate = dict(balance)
                candidate[home] -= 2
                candidate[away] += 2
                if sum(v * v for v in candidate.values()) < cost:
                    game[0], game[1] = away, home
                    balance = defaultdict(int, candidate)
                    cost = sum(v * v for v in balance.values())
                    improved = True

        if not improved:
            return


def _balance_of(draft: list[list[list[str | None]]]) -> dict[str, int]:
    balance: dict[str, int] = defaultdict(int)
    for games in draft:
        for home, away in games:
            if away is None or home is None:
                continue
            balance[home] += 1
            balance[away] -= 1
    return balance


# ====================================================================== swiss


def swiss_round(
    entrants: Sequence[Entrant],
    results: Sequence[Result],
    round_number: int,
) -> list[Pairing]:
    """One Swiss round, paired from the standings so far.

    Players are sorted by score and paired within score groups, which is what makes Swiss work:
    the leaders keep meeting each other, so a winner emerges without everyone playing everyone.

    A rematch is never issued while any alternative exists — that is the constraint that makes
    naive greedy pairing wrong, and why this backtracks. With a field of tens the search is
    trivially small; a field where it is not would be a field that cannot afford Swiss anyway.
    """
    played = _met(results)
    scores = _scores(entrants, results)
    field = sorted(ordered(entrants), key=lambda e: (-scores[e.key], e.seed, e.key))

    keys = [e.key for e in field]
    bye: str | None = None
    if len(keys) % 2:
        bye = _bye_for(keys, results)
        keys.remove(bye)

    matched = _match(keys, played)
    if matched is None:
        # Every remaining pairing is a rematch. Replaying one is better than stalling the
        # tournament, and it is recorded honestly rather than hidden.
        matched = _match(keys, set())
    if matched is None:  # pragma: no cover - only reachable with an empty field
        matched = []

    games = [_with_colours(home, away, results, round_number) for home, away in matched]
    if bye is not None:
        games.append(Pairing(white=bye, black=None, round_number=round_number))
    return games


def _match(keys: list[str], played: set[frozenset[str]]) -> list[tuple[str, str]] | None:
    """Pair the list in order, backtracking past pairs that have already met."""
    if not keys:
        return []

    home, rest = keys[0], keys[1:]
    for index, away in enumerate(rest):
        if frozenset({home, away}) in played:
            continue
        remainder = rest[:index] + rest[index + 1 :]
        tail = _match(remainder, played)
        if tail is not None:
            return [(home, away), *tail]
    return None


def _bye_for(keys: list[str], results: Sequence[Result]) -> str:
    """The bye goes to the lowest-placed entrant who has not already had one.

    A second bye to the same player would hand them a free point twice, which distorts a
    standings table far more than an unlucky pairing does.
    """
    had = {r.white for r in results if r.black is None}
    for key in reversed(keys):
        if key not in had:
            return key
    return keys[-1]


def _with_colours(home: str, away: str, results: Sequence[Result], round_number: int) -> Pairing:
    """Give White to whoever is more owed it.

    Colour matters — White scores better — so over a Swiss event the allocation has to even out,
    or the standings partly measure who drew the lucky side.
    """
    balance = _colour_balance(results)
    if balance[home] > balance[away]:
        home, away = away, home
    return Pairing(white=home, black=away, round_number=round_number)


def _colour_balance(results: Sequence[Result]) -> dict[str, int]:
    """Whites minus blacks, per entrant. Positive means they have had White more often."""
    balance: dict[str, int] = defaultdict(int)
    for result in results:
        if result.black is None:
            continue
        balance[result.white] += 1
        balance[result.black] -= 1
    return balance


def _met(results: Sequence[Result]) -> set[frozenset[str]]:
    return {frozenset(r.players) for r in results if r.black is not None}


def _scores(entrants: Sequence[Entrant], results: Sequence[Result]) -> dict[str, float]:
    scores = {e.key: 0.0 for e in entrants}
    for result in results:
        for key in result.players:
            if key in scores:
                scores[key] += result.score_for(key)
    return scores


# ====================================================================== the schedule


def schedule(entrants: Sequence[Entrant], config: TournamentConfig) -> list[list[Pairing]]:
    """Every round a round robin will play, or the first Swiss round.

    Swiss cannot be scheduled ahead: round two depends on round one's results. The runner asks for
    each round as the previous one finishes, which is also what makes a tournament resumable — the
    schedule is derived from what has been played, never from where a crashed process had got to.
    """
    if config.format is Format.ROUND_ROBIN:
        return round_robin(entrants, double=config.double)
    return [swiss_round(entrants, [], 1)]
