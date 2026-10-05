#!/usr/bin/env python3
"""The site's rating beside Glicko-2, the method it replaced, over the same games (ADR-0060).

    make compare-ratings                       # the leaderboard's games, then every pool's
    make compare-ratings ARGS="--pool pool-free"
    make compare-ratings ARGS="--shuffles 500 --prior 350 --prior 200"   # fixed widths, not estimated

Read-only, and it changes nothing the site shows. It is the evidence behind ADR-0060, kept so the
answer to "why two imaginary draws?" and "why not Glicko-2?" can be re-run on whatever the games
are today, rather than taken from a table in a document.

The question it was written for: **is Glicko-2 the right model for contestants that never change
strength and often stop playing?** A model is a fixed set of weights, and a paid one may play six
games in one event and never return. Glicko-2's machinery for time (rating periods, a deviation
that widens while idle, volatility) models a drift that does not happen here, and as a consequence
its answer depends on the order the games were played in.

Per contestant, over exactly the games the leaderboard counts (`bench.service.scan`, so the
eligibility rules are the site's own, not a re-implementation):

* **Site** — the rating the site publishes and the place it gives (Bradley-Terry, two imaginary
  draws, ordered by proven strength), checked against `compute_ratings` so a mismatch is a loud
  failure rather than a quiet misreport.
* **Glicko-2** — the method it replaced, from `glicko2_reference.py`, which is the deleted
  `bench/glicko2.py` unchanged. Its replay matched the site's published numbers exactly on
  production's data on 2026-10-05, the last day it was the site's engine.
* **Order** — the range of places Glicko-2 gives the same contestant when the *same games* are
  dealt into the same periods in a different order. A rating of a fixed thing should not have one.
* **Bradley-Terry with a normal prior**, at each width — the estimate of how wide the prior should
  be, printed two independent ways so the choice is visible rather than assumed.

All use the Elo logistic scale (400 points = 10:1 odds) and treat a draw as half a win, so the
numbers are on the same scale and differ only in the model.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import math
import random
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from glicko2_reference import Glicko2, Outcome  # noqa: E402
from glicko2_reference import Rating as GlickoRating  # noqa: E402

from chessmark.bench import fit as site_fit  # noqa: E402
from chessmark.bench import standing_key  # noqa: E402
from chessmark.bench.service import (  # noqa: E402
    Contestant,
    _contestant,
    _score,
    compute_ratings,
    scan,
)
from chessmark.db.models import Tournament  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402
from chessmark.game import Colour  # noqa: E402

#: Elo's scale: a 400-point gap is 10:1 odds. Both systems are reported on it.
ELO = 400.0 / math.log(10)
CENTRE = 1500.0
PERIOD_EPOCH = dt.date(2026, 1, 1)


def period_of(when: dt.datetime) -> int:
    """Glicko-2's rating period: one UTC day, as the site used it."""
    return (when.astimezone(dt.UTC).date() - PERIOD_EPOCH).days


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


@dataclass(slots=True)
class Fit:
    players: list[Contestant]
    #: Strengths on the natural-log scale, centred on 0.
    theta: list[float]
    #: The negative Hessian of the log posterior at `theta` — the posterior precision.
    precision: list[list[float]]
    #: Log likelihood of the games at `theta`, without the prior.
    loglik: float
    tau2: float


def fit(games: list[Played], prior_sd: float, start: dict[Contestant, float] | None = None) -> Fit:
    """MAP strengths under a normal prior, by Newton's method on the log posterior.

    The prior makes it strictly concave, so it converges from zero in a handful of steps and needs
    no connected comparison graph — a contestant whose games are all against one opponent still
    gets an answer, and a wide one. A draw is half a win, as Glicko-2 and Elo both treat it, so the
    two systems differ only in the model and not in what a result means.
    """
    players = sorted({c for g in games for c in (g.white, g.black)}, key=label)
    index = {c: i for i, c in enumerate(players)}
    n = len(players)
    tau2 = (prior_sd / ELO) ** 2
    theta = [(start or {}).get(c, 0.0) for c in players]

    for _ in range(100):
        grad = [-t / tau2 for t in theta]
        precision = [[(1.0 / tau2 if i == j else 0.0) for j in range(n)] for i in range(n)]
        for g in games:
            i, j = index[g.white], index[g.black]
            p = 1.0 / (1.0 + math.exp(theta[j] - theta[i]))
            grad[i] += g.score - p
            grad[j] -= g.score - p
            w = p * (1.0 - p)
            precision[i][i] += w
            precision[j][j] += w
            precision[i][j] -= w
            precision[j][i] -= w
        step = solve(precision, grad)
        theta = [t + s for t, s in zip(theta, step, strict=True)]
        if max(abs(s) for s in step) < 1e-10:
            break

    loglik = 0.0
    for g in games:
        p = 1.0 / (1.0 + math.exp(theta[index[g.black]] - theta[index[g.white]]))
        loglik += g.score * math.log(p) + (1.0 - g.score) * math.log(1.0 - p)
    return Fit(players, theta, precision, loglik, tau2)


def bradley_terry(games: list[Played], prior_sd: float) -> dict[Contestant, tuple[float, float]]:
    """Strengths and their Laplace standard deviations, in rating points."""
    result = fit(games, prior_sd)
    covariance = inverse(result.precision)
    return {
        c: (CENTRE + result.theta[i] * ELO, math.sqrt(covariance[i][i]) * ELO)
        for i, c in enumerate(result.players)
    }


# --- Choosing the prior's width from the games ----------------------------------------------------

#: The widths tried, in rating points. 350 is Glickman's starting deviation, 500 Lichess's.
GRID = (50, 75, 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 500, 650, 800)


