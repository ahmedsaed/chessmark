"""Take models that cannot finish a game out of the registry, and clear up after them.

AGENT-14 says four kinds of model are never registered: no tool calling, `:batch` variants,
a context window under the floor, and floating aliases. The rule was enforced at *admission* and
nowhere else, so it held for models arriving after it and not for the registry as it already
stood — and `seed-models` re-enabled whatever a `refresh-catalogue` had disabled, because a sync
asserted `enabled: True` on every row it touched. `liquid/lfm-2.5-2.6b:free` reached a pool that
way, with a 65,536-token window against a 128k floor, and abandoned a game at ply 10.

This applies the rule to what is already there, and it is deliberately two commands in one:

* **disable** the registry rows — never delete them. `players.model_id` is `ON DELETE RESTRICT`,
  so a row with games cannot be deleted at all, and that is the right constraint: a game must stay
  readable however its model turned out.
* **remove their games**, which is the destructive half and the reason this reports before it acts.

`--apply` is required to change anything. Without it this is a report.

    make prune-registry                          # what would go, and why
    make prune-registry ARGS=--apply             # do it
    make prune-registry ARGS="--apply --keep-finished"   # spare games that reached a result

`--model SLUG` names one explicitly, whatever the catalogue says about it. The rule above is a
prediction from metadata, and a **distribution gate is invisible to it** — a gated model advertises
tool support, a full window and 100% uptime and refuses anyway (AGENT-18). The worker disables such
a model when it is refused; this is how the games it already lost get cleared.

    make prune-registry ARGS="--model vendor/model:free --only-named --apply"

`--apply` acts on everything in the report, so `--only-named` is usually what you want alongside
`--model`: it leaves the eligibility rule out entirely and touches nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.agents.registry import (  # noqa: E402
    context_floor,
    endpoint_is_playable,
    ineligible_reasons,
)
from chessmark.db import tournaments as repo  # noqa: E402
from chessmark.db.enums import GameStatus  # noqa: E402
from chessmark.db.models import (  # noqa: E402
    Game,
    ModelEndpoint,
    ModelRegistry,
    Player,
    Tournament,
    TournamentEntrant,
    TournamentGame,
)
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, AMBER, GREEN = "\033[31m", "\033[33m", "\033[32m"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="make the changes (default: report)")
    parser.add_argument(
        "--keep-finished",
        action="store_true",
        help="keep games that reached a real result; remove only the ones that never finished",
    )
    parser.add_argument(
        "--min-context",
        type=int,
        default=None,
        help="override the floor (default: settings.min_context_tokens)",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="SLUG",
        help=(
            "also prune this model, whatever the catalogue says about it. Repeatable. For a gate "
            "no metadata predicts (AGENT-18): the model is playable on paper and refused in fact."
        ),
    )
    parser.add_argument(
        "--only-named",
        action="store_true",
        help="prune only the --model slugs, leaving the eligibility rule out of it entirely",
    )
    args = parser.parse_args()

    if args.only_named and not args.model:
        parser.error("--only-named needs at least one --model")

    floor = context_floor(args.min_context)
    sessionmaker = get_sessionmaker()

    try:
        async with sessionmaker() as session:
            playable_ids = set(
                (
                    await session.scalars(
                        sa.select(ModelEndpoint.model_id).where(*endpoint_is_playable(floor))
                    )
                ).all()
            )
            rows = list(
                await session.scalars(
                    sa.select(ModelRegistry)
                    .where(ModelRegistry.enabled.is_(True))
                    .order_by(ModelRegistry.openrouter_id)
                )
            )

            doomed: list[tuple[ModelRegistry, list[str]]] = []
            # **`--only-named` is about the blast radius, not about convenience.** `--apply` acts
            # on the whole report, so naming one gated model would otherwise take every model the
            # eligibility rule happens to dislike today with it — a much larger change than the
            # operator asked for, made at the moment they are dealing with something else.
            if not args.only_named:
                for row in rows:
                    against = ineligible_reasons(
                        row, min_context=floor, has_endpoint=row.id in playable_ids
                    )
                    if against:
                        doomed.append((row, against))

            print(f"{BOLD}context floor{OFF} {floor:,} tokens · {len(rows)} enabled models")
            if args.only_named:
                print(f"{DIM}--only-named: the eligibility rule is not being applied{OFF}")

            # **Named models bypass the test, deliberately.** The rule this script applies is a
            # prediction from metadata, and a distribution gate is invisible to it: the model
            # advertises tools, a full window and 100% uptime, and answers 403 anyway (AGENT-18).
            # So the operator supplies the finding the catalogue cannot. Already-disabled rows are
            # included, because disabling is what the worker does on the refusal and the games it
            # left behind still need clearing.
            named = set(args.model)
            for slug in sorted(named):
                row = await session.scalar(
                    sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == slug)
                )
                if row is None:
                    print(f"{RED}no registry row for {slug}{OFF} — nothing to prune")
                    continue
                if any(existing.id == row.id for existing, _ in doomed):
                    continue
                doomed.append((row, ["named on the command line"]))

            if not doomed:
                print(f"{GREEN}nothing to prune{OFF} — every enabled model can finish a game")
                return 0

            total_games = 0
            for row, against in doomed:
                games = list(
                    await session.scalars(
                        sa.select(Game)
                        .join(Player, Player.game_id == Game.id)
                        .where(Player.model_id == row.id)
                        .distinct()
                    )
                )
                keep = (
                    [g for g in games if g.status is GameStatus.FINISHED]
                    if args.keep_finished
                    else []
                )
                remove = [g for g in games if g not in keep]
                total_games += len(remove)

                plies = sum(g.ply_count for g in remove)
                cost = sum((g.total_cost_usd or Decimal(0)) for g in remove)
                seats = await session.scalars(
                    sa.select(TournamentEntrant).where(TournamentEntrant.model_id == row.id)
                )
                events = list(seats)

                print(f"\n{AMBER}{row.openrouter_id}{OFF}")
                for reason in against:
                    print(f"    · {reason}")
                print(
                    f"    {DIM}games{OFF} {len(remove)} to remove"
                    f"{f', {len(keep)} kept' if keep else ''}"
                    f" · {plies} plies · ${cost:.4f}"
                    f" · {len(events)} tournament seats"
                )

            if not args.apply:
                print(
                    f"\n{DIM}report only — {len(doomed)} models and {total_games} games would go."
                    f" Re-run with --apply.{OFF}"
                )
                return 0

            # ------------------------------------------------------------------ apply
            removed_games = 0
            for row, _ in doomed:
                row.enabled = False

                # Out of every event first, so nothing schedules against a model mid-cleanup. An
                # unplayed pairing is abandoned rather than awarded: a walkover is not a finding
                # about the opponent, which is the same rule `withdraw` follows.
                for entrant in await session.scalars(
                    sa.select(TournamentEntrant).where(TournamentEntrant.model_id == row.id)
                ):
                    entrant.withdrawn = True
                    tournament = await session.get(Tournament, entrant.tournament_id)
                    if tournament is None:
                        continue
                    for pairing in await repo.unplayed(session, tournament.id):
                        if entrant.key in (pairing.white_key, pairing.black_key):
                            pairing.abandoned_reason = f"{entrant.key} cannot finish a game"
                            pairing.ended_at = sa.func.now()

                games = list(
                    await session.scalars(
                        sa.select(Game)
                        .join(Player, Player.game_id == Game.id)
                        .where(Player.model_id == row.id)
                        .distinct()
                    )
                )
                for game in games:
                    if args.keep_finished and game.status is GameStatus.FINISHED:
                        continue
                    # The pairing row goes explicitly: `tournament_games.game_id` is
                    # ON DELETE SET NULL, so deleting the game alone leaves a settled-abandoned
                    # pairing behind, still counted in the event's totals.
                    await session.execute(
                        sa.delete(TournamentGame).where(TournamentGame.game_id == game.id)
                    )
                    await session.delete(game)
                    removed_games += 1

            await session.commit()

        print(f"\n{GREEN}pruned{OFF} {len(doomed)} models disabled · {removed_games} games removed")
        print(
            f"{DIM}The rows are disabled, never deleted: a game must stay readable however its "
            f"model turned out, and `players.model_id` is ON DELETE RESTRICT.{OFF}"
        )
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
