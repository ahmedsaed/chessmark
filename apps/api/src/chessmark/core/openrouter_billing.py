"""What OpenRouter billed for a game's session (ADR-0054).

Every request a game makes carries `session_id = game-<id>`, and OpenRouter's analytics can group
by it. That is the whole of reconciliation's reach: it sees what OpenRouter billed whatever our own
record lost, which is why it is the check rather than yet another place to record calls.

**Two of OpenRouter's numbers, used for what each is good for.**

* The analytics API (`POST /analytics/query`, management key) lists every generation in a session.
  Its `total_usage` is **truncated to six decimal places per generation** — `0.000062496` reads
  `0.000062` — which is about 1% of a decision model's call. It is used to *find* generations,
  never to price them.
* `GET /generation` gives one generation's exact `total_cost`, to the nine places the account is
  billed in. It prices the generations our own record does not hold. The ones it does hold were
  already priced from the same figure (sixty sampled, sixty exact).

Every failure is `None` or an empty answer and never an exception: reconciliation is a check that
runs again later, and a network error must not become a charge or a refund.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
TIMEOUT_SECONDS = 20.0

#: OpenRouter's per-generation dimensions are held for 31 days. A day's margin, so a range computed
#: a moment ago is not refused for being a moment too long.
WINDOW = dt.timedelta(days=30)

#: One query's row cap. A game longer than this in generations is billed from what it returned and
#: logged, rather than paged — the longest game on record made about 750 requests.
ROW_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class BilledGeneration:
    generation_id: str
    #: Truncated to six places by the analytics API; zero means under a millionth of a dollar.
    usage_floor: Decimal


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OpenRouterBilling:
    def __init__(
        self,
        *,
        management_key: str,
        api_key: str,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._management_key = management_key
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self._management_key and self._api_key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport)

    async def generations(
        self, session_id: str, *, since: dt.datetime, until: dt.datetime
    ) -> list[BilledGeneration] | None:
        """Every generation OpenRouter billed to this session in the window. `None` if it would
        not say — which is not the same as an empty session, and must not be read as one."""
        start = max(since, until - WINDOW)
        body = {
            "metrics": ["total_usage"],
            "dimensions": ["generation_id"],
            "filters": [{"field": "session_id", "operator": "eq", "value": session_id}],
            # `Z`, whole seconds: the API refuses `+00:00` as "Invalid ISO datetime".
            "time_range": {"start": _iso(start), "end": _iso(until)},
            "limit": ROW_LIMIT,
        }
        try:
            async with self._client() as http:
                response = await http.post(
                    f"{self._base_url}/analytics/query",
                    json=body,
                    headers={"Authorization": f"Bearer {self._management_key}"},
                )
            if response.status_code != httpx.codes.OK:
                log.info("analytics answered %s for %s", response.status_code, session_id)
                return None
            payload = response.json()
            rows = payload["data"]["data"]
        except Exception:
            log.info("analytics did not answer for %s", session_id, exc_info=True)
            return None

        if payload["data"].get("metadata", {}).get("truncated"):
            log.warning("analytics truncated %s at %s rows", session_id, ROW_LIMIT)

        found: list[BilledGeneration] = []
        for row in rows:
            generation = row.get("generation_id")
            usage = _decimal(row.get("total_usage") or 0)
            if isinstance(generation, str) and generation and usage is not None:
                found.append(BilledGeneration(generation_id=generation, usage_floor=usage))
        return found

    async def remaining(self) -> Decimal | None:
        """What is left of the account's prepaid OpenRouter credit: purchased less used.

        Account-wide, so it counts every key's spending — ours, tournaments included — which is
        what selling credit has to be measured against (ADR-0056). `None` if OpenRouter would not
        say, which a caller must treat as "unknown", never as zero or as plenty.
        """
        try:
            async with self._client() as http:
                response = await http.get(
                    f"{self._base_url}/credits",
                    headers={"Authorization": f"Bearer {self._management_key}"},
                )
            if response.status_code != httpx.codes.OK:
                log.info("credits answered %s", response.status_code)
                return None
            data = response.json()["data"]
            purchased = _decimal(data.get("total_credits"))
            used = _decimal(data.get("total_usage"))
        except Exception:
            log.info("credits did not answer", exc_info=True)
            return None
        if purchased is None or used is None:
            return None
        return purchased - used

    async def cost_of(self, generation_id: str) -> Decimal | None:
        """One generation's exact cost, or `None` if OpenRouter would not say."""
        try:
            async with self._client() as http:
                response = await http.get(
                    f"{self._base_url}/generation",
                    params={"id": generation_id},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            if response.status_code != httpx.codes.OK:
                return None
            return _decimal(response.json()["data"].get("total_cost") or 0)
        except Exception:
            log.info("generation %s did not answer", generation_id, exc_info=True)
            return None


__all__ = ["BilledGeneration", "OpenRouterBilling"]
