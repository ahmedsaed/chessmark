"""The global halt — one switch that stops every model call in the system (OPS-19).

Distinct from the daily spend kill switch beside it in `core.budget`, and the difference is what
makes both worth having. That one is a **limit**: a number in config, compared against a counter
that resets at UTC midnight. This one is a **state**: something is wrong, or somebody said stop,
and it stays on until that changes.

Two things set it.

**A 402 from OpenRouter.** *"Your account or API key has insufficient credits"* — account-level, and
their documentation is explicit that it applies to free models too when the balance is negative. It
is not a fact about a model, an endpoint, or a game, so pausing games one at a time is the wrong
shape: thirty pairings each waking every fifteen minutes to rediscover the same refusal is roughly
120 doomed requests an hour against an account that cannot serve one of them.

**An operator.** `./chessmark halt "reason"` stops the whole harness without editing config or
stopping containers, which is worth having on its own — the daily kill switch is a limit read at
startup and cannot be flipped at runtime.

**It clears itself.** A halt that only a command lifts has one obvious failure: credits are topped
up at 11pm and the pool sits idle until somebody remembers the command. The halt records what the
balance said when it was set, and the reconciler probes `/api/v1/credits` on a slow cadence and
lifts a credit halt once the balance is positive. An operator halt is never lifted automatically —
somebody meant it.

**Nothing is forfeited and no game is ended.** A halted turn is not run and its job is dropped; the
game stays `RUNNING` and the reconciler re-enqueues it once spending is possible, exactly as the
daily budget does. A model must never lose a game because we ran out of money (invariant 11).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

#: No TTL. A limit expires with its day; a state persists until something changes it, and a halt
#: that quietly lapsed overnight would resume spending against an account that is still empty.
KEY = "chessmark:halt"

#: Set by a 402. Cleared automatically once the balance is positive again.
SOURCE_CREDITS = "credits"

#: Set by a person. **Never** cleared automatically — they meant it, and a probe deciding
#: otherwise would be the system overruling its operator.
SOURCE_OPERATOR = "operator"

#: Set by OpenRouter's daily free-model allowance running out. Lifts itself at a *known* time.
#:
#: The one halt that needs neither a probe nor a command: `X-RateLimit-Reset` says when the cap
#: lifts, so the key is written with that as its TTL and Redis does the rest. Nothing has to sweep
#: it and there is no midnight job to fail to run — the same reasoning the spend counters use.
SOURCE_FREE_TIER = "free_tier"


@dataclass(frozen=True, slots=True)
class HaltState:
    """Why everything stopped, and what we knew when it did."""

    reason: str
    source: str
    at: dt.datetime
    #: The account balance when the halt was set, when we had one. `None` means we never asked.
    #:
    #: Kept because it is what tells a genuine empty balance apart from a 402 about *this request*.
    #: OpenRouter is reported to check a key's remaining budget against `max_tokens` — the maximum
    #: possible output rather than the actual usage — so a large request can be refused against a
    #: balance that would serve a smaller one. Undocumented and never yet seen here, but it is the
    #: difference between "the account is empty" and "this one request was too big", and halting
    #: the whole system on the second would be the `403 → disable` mistake again (ADR-0019).
    balance_usd: Decimal | None = None

    #: When this halt lifts on its own, for the one source that knows. `None` means it does not.
    until: dt.datetime | None = None

    @property
    def self_clearing(self) -> bool:
        """Whether a credit probe may lift this. Not the same as expiring on its own."""
        return self.source == SOURCE_CREDITS


class Halt:
    """Read and write the global halt."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def state(self) -> HaltState | None:
        """The halt, or `None` when the system is running.

        A malformed value is treated as **no halt** and logged. The alternative — refusing to run
        because a key could not be parsed — turns a bad write into an outage, and this switch's
        failure mode should be "we kept going" rather than "everything stopped and nobody knew why".
        """
        raw = await self._redis.get(KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return HaltState(
                reason=str(data["reason"]),
                source=str(data["source"]),
                at=dt.datetime.fromisoformat(data["at"]),
                balance_usd=(
                    Decimal(str(data["balance_usd"]))
                    if data.get("balance_usd") is not None
                    else None
                ),
                until=(
                    dt.datetime.fromisoformat(data["until"])
                    if data.get("until") is not None
                    else None
                ),
            )
        except (ValueError, KeyError, TypeError):
            log.exception("unreadable halt value; treating the system as running")
            return None

    async def active(self) -> bool:
        return await self.state() is not None

    async def set(
        self,
        reason: str,
        *,
        source: str = SOURCE_OPERATOR,
        balance_usd: Decimal | None = None,
        until: dt.datetime | None = None,
        now: dt.datetime | None = None,
    ) -> HaltState:
        """Stop everything.

        **The first halt wins.** A later 402 must not overwrite an operator's halt with a
        self-clearing one, which would let a credit probe lift a stop a person had asked for. Two
        workers hitting a 402 in the same second are the ordinary case and neither should clobber
        the other's timestamp.
        """
        existing = await self.state()
        if existing is not None:
            return existing

        stamp = now or dt.datetime.now(dt.UTC)
        state = HaltState(
            reason=reason,
            source=source,
            at=stamp,
            balance_usd=balance_usd,
            until=until,
        )
        payload = json.dumps(
            {
                "reason": state.reason,
                "source": state.source,
                "at": state.at.isoformat(),
                "balance_usd": str(state.balance_usd) if state.balance_usd is not None else None,
                "until": state.until.isoformat() if state.until is not None else None,
            }
        )

        # **The TTL is the expiry.** A halt that knows when it ends is written with that as the
        # key's lifetime, so Redis lifts it — nothing has to sweep, and there is no job to fail to
        # run. A halt already in the past is not written at all: it would expire immediately and
        # the only effect would be a log line claiming the harness had stopped.
        seconds = int((until - stamp).total_seconds()) if until is not None else 0
        if until is not None and seconds <= 0:
            log.info("not halting: %s had already lifted at %s", reason, until.isoformat())
            return state
        if seconds > 0:
            await self._redis.set(KEY, payload, ex=seconds)
        else:
            await self._redis.set(KEY, payload)
        log.error("halting every model call (%s): %s", state.source, state.reason)
        return state

    async def clear(self) -> bool:
        """Start again. True when a halt was actually lifted."""
        removed = bool(await self._redis.delete(KEY))
        if removed:
            log.warning("halt lifted; model calls resume")
        return removed
