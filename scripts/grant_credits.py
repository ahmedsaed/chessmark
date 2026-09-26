"""Grant or revoke credit in US dollars, by email, Clerk id, or ours (AUTH-11, AUTH-13, ADR-0052).

The same thing `POST /admin/credits` does, without needing an admin session — which is the point:
on a server the person granting credit is at a shell, not signed in to their own site. New accounts
hold zero by design, so this is the whole of the granting mechanism during the testing phase.

    grant_credits.py ahmed@example.com 5         # $5.00 of credit
    grant_credits.py user_2abc... 2.50 --note "beta invite"
    grant_credits.py ahmed@example.com -1        # take $1 back, stopping at zero
    grant_credits.py ahmed@example.com --show    # the balance and how it got there

**An email we do not hold is asked of Clerk**, so credit can be granted to somebody who has not
signed in yet. Our `users` row is created on a person's first request; without that lookup an
invitation could only be pre-funded for people who had already visited.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from chessmark.db.credits import balance_of, grant, history_of  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402
from chessmark.db.users import resolve_user  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, AMBER = "\033[31m", "\033[32m", "\033[33m"


def _dollars(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an amount of dollars") from error


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user", help="an email address, a Clerk user id, or a Chessmark user id")
    parser.add_argument(
        "amount",
        nargs="?",
        type=_dollars,
        help="US dollars to add; negative takes credit away, stopping at zero",
    )
    parser.add_argument("--note", help="why, recorded on the ledger entry (AUTH-13)")
    parser.add_argument(
        "--show", action="store_true", help="print the balance and its history, changing nothing"
    )
    args = parser.parse_args()

    if args.amount is None and not args.show:
        parser.error("give an amount, or --show to read the balance")

    try:
        async with session_scope() as session:
            user = await resolve_user(session, args.user)
            if user is None:
                print(
                    f"{RED}no user matching {args.user!r}{OFF}\n"
                    f"{DIM}Give an email address, a Clerk user id, or a Chessmark user id.{OFF}",
                    file=sys.stderr,
                )
                return 1

            label = f"{BOLD}{user.email or user.clerk_user_id}{OFF} {DIM}{user.id}{OFF}"

            if args.show:
                balance = await balance_of(session, user.id)
                print(f"{label}\n  balance {BOLD}${balance:.2f}{OFF}")
                rows = await history_of(session, user.id)
                for row in rows:
                    sign = (
                        f"{GREEN}+{row.delta}{OFF}" if row.delta > 0 else f"{RED}{row.delta}{OFF}"
                    )
                    when = row.created_at.strftime("%Y-%m-%d %H:%M")
                    note = f" · {row.note}" if row.note else ""
                    unit = "" if row.unit == "usd" else f" {DIM}({row.unit}s){OFF}"
                    print(
                        f"  {DIM}{when}{OFF} {sign}{unit} → {row.balance_after:.4f} · {row.reason}{note}"
                    )
                if not rows:
                    print(f"  {DIM}no movements recorded{OFF}")
                return 0

            balance = await grant(session, user.id, args.amount, note=args.note)

        verb = "granted" if args.amount >= 0 else "revoked"
        print(
            f"{GREEN}{verb}{OFF} ${abs(args.amount):.2f} → balance {BOLD}${balance:.2f}{OFF}  {label}"
        )
        if args.amount < 0:
            # `grant` stops at zero, because a negative balance would be a debt rather than a
            # revocation, and the ledger records what actually moved rather than what was asked.
            print(f"{DIM}revocations stop at zero; the ledger records what moved{OFF}")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
