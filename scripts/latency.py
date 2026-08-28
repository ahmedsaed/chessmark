"""Where a game's time actually goes.

Answers one question — *is the model slow, or are we?* — by decomposing the wall clock into the
three places it can be spent:

* **provider** — `llm_calls.latency_ms`, the time inside a call. Nothing we control.
* **harness** — a turn's wall clock minus its calls: transcript building, tool dispatch, the
  referee, persistence. Ours, and it should be milliseconds.
* **queue** — the gap between turns that the earlier turn does not account for. Ours too, and it is
  where a busy worker shows up: a worker plays one turn at a time, so a turn waits behind whatever
  else is running (see `WORKER_REPLICAS`).

Only the third is usually interesting, and only the third is invisible in a turn's own numbers,
which is why this exists rather than a query over `turns`.

**It has to be derived.** `game_events.created_at` is `now()`, which in Postgres is the
*transaction* timestamp, and a turn commits everything it produced in one transaction (NFR-08) — so
a turn's `turn_started` and its `move_made` carry the same instant. "Move landed, next turn started"
reads back the previous turn's duration, plausibly enough to be believed.

    latency.py <game-id>
    latency.py <game-id> --all      # every turn, not just the tail
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import statistics
import sys
import uuid
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.db.enums import EventType  # noqa: E402
from chessmark.db.models import GameEvent, LlmCall, Turn  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, AMBER = "\033[31m", "\033[32m", "\033[33m"


def _secs(ms: float) -> str:
    return f"{ms / 1000:.1f}s"


def _tone(seconds: float, warn: float, bad: float) -> str:
    if seconds >= bad:
        return RED
    if seconds >= warn:
        return AMBER
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_id")
    parser.add_argument("--all", action="store_true", help="every turn, not just the last 20")
    args = parser.parse_args()

    try:
        game_id = uuid.UUID(args.game_id)
    except ValueError:
        print(f"{RED}{args.game_id!r} is not a game id{OFF}", file=sys.stderr)
        return 1

    try:
        async with session_scope() as session:
            turns = list(
                await session.scalars(
                    sa.select(Turn).where(Turn.game_id == game_id).order_by(Turn.id)
                )
            )
            if not turns:
                print(f"{RED}no turns for {game_id}{OFF}", file=sys.stderr)
                return 1

            calls = (
                await session.execute(
                    sa.select(LlmCall.turn_id, sa.func.sum(LlmCall.latency_ms))
                    .where(LlmCall.game_id == game_id)
                    .group_by(LlmCall.turn_id)
                )
            ).all()
            provider_ms = {turn_id: int(total or 0) for turn_id, total in calls}

            # The queue wait, and it has to be *derived* rather than read.
            #
            # `game_events.created_at` defaults to `now()`, which in Postgres is the **transaction**
            # timestamp — constant for the whole transaction. A turn commits everything it produced
            # in one transaction (NFR-08), so a turn's `turn_started` and its `move_made` carry the
            # *same* instant. Timing anything inside a turn from the log therefore measures nothing,
            # and the obvious "move landed → next turn started" reads back the previous turn's
            # duration instead. It looked plausible, which is what made it worth chasing.
            #
            # What the log does give reliably is the gap between two turns' transaction starts, and
            # that gap is the earlier turn plus the wait after it. The turn's own `latency_ms` comes
            # from `perf_counter` in-process, so subtracting it leaves the wait.
            starts = list(
                await session.scalars(
                    sa.select(GameEvent)
                    .where(
                        GameEvent.game_id == game_id,
                        GameEvent.type == EventType.TURN_STARTED,
                    )
                    .order_by(GameEvent.seq)
                )
            )
            by_turn = {t.ply_number: t for t in turns if t.ply_number is not None}
            waits: list[float] = []
            by_ply: dict[int, float] = {}
            previous: tuple[int, dt.datetime] | None = None
            for event in starts:
                stamp = event.created_at
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=dt.UTC)
                ply = int(event.payload.get("ply") or 0)
                if previous is not None:
                    earlier_ply, earlier = previous
                    played = by_turn.get(earlier_ply)
                    gap = (stamp - earlier).total_seconds()
                    wait = gap - (played.latency_ms or 0) / 1000 if played else gap
                    # A human's thinking time sits in this gap too and is not ours to count. Only
                    # the turns between two *model* turns are attributable to the queue.
                    if wait >= 0:
                        waits.append(wait)
                        by_ply[ply] = wait
                previous = (ply, stamp)

        shown = turns if args.all else turns[-20:]
        print(
            f"{BOLD}{'ply':>5} {'turn':>8} {'provider':>9} {'harness':>8} "
            f"{'queued':>8} {'calls':>6}{OFF}"
        )
        overhead_total = wall_total = 0
        for turn in shown:
            wall = turn.latency_ms or 0
            prov = provider_ms.get(turn.id, 0)
            over = max(wall - prov, 0)
            wall_total += wall
            overhead_total += over
            wait = by_ply.get(turn.ply_number or -1)
            wait_text = f"{wait:.1f}s" if wait is not None else "—"
            print(
                f"{turn.ply_number or 0:>5} {_secs(wall):>8} {_secs(prov):>9} "
                f"{_tone(over / 1000, 1, 5)}{_secs(over):>8}{OFF} "
                f"{_tone(wait or 0, 30, 120)}{wait_text:>8}{OFF} {turn.llm_call_count:>6}"
            )

        share = 100 * overhead_total / wall_total if wall_total else 0
        print()
        print(f"{BOLD}where the time went{OFF}")
        print(
            f"  provider   {_secs(wall_total - overhead_total):>9}"
            f"   {100 - share:5.1f}%  {DIM}inside the model's calls{OFF}"
        )
        print(
            f"  harness    {_secs(overhead_total):>9}   {share:5.1f}%  "
            f"{DIM}transcript, tools, referee, persistence{OFF}"
        )
        if waits:
            recent = waits[-20:]
            median = statistics.median(recent)
            tone = _tone(median, 30, 120)
            print(
                f"  queued     {tone}{median:8.1f}s{OFF}   median   "
                f"{DIM}waiting for a worker, between turns{OFF}"
            )
            # The worst case is quoted separately because it is not always a queue: a paused game
            # (ADR-0017) or a person thinking sits in the same gap, and calling either "queued"
            # would blame the harness for a wait it did not cause.
            print(
                f"  {DIM}worst {max(recent):.0f}s — a pause or a person thinking lands here too"
                f"{OFF}"
            )
            if median > 30:
                print(
                    f"\n{AMBER}A turn is waiting minutes for a worker.{OFF} A worker plays one turn "
                    f"at a time,\nso this is other games ahead in the queue: {BOLD}./chessmark "
                    f"workers 3{OFF}"
                )
        if share > 5:
            print(f"\n{RED}The harness is a real share of this.{OFF} That is worth chasing.")
        elif waits and statistics.median(waits[-20:]) <= 30:
            print(f"\n{GREEN}The time is the model's.{OFF} Nothing here is blocking it.")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
