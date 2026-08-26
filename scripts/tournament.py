#!/usr/bin/env python3
"""Create, run and inspect tournaments.

    # who would enter, without creating anything
    make tournament ARGS="field --free"

    # create an event
    make tournament ARGS="create --name 'Free models' --slug free-1 --free \\
        --format swiss --rounds 5 --max-concurrent 1"

    # tick it along — a worker must be running to actually play the games
    make tournament ARGS="run free-1"
    make tournament ARGS="standings free-1"

**This schedules; it does not play.** Turns are played by `scripts/worker.py`, which must be
running separately — one of them, because a job goes to whichever worker reaches it first.

`run` is a loop of `advance` calls and holds nothing between them, so stopping it with Ctrl-C and
starting it again tomorrow costs nothing: the schedule is on disk, and the next step is derived
from what has been played.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db import tournaments as repo  # noqa: E402
from chessmark.db.enums import GameStatus, TournamentStatus  # noqa: E402
from chessmark.db.models import Game, Tournament, TournamentEntrant  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import Termination  # noqa: E402
from chessmark.orchestration import TurnQueue  # noqa: E402
from chessmark.orchestration.tournament import advance  # noqa: E402
from chessmark.tournament import FieldFilter, Format, TournamentConfig, standings  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
AMBER, GREEN, RED = "\033[38;5;179m", "\033[38;5;108m", "\033[38;5;167m"


def field_from(args: argparse.Namespace) -> FieldFilter:
    """Every bracket is this filter with different arguments — that is the whole design."""
    free_only = True if args.free else (False if args.paid else None)
    open_weights = True if args.open_weights else (False if args.closed_weights else None)
    return FieldFilter(
        slugs=tuple(args.model or ()),
        providers=tuple(args.provider or ()),
        free_only=free_only,
        open_weights=open_weights,
        min_credit_cost=args.min_credits,
        max_credit_cost=args.max_credits,
        requires_reasoning=True if args.reasoning else None,
        limit=args.limit,
    )


async def cmd_field(args: argparse.Namespace) -> int:
    """Show who would enter, so a bracket can be checked before it costs anything."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        field = field_from(args)
        entrants = await repo.resolve_field(session, field)

    print(f"{BOLD}{field.describe()}{OFF}")
    print(f"{DIM}{len(entrants)} entrants{OFF}\n")
    for entrant in entrants:
        print(f"  {entrant.seed:>3}. {entrant.key}")
    if not entrants:
        print(f"  {RED}nothing matches — no games would be scheduled{OFF}")
    return 0


