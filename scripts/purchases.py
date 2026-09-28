"""What an account bought, and whether each purchase may be refunded (ADR-0055).

The refund policy (`/refunds`) refunds a purchase only while it is **untouched and inside 14 days**,
and nothing enforces that automatically: Paddle processes the refund, and our webhook only takes
the credit back once it has. So when somebody writes to support asking for their money, this is
the answer to look up before refunding in Paddle's dashboard.

    purchases.py ahmed@example.com
    purchases.py user_2abc...

"Untouched" is judged on the pooled balance: nothing charged to the account after the purchase.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from chessmark.db.credits import balance_of  # noqa: E402
from chessmark.db.purchases import purchases_of  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402
from chessmark.db.users import resolve_user  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN = "\033[31m", "\033[32m"


def _usd(amount: Decimal) -> str:
    return f"{'-' if amount < 0 else ''}${abs(amount):.2f}"


def _paid(minor: str, currency: str) -> str:
    """Paddle's lowest-unit amount, as the buyer paid it. Every pack is sold in USD today."""
    try:
        return f"{Decimal(minor) / 100:.2f} {currency}"
    except ArithmeticError:
        return f"{minor} {currency}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user", help="an email address, a Clerk user id, or a Chessmark user id")
    args = parser.parse_args()

    try:
        async with session_scope() as session:
            user = await resolve_user(session, args.user)
            if user is None:
                print(f"{RED}no user matching {args.user!r}{OFF}", file=sys.stderr)
                return 1

            balance = await balance_of(session, user.id)
            print(
                f"{BOLD}{user.email or user.clerk_user_id}{OFF} {DIM}{user.id}{OFF}\n"
                f"  balance {BOLD}{_usd(balance)}{OFF}"
            )
            reports = await purchases_of(session, user.id)
            if not reports:
                print(f"  {DIM}no purchases{OFF}")
                return 0

            for report in reports:
                p = report.purchase
                verdict = (
                    f"{GREEN}refundable until {report.window_ends:%Y-%m-%d %H:%M} UTC{OFF}"
                    if report.refundable
                    else f"{RED}not refundable{OFF}: {report.why}"
                )
                print(
                    f"\n  {p.created_at:%Y-%m-%d %H:%M} {BOLD}{p.paddle_transaction_id}{OFF}\n"
                    f"    paid {_paid(p.grand_total, p.currency_code)} (tax "
                    f"{_paid(p.tax or '0', p.currency_code)}) · credit {_usd(p.credit_usd)} · "
                    f"{p.status}\n"
                    f"    spent after it {_usd(report.spent_since)}"
                    + (f" · refunds/chargebacks {_usd(report.adjusted)}" if report.adjusted else "")
                    + f"\n    {verdict}"
                )
            print(
                f"\n{DIM}Refund in Paddle's dashboard (Transactions → the transaction → Refund);"
                f" the webhook takes the credit back once Paddle approves it.{OFF}"
            )
            return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