def evidence(games: list[Played], prior_sd: float) -> float:
    """Log marginal likelihood of the games under this prior width (Laplace approximation).

    How probable the observed results are, averaged over every set of strengths the prior allows.
    Too narrow and real differences cannot be explained; too wide and the prior spreads its belief
    over strengths nothing needed. The peak is the width the games themselves argue for, which is
    the point: the width decides who tops the table, so it must not be picked by taste.
    """
    result = fit(games, prior_sd)
    n = len(result.players)
    penalty = sum(t * t for t in result.theta) / (2 * result.tau2)
    return (
        result.loglik
        - penalty
        - 0.5 * n * math.log(result.tau2)
        - 0.5 * log_determinant(result.precision)
    )


def held_out_loss(games: list[Played], prior_sd: float) -> float:
    """Mean log loss predicting each game from a fit to all the others.

    A second estimate that assumes nothing about the Laplace approximation: the width that best
    predicts results it has not seen. If the two agree, the choice is not an artefact of either
    method. Each fit starts from the full fit, so it converges in a step or two.
    """
    full = fit(games, prior_sd)
    start = dict(zip(full.players, full.theta, strict=True))
    loss = 0.0
    for k, held in enumerate(games):
        rest = fit(games[:k] + games[k + 1 :], prior_sd, start)
        strength = dict(zip(rest.players, rest.theta, strict=True))
        diff = strength.get(held.white, 0.0) - strength.get(held.black, 0.0)
        p = 1.0 / (1.0 + math.exp(-diff))
        loss -= held.score * math.log(p) + (1.0 - held.score) * math.log(1.0 - p)
    return loss / len(games)


def estimate(games: list[Played]) -> tuple[float, float]:
    """The width each method prefers, after printing both curves."""
    rows = [(sd, evidence(games, sd), held_out_loss(games, sd)) for sd in GRID]
    by_evidence = max(rows, key=lambda r: r[1])[0]
    by_held_out = min(rows, key=lambda r: r[2])[0]
    print("| prior width | log evidence | held-out log loss |")
    print("|---|---|---|")
    for sd, ev, loss in rows:
        marks = (" ◀ evidence" if sd == by_evidence else "") + (
            " ◀ held-out" if sd == by_held_out else ""
        )
        print(f"| {sd} | {ev:.2f} | {loss:.4f}{marks} |")
    # A coin flip scores ln 2 ≈ 0.6931; a fit that cannot beat it is not finding anything.
    print(f"\nA coin flip's held-out loss is {math.log(2):.4f}.\n")
    return float(by_evidence), float(by_held_out)


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


def log_determinant(a: list[list[float]]) -> float:
    """Of a positive-definite matrix: the sum of the logs of its elimination pivots."""
    n = len(a)
    m = [row[:] for row in a]
    total = 0.0
    for col in range(n):
        total += math.log(m[col][col])
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            m[r] = [x - f * y for x, y in zip(m[r], m[col], strict=True)]
    return total


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

    # The column labelled "site" must be the site's number, or every comparison below is against
    # the wrong thing.
    mine = site_fit([(g.white, g.black, g.score) for g in games])
    for contestant, rating in site.ratings.items():
        ours = mine[contestant]
        if abs(ours.rating - rating.rating) > 1e-6 or abs(ours.rd - rating.rd) > 1e-6:
            raise SystemExit(f"site column disagrees with compute_ratings for {label(contestant)}")
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
    print(f"\n## {title}\n")
    if not priors:
        by_evidence, by_held_out = estimate(games)
        priors = sorted({by_evidence, by_held_out}, reverse=True)
    published = site_fit([(g.white, g.black, g.score) for g in games])
    site_order = sorted(published, key=lambda c: (*standing_key(published[c]), label(c)))
    site_place = {c: i + 1 for i, c in enumerate(site_order)}
    old = glicko(games)
    g_place = places({c: r.rating for c, r in old.items()})
    spread = order_range(games, shuffles, seed=1)
    fits = [bradley_terry(games, prior) for prior in priors]
    bt_place = [places({c: v[0] for c, v in fit.items()}) for fit in fits]

    print(
        f"{len(games)} games, {len(published)} contestants, "
        f"{len({g.period for g in games})} Glicko-2 periods.\n"
    )
    header = ["Site #", "Contestant", "W/D/L", "Site", "proven", "Glicko #", "Glicko-2"]
    header += [f"order ({shuffles})"]
    header += [f"BT prior {p:.0f}" for p in priors]
    header += [f"BT # @{p:.0f}" for p in priors]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for c in site_order:
        w, d, lo = record[c]
        best, worst = spread[c]
        row = [
            str(site_place[c]),
            label(c),
            f"{w}/{d}/{lo}",
            f"{published[c].rating:.0f} ± {published[c].rd:.0f}",
            f"{published[c].proven:.0f}",
            str(g_place[c]),
            f"{old[c].rating:.0f} ± {old[c].rd:.0f}",
            f"{best}" if best == worst else f"{best}-{worst}",
        ]
        row += [f"{fit[c][0]:.0f} ± {fit[c][1]:.0f}" for fit in fits]
        row += [str(p[c]) for p in bt_place]
        print("| " + " | ".join(row) + " |")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pool", action="append", help="a tournament slug; repeatable")
    parser.add_argument(
        "--prior",
        type=float,
        action="append",
        help="prior sd in rating points; repeatable. Omitted, it is estimated from the games",
    )
    parser.add_argument("--shuffles", type=int, default=200)
    args = parser.parse_args()
    # Empty means "estimate it from each scope's own games", which is the default because the
    # width is the decision under examination and should not be one this script makes.
    priors: list[float] = args.prior or []

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
