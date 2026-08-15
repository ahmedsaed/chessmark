"""Match orchestration: the queue, the worker, and the reconciler.

A job is "advance game G from ply N" (ADR-0007). Everything here follows from that.
"""

from chessmark.orchestration.match import Match, Seat, create_match, model_for, start_match
from chessmark.orchestration.queue import AdvanceTurn, Delivery, TurnQueue
from chessmark.orchestration.reconciler import ReconcileReport, find_stalled, reconcile
from chessmark.orchestration.worker import (
    ABORTED,
    ADVANCED,
    BUDGET,
    GAME_OVER,
    NOT_RUNNING,
    STALE,
    TURN_FAILED,
    HandledJob,
    TurnWorker,
)

__all__ = [
    "ABORTED",
    "ADVANCED",
    "BUDGET",
    "GAME_OVER",
    "NOT_RUNNING",
    "STALE",
    "TURN_FAILED",
    "AdvanceTurn",
    "Delivery",
    "HandledJob",
    "Match",
    "ReconcileReport",
    "Seat",
    "TurnQueue",
    "TurnWorker",
    "create_match",
    "find_stalled",
    "model_for",
    "reconcile",
    "start_match",
]
