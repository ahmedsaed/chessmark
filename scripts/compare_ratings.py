#!/usr/bin/env python3
"""Glicko-2 as the site computes it, beside a Bradley-Terry fit over the same games.

    make compare-ratings                       # the leaderboard's games, then every pool's
    make compare-ratings ARGS="--pool pool-free"
    make compare-ratings ARGS="--shuffles 500 --prior 350 --prior 200"

Read-only, and it changes nothing the site shows. It exists to answer one question with
production's numbers rather than an argument: **is Glicko-2 the right model for contestants that
never change strength and often stop playing?** A model is a fixed set of weights — it does not
improve between Tuesday and Friday — and a paid one may play six games in one event and never
again. Glicko-2's machinery for time (rating periods, a deviation that widens while idle,
volatility) models a drift that does not happen here, and as a consequence its answer depends on
the order the games were played in.

Three columns per contestant, all over exactly the games the leaderboard counts (`bench.service
.scan`, so the eligibility rules are the site's own, not a re-implementation):

* **Glicko-2** — the site's number, recomputed and checked against `compute_ratings` so a mismatch
  is a loud failure rather than a quiet misreport.
* **Order** — the range of places Glicko-2 gives the same contestant when the *same games* are
  dealt into the same periods in a different order. A rating of a fixed thing should not have one.
* **Bradley-Terry** — every game at once, no dates, with a normal prior on strength (MAP estimate,
  Laplace approximation for the ±). The prior is what lets a 3/0/0 record have a finite rating; it
  is printed at each `--prior` width so its influence is visible rather than assumed.

Both use the Elo logistic scale (400 points = 10:1 odds) and treat a draw as half a win, so the
numbers are on the same scale and differ only in the model.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.bench.glicko2 import Glicko2, Outcome  # noqa: E402
from chessmark.bench.glicko2 import Rating as GlickoRating  # noqa: E402
from chessmark.bench.service import (  # noqa: E402
    Contestant,
    _contestant,
    _score,
    compute_ratings,
    period_of,
    scan,
)
from chessmark.db.models import Tournament  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402
from chessmark.game import Colour  # noqa: E402

#: Elo's scale: a 400-point gap is 10:1 odds. Both systems are reported on it.
ELO = 400.0 / math.log(10)
CENTRE = 1500.0


@dataclass(frozen=True, slots=True)
class Played:
    period: int
    white: Contestant
    black: Contestant
    #: White's score: 1, 0.5 or 0.
    score: float


def label(c: Contestant) -> str:
    return c.model_slug if c.quantization == "unknown" else f"{c.model_slug}@{c.quantization}"


# --- Glicko-2, replayed exactly as `compute_ratings` does it -------------------------------------


def glicko(games: list[Played]) -> dict[Contestant, GlickoRating]:
    """`compute_ratings`' loop over plain tuples, so it can be re-run on a shuffled order."""
    system = Glicko2()
    ratings: dict[Contestant, GlickoRating] = {}
    by_period: dict[int, list[Played]] = {}
    for game in games:
        by_period.setdefault(game.period, []).append(game)
    for period in sorted(by_period):
        outcomes: dict[Contestant, list[Outcome]] = {c: [] for c in ratings}
        for game in by_period[period]:
            for me, other, score in (
                (game.white, game.black, game.score),
                (game.black, game.white, 1.0 - game.score),
            ):
                outcomes.setdefault(me, []).append(
                    Outcome(opponent=ratings.get(other, GlickoRating()), score=score)
                )
        for contestant, results in outcomes.items():
            ratings[contestant] = system.rate(ratings.get(contestant, GlickoRating()), results)
    return ratings


def places(scores: dict[Contestant, float]) -> dict[Contestant, int]:
    ordered = sorted(scores, key=lambda c: -scores[c])
    return {c: i + 1 for i, c in enumerate(ordered)}


def order_range(games: list[Played], shuffles: int, seed: int) -> dict[Contestant, tuple[int, int]]:
    """Each contestant's best and worst Glicko-2 place over `shuffles` re-orderings.

    The periods keep their sizes and their dates; only *which* game falls in which period moves.
    So every run has the same games, the same number of periods and the same idle stretches —
    the only thing that differs is the order, which is exactly what a fixed model's rating should
    not depend on.
    """
    rng = random.Random(seed)
    slots = [g.period for g in games]
    best: dict[Contestant, int] = {}
    worst: dict[Contestant, int] = {}
    for _ in range(shuffles):
        dealt = games[:]
        rng.shuffle(dealt)
        reordered = [
            Played(period=p, white=g.white, black=g.black, score=g.score)
            for p, g in zip(slots, dealt, strict=True)
        ]
        ranks = places({c: r.rating for c, r in glicko(reordered).items()})
        for c, place in ranks.items():
            best[c] = min(best.get(c, place), place)
            worst[c] = max(worst.get(c, place), place)
    return {c: (best[c], worst[c]) for c in best}


# --- Bradley-Terry with a normal prior ------------------------------------------------------------


