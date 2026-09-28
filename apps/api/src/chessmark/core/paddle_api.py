"""The one Paddle API call the server makes: a transaction at the price the buyer chose (ADR-0056).

A custom amount cannot be a catalogue price, and Paddle.js cannot open a checkout at an arbitrary
price on its own — so the API creates a transaction with a non-catalogue price of the "Chessmark
credit" product, and the browser opens Paddle's checkout on that transaction. It also puts the
reservation in `custom_data`, which is how the webhook knows what the purchase grants.

The key's own prefix says which Paddle to call: `pdl_sdbx_…` is the sandbox, `pdl_live_…` live.
Reading the environment from the key rather than from a second setting means the two cannot
disagree — a sandbox key sent to the live API fails, and silently choosing either would be worse.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx

log = logging.getLogger(__name__)

SANDBOX_URL = "https://sandbox-api.paddle.com"
LIVE_URL = "https://api.paddle.com"
TIMEOUT_SECONDS = 15.0


class PaddleApiError(Exception):
    """Paddle did not create the transaction. Carries no detail meant for a buyer."""


def base_url_for(api_key: str) -> str:
    if api_key.startswith("pdl_sdbx_"):
        return SANDBOX_URL
    if api_key.startswith("pdl_live_"):
        return LIVE_URL
    raise PaddleApiError(
        "PADDLE_API_KEY is neither a sandbox (pdl_sdbx_) nor a live (pdl_live_) key"
    )


def _cents(usd: Decimal) -> str:
    return str(int((usd * 100).to_integral_value()))


class PaddleApi:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._transport = transport

    async def create_transaction(
        self,
        *,
        product_id: str,
        price_usd: Decimal,
        credit_usd: Decimal,
        custom_data: dict[str, Any],
    ) -> str:
        """A `draft` transaction for one purchase of credit, to open a checkout on. Its id."""
        body = {
            "items": [
                {
                    "quantity": 1,
                    "price": {
                        "product_id": product_id,
                        "name": "Chessmark credit",
                        "description": (
                            f"${price_usd:.2f} of credit purchase, tax included "
                            f"(up to ${credit_usd:.2f} of credit before tax)"
                        ),
                        "unit_price": {"amount": _cents(price_usd), "currency_code": "USD"},
                        # Tax included: the amount the buyer chose is what they pay, and any tax
                        # comes out of it. The credit is therefore settled from the tax Paddle
                        # charged, when its webhook arrives (`db.purchases`).
                        "tax_mode": "internal",
                        "quantity": {"minimum": 1, "maximum": 1},
                    },
                }
            ],
            "currency_code": "USD",
            "collection_mode": "automatic",
            "custom_data": custom_data,
        }
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=self._transport
            ) as http:
                response = await http.post(
                    f"{base_url_for(self._api_key)}/transactions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as error:
            raise PaddleApiError("Paddle did not answer") from error
        if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            log.warning("paddle refused a transaction: %s %s", response.status_code, response.text)
            raise PaddleApiError(f"Paddle answered {response.status_code}")
        transaction_id = response.json().get("data", {}).get("id")
        if not isinstance(transaction_id, str) or not transaction_id.startswith("txn_"):
            raise PaddleApiError("Paddle answered without a transaction id")
        return transaction_id


__all__ = ["PaddleApi", "PaddleApiError", "base_url_for"]