async def cmd_create(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    config = TournamentConfig(
        format=Format(args.format),
        double=args.double,
        rounds=args.rounds,
        max_concurrent=args.max_concurrent,
        max_usd=Decimal(str(args.max_usd)) if args.max_usd else None,
        max_plies_per_game=args.max_plies,
        max_usd_per_game=Decimal(str(args.max_usd_per_game)) if args.max_usd_per_game else None,
        is_ranked=not args.unranked,
        field=field_from(args),
    )

    async with sessionmaker() as session:
        entrants = await repo.resolve_field(session, config.field)
        if len(entrants) < 2:
            print(
                f"{RED}only {len(entrants)} entrants match — nothing to play{OFF}", file=sys.stderr
            )
            return 1

        tournament = await repo.create_tournament(
            session, name=args.name, slug=args.slug, config=config, entrants=entrants
        )
        await session.commit()
        created = tournament.id

    print(f"{BOLD}{args.name}{OFF}  {DIM}{created}{OFF}")
    print(f"  field       : {config.field.describe()} — {len(entrants)} entrants")
    print(f"  format      : {config.format}{' (double)' if config.double else ''}")
    if config.format is Format.SWISS:
        print(f"  rounds      : {config.rounds}")
    print(f"  concurrency : {config.max_concurrent}")
    print(f"  budget      : {f'${config.max_usd}' if config.max_usd else 'uncapped'}")
    print(f'\n{DIM}Start a worker, then: make tournament ARGS="run {args.slug}"{OFF}')
    return 0


async def resolve_slug(session: Any, slug: str) -> Tournament:
    tournament = await session.scalar(sa.select(Tournament).where(Tournament.slug == slug))
    if tournament is None:
        raise SystemExit(f"no tournament with slug {slug!r}")
    return tournament


async def cmd_run(args: argparse.Namespace) -> int:
    """Tick the event along until it finishes, pauses, or the operator stops."""
    settings = get_settings()
    redis: Redis[Any] = Redis.from_url(str(settings.redis_url))
    queue = TurnQueue(redis)
    await queue.ensure_group()
    sessionmaker = get_sessionmaker()

    try:
        async with sessionmaker() as session:
            tournament = await resolve_slug(session, args.slug)
            tournament_id, name = tournament.id, tournament.name

        print(f"{BOLD}{name}{OFF} {DIM}{tournament_id}{OFF}")
        quiet = 0

        while True:
            step = await advance(sessionmaker, queue, tournament_id=tournament_id)

            if step.status is TournamentStatus.FINISHED:
                print(f"{GREEN}finished{OFF} — {step.detail}")
                return 0
            if step.status is TournamentStatus.PAUSED:
                print(f"{AMBER}paused{OFF} — {step.detail}")
                return 0

            if step.idle:
                quiet += 1
                if args.once:
                    print(f"{DIM}nothing to do{OFF}")
                    return 0
            else:
                quiet = 0
                bits = []
                if step.scheduled_round:
                    bits.append(f"scheduled round {step.scheduled_round}")
                if step.started:
                    bits.append(f"started {step.started}")
                if step.settled:
                    bits.append(f"settled {step.settled}")
                print(f"  {' · '.join(bits)}")

            if args.once:
                return 0
            await asyncio.sleep(args.interval)
    finally:
        await redis.aclose()


async def cmd_pause(args: argparse.Namespace) -> int:
    """Stop starting new games. Games already in flight are left alone unless asked otherwise."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tournament = await resolve_slug(session, args.slug)
        if tournament.status in {TournamentStatus.FINISHED, TournamentStatus.ABANDONED}:
            print(f"{AMBER}{tournament.slug} is already over ({tournament.status}){OFF}")
            return 0

        tournament.status = TournamentStatus.PAUSED
        aborted = 0
        if args.abort_live:
            # A game left running holds a job in the queue; a worker starting later would pick it
            # up and play a round of a tournament nobody meant to continue.
            for row in await repo.in_flight(session, tournament.id):
                game = await session.get(Game, row.game_id)
                if game is None:
                    continue
                game.status = GameStatus.ABORTED
                game.termination = Termination.ABANDONED
                game.termination_detail = "the tournament was paused"
                game.ended_at = sa.func.now()
                row.abandoned_reason = "the tournament was paused"
                row.ended_at = sa.func.now()
                aborted += 1
        await session.commit()

    print(
        f"{AMBER}paused{OFF} {args.slug}" + (f" · aborted {aborted} in flight" if aborted else "")
    )
    print(f'{DIM}resume with: make tournament ARGS="resume {args.slug}"{OFF}')
    return 0


async def cmd_resume(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tournament = await resolve_slug(session, args.slug)
        if tournament.status in {TournamentStatus.FINISHED, TournamentStatus.ABANDONED}:
            print(f"{RED}{args.slug} is over and cannot be resumed{OFF}", file=sys.stderr)
            return 1
        if args.max_usd is not None:
            tournament.max_usd = Decimal(str(args.max_usd))
        tournament.status = TournamentStatus.RUNNING
        tournament.ended_at = None
        await session.commit()

    print(f"{GREEN}resumed{OFF} {args.slug}")
    return 0


async def cmd_abandon(args: argparse.Namespace) -> int:
    """End an event for good. Its games stay readable; its table is final but incomplete."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tournament = await resolve_slug(session, args.slug)
        tournament.status = TournamentStatus.ABANDONED
        tournament.ended_at = sa.func.now()
        for row in await repo.in_flight(session, tournament.id):
            game = await session.get(Game, row.game_id)
            if game is not None:
                game.status = GameStatus.ABORTED
                game.termination = Termination.ABANDONED
                game.termination_detail = "the tournament was abandoned"
                game.ended_at = sa.func.now()
            row.abandoned_reason = "the tournament was abandoned"
            row.ended_at = sa.func.now()
        await session.commit()

    print(f"{RED}abandoned{OFF} {args.slug}")
    return 0


async def cmd_withdraw(args: argparse.Namespace) -> int:
    """Take an entrant out of a running event.

    Their played games stand — those are real results — and their unplayed pairings are marked
    abandoned rather than awarded, because a walkover is not a finding about the opponent.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tournament = await resolve_slug(session, args.slug)
        entrant = await session.scalar(
            sa.select(TournamentEntrant).where(
                TournamentEntrant.tournament_id == tournament.id,
                TournamentEntrant.key == args.key,
            )
        )
        if entrant is None:
            print(f"{RED}{args.key} is not in {args.slug}{OFF}", file=sys.stderr)
            return 1

        entrant.withdrawn = True
        dropped = 0
        for row in await repo.unplayed(session, tournament.id):
            if args.key in (row.white_key, row.black_key):
                row.abandoned_reason = f"{args.key} withdrew"
                row.ended_at = sa.func.now()
                dropped += 1
        await session.commit()

    print(f"{AMBER}withdrew{OFF} {args.key} · {dropped} unplayed pairings dropped")
    return 0


async def cmd_standings(args: argparse.Namespace) -> int:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tournament = await resolve_slug(session, args.slug)
        entrants = await repo.entrants_of(session, tournament.id)
        results = await repo.results_so_far(session, tournament.id)
        used = await repo.spent(session, tournament.id)
        status, name = tournament.status, tournament.name

    table = standings(entrants, results)
    print(f"{BOLD}{name}{OFF}  {DIM}{status} · {len(results)} games · ${used:.4f}{OFF}\n")
    print(f"  {'#':>3}  {'model':<44} {'pts':>5} {'w/d/l':>9} {'sb':>6}")
    for standing in table:
        record = f"{standing.wins}/{standing.draws}/{standing.losses}"
        print(
            f"  {standing.place:>3}  {standing.key:<44} {standing.score:>5.1f} "
            f"{record:>9} {standing.sonneborn_berger:>6.2f}"
        )
    return 0


def add_field_options(parser: argparse.ArgumentParser) -> None:
    """The bracket. Every option is optional and they compose."""
    group = parser.add_argument_group("field")
    group.add_argument("--model", action="append", help="an explicit slug; repeatable")
    group.add_argument("--provider", action="append", help="a vendor; repeatable")
    group.add_argument("--free", action="store_true", help="free variants only")
    group.add_argument("--paid", action="store_true", help="paid variants only")
    group.add_argument("--open-weights", action="store_true", help="models with a HuggingFace repo")
    group.add_argument("--closed-weights", action="store_true", help="models without one")
    group.add_argument("--min-credits", type=int)
    group.add_argument("--max-credits", type=int)
    group.add_argument("--reasoning", action="store_true", help="reasoning models only")
    group.add_argument("--limit", type=int, help="cap the field size")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("field", help="show who would enter")
    add_field_options(preview)
    preview.set_defaults(run=cmd_field)

    create = sub.add_parser("create", help="create a tournament")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--format", choices=[str(f) for f in Format], default=str(Format.SWISS))
    create.add_argument("--double", action="store_true", help="round robin: play every pair twice")
    create.add_argument("--rounds", type=int, default=5, help="swiss only")
    create.add_argument("--max-concurrent", type=int, default=1)
    create.add_argument("--max-usd", type=float, help="the event's own ceiling")
    create.add_argument("--max-usd-per-game", type=float)
    create.add_argument("--max-plies", type=int, default=300)
    create.add_argument("--unranked", action="store_true")
    add_field_options(create)
    create.set_defaults(run=cmd_create)

    run = sub.add_parser("run", help="tick an event along; a worker must be running")
    run.add_argument("slug")
    run.add_argument("--interval", type=float, default=20.0, help="seconds between ticks")
    run.add_argument("--once", action="store_true", help="one step, then exit")
    run.set_defaults(run=cmd_run)

    pause = sub.add_parser("pause", help="stop starting new games")
    pause.add_argument("slug")
    pause.add_argument(
        "--abort-live",
        action="store_true",
        help="also abort games in flight, so a worker cannot pick them up later",
    )
    pause.set_defaults(run=cmd_pause)

    resume = sub.add_parser("resume", help="start again after a pause")
    resume.add_argument("slug")
    resume.add_argument("--max-usd", type=float, help="raise the ceiling that stopped it")
    resume.set_defaults(run=cmd_resume)

    abandon = sub.add_parser("abandon", help="end an event for good")
    abandon.add_argument("slug")
    abandon.set_defaults(run=cmd_abandon)

    withdraw = sub.add_parser("withdraw", help="take an entrant out of a running event")
    withdraw.add_argument("slug")
    withdraw.add_argument("key", help="the contestant key, e.g. vendor/model:free")
    withdraw.set_defaults(run=cmd_withdraw)

    table = sub.add_parser("standings", help="print the table")
    table.add_argument("slug")
    table.set_defaults(run=cmd_standings)

    return parser


async def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(await args.run(args))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