def bradley_terry(games: list[Played], prior_sd: float) -> dict[Contestant, tuple[float, float]]:
    """MAP strengths and their Laplace standard deviations, in rating points.

    Newton's method on the log posterior. The prior makes it strictly concave, so it converges from
    zero in a handful of steps and needs no connected comparison graph — a contestant whose games
    are all against one opponent still gets an answer, and a wide one.
    """
    players = sorted({c for g in games for c in (g.white, g.black)}, key=label)
    index = {c: i for i, c in enumerate(players)}
    n = len(players)
    tau2 = (prior_sd / ELO) ** 2
    theta = [0.0] * n

    for _ in range(100):
        grad = [-t / tau2 for t in theta]
        hess = [[(-1.0 / tau2 if i == j else 0.0) for j in range(n)] for i in range(n)]
        for g in games:
            i, j = index[g.white], index[g.black]
            p = 1.0 / (1.0 + math.exp(theta[j] - theta[i]))
            grad[i] += g.score - p
            grad[j] -= g.score - p
            w = p * (1.0 - p)
            hess[i][i] -= w
            hess[j][j] -= w
            hess[i][j] += w
            hess[j][i] += w
        step = solve(hess, grad)
        theta = [t - s for t, s in zip(theta, step, strict=True)]
        if max(abs(s) for s in step) < 1e-10:
            break

    covariance = inverse([[-h for h in row] for row in hess])
    return {
        c: (CENTRE + theta[i] * ELO, math.sqrt(covariance[i][i]) * ELO) for c, i in index.items()
    }


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. n is a few dozen; numpy is not a dependency."""
    n = len(b)
    m = [[*row, b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def inverse(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    columns = [solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[columns[j][i] for j in range(n)] for i in range(n)]


# --- Reading the games, and the table -------------------------------------------------------------


async def read(tournament_id: uuid.UUID | None) -> tuple[list[Played], dict[Contestant, list[int]]]:
    async with session_scope() as session:
        scanned = await scan(session, tournament_id=tournament_id)
        site = await compute_ratings(session, tournament_id=tournament_id, scanned=scanned)

    games: list[Played] = []
    record: dict[Contestant, list[int]] = {}
    for game, players, quantizations in scanned.counted:
        seats = {p.colour: _contestant(p, quantizations) for p in players}
        white, black = seats.get(Colour.WHITE), seats.get(Colour.BLACK)
        if white is None or black is None:
            continue
        score = _score(game.result, Colour.WHITE)
        games.append(Played(period_of(game.ended_at or game.created_at), white, black, score))
        for me, s in ((white, score), (black, 1.0 - score)):
            wdl = record.setdefault(me, [0, 0, 0])
            wdl[0 if s == 1.0 else 1 if s == 0.5 else 2] += 1

    # The replay must be the site's number, or every comparison below is against the wrong thing.
    mine = glicko(games)
    for contestant, rating in site.ratings.items():
        ours = mine[contestant]
        if abs(ours.rating - rating.rating) > 1e-6 or abs(ours.rd - rating.rd) > 1e-6:
            raise SystemExit(f"replay disagrees with compute_ratings for {label(contestant)}")
    return games, record


def report(
    title: str,
    games: list[Played],
    record: dict[Contestant, list[int]],
    priors: list[float],
    shuffles: int,
) -> None:
    if not games:
        print(f"\n## {title}\n\nNo games count here.\n")
        return
    site = glicko(games)
    g_place = places({c: r.rating for c, r in site.items()})
    spread = order_range(games, shuffles, seed=1)
    fits = [bradley_terry(games, prior) for prior in priors]
    bt_place = [places({c: v[0] for c, v in fit.items()}) for fit in fits]

    print(f"\n## {title}\n")
    print(
        f"{len(games)} games, {len(site)} contestants, {len({g.period for g in games})} periods.\n"
    )
    header = ["BT #", "Glicko #", "Contestant", "W/D/L", "Glicko-2", f"order ({shuffles})"]
    header += [f"BT prior {p:.0f}" for p in priors]
    header += [f"BT # @{p:.0f}" for p in priors[1:]]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for c in sorted(site, key=lambda c: bt_place[0][c]):
        w, d, lo = record[c]
        best, worst = spread[c]
        row = [
            str(bt_place[0][c]),
            str(g_place[c]),
            label(c),
            f"{w}/{d}/{lo}",
            f"{site[c].rating:.0f} ± {site[c].rd:.0f}",
            f"{best}" if best == worst else f"{best}-{worst}",
        ]
        row += [f"{fit[c][0]:.0f} ± {fit[c][1]:.0f}" for fit in fits]
        row += [str(p[c]) for p in bt_place[1:]]
        print("| " + " | ".join(row) + " |")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pool", action="append", help="a tournament slug; repeatable")
    parser.add_argument("--prior", type=float, action="append", help="prior sd, rating points")
    parser.add_argument("--shuffles", type=int, default=200)
    args = parser.parse_args()
    priors = args.prior or [350.0, 200.0]

    try:
        games, record = await read(None)
        report("Leaderboard — every counted game", games, record, priors, args.shuffles)

        async with session_scope() as session:
            query = sa.select(Tournament.id, Tournament.slug).where(Tournament.format == "pool")
            if args.pool:
                query = query.where(Tournament.slug.in_(args.pool))
            pools = (await session.execute(query.order_by(Tournament.slug))).all()
        for tournament_id, slug in pools:
            games, record = await read(tournament_id)
            report(f"Pool `{slug}` — its own games", games, record, priors, args.shuffles)
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
