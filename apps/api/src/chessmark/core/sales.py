"""Whether credit is on sale: the operator's switch, flipped at runtime (ADR-0057).

Configuring Paddle says selling is *possible*; this says it is *open*. They are kept apart so the
keys can be put on the server before Paddle has verified the account — the page then shows the
breakdown and the tax estimate, and says "not on sale" where Buy would be — and so selling can be
paused later without editing `.env` and restarting: a refund storm, a pricing bug, OpenRouter
misbehaving. `./chessmark sales open | pause "reason"`.

**Closed until somebody opens it.** No value means closed, not open: the first deploy with the
keys must not start taking money on its own, and a Redis that lost its data should stop sales
rather than resume them. The failure mode of a switch on money is "we stopped selling", never "we
sold when we meant not to" — the opposite of the model-call halt beside it (`core.halt`), whose
failure mode is "we kept playing".

**Only new checkouts stop.** Paddle's webhook keeps crediting what was already paid for: a buyer
halfway through a checkout when sales pause has paid, and the money has moved.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: No TTL: a state, not a limit. A pause that lapsed overnight would reopen sales nobody reopened.
KEY = "chessmark:sales"


@dataclass(frozen=True, slots=True)
class SalesState:
    open: bool
    #: Why it was paused; `None` when open, or never set.
    reason: str | None = None
    #: When it last changed; `None` when it never has.
    at: dt.datetime | None = None


#: What an unset or unreadable switch means.
NEVER_OPENED = SalesState(open=False, reason="never opened")


class Sales:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def state(self) -> SalesState:
        """The switch. Missing or unreadable is **closed** — logged, never guessed open."""
        raw = await self._redis.get(KEY)
        if not raw:
            return NEVER_OPENED
        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            is_open = data["open"]
            if not isinstance(is_open, bool):
                raise TypeError("open is not a boolean")
            return SalesState(
                open=is_open,
                reason=data.get("reason"),
                at=dt.datetime.fromisoformat(data["at"]),
            )
        except (ValueError, KeyError, TypeError):
            log.exception("unreadable sales switch; treating credit as not on sale")
            return NEVER_OPENED

    async def is_open(self) -> bool:
        return (await self.state()).open

    async def open(self, *, now: dt.datetime | None = None) -> SalesState:
        return await self._write(SalesState(open=True, at=now or dt.datetime.now(dt.UTC)))

    async def pause(self, reason: str, *, now: dt.datetime | None = None) -> SalesState:
        return await self._write(
            SalesState(open=False, reason=reason, at=now or dt.datetime.now(dt.UTC))
        )

    async def _write(self, state: SalesState) -> SalesState:
        assert state.at is not None
        await self._redis.set(
            KEY,
            json.dumps({"open": state.open, "reason": state.reason, "at": state.at.isoformat()}),
        )
        if state.open:
            log.warning("credit sales opened")
        else:
            log.warning("credit sales paused: %s", state.reason)
        return state


__all__ = ["KEY", "NEVER_OPENED", "Sales", "SalesState"]
