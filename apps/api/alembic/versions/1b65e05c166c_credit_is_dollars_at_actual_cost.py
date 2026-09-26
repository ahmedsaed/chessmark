"""credit is dollars, spent at actual cost

Revision ID: 1b65e05c166c
Revises: e86b62400f55
Create Date: 2026-09-26 00:00:00.000000

ADR-0052. A balance becomes US dollars and a game is charged per turn at what it actually cost, so:

* **Every balance resets to zero.** Credits did not convert — the owner's decision, and only
  testers held any. Each nonzero balance gets one closing `retired` row in credits, so the credit
  history still sums to what it was and then to zero; nothing is deleted from the ledger.
* **The ledger counts dollars.** `delta` and `balance_after` become the money type, a `unit`
  column tells the old rows (`credit`) from the new (`usd`), and `turn_id` names the turn a
  `turn` row paid for.
* **The price band stays, as a band.** `credit_cost` was 1, 2, 3 or 6 credits; `price_tier` is the
  same bands numbered 1 to 4. The administrator's override goes: it existed to change what a user
  was charged, and nobody is charged by band any more. Stored tournament fields that filtered on
  the old numbers are rewritten to select the same models.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b65e05c166c"
down_revision: str | None = "e86b62400f55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USD = sa.Numeric(16, 8)


def upgrade() -> None:
    # ---------------------------------------------------------------- the ledger counts dollars
    op.add_column(
        "credit_ledger", sa.Column("unit", sa.Text(), server_default="usd", nullable=False)
    )
    op.execute("UPDATE credit_ledger SET unit = 'credit'")
    op.alter_column("credit_ledger", "delta", type_=USD, existing_type=sa.Integer())
    op.alter_column("credit_ledger", "balance_after", type_=USD, existing_type=sa.Integer())
    op.add_column("credit_ledger", sa.Column("turn_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_credit_ledger_turn_id_turns",
        "credit_ledger",
        "turns",
        ["turn_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_credit_ledger_turn_id", "credit_ledger", ["turn_id"])

    # ---------------------------------------------------------------- every balance to zero
    # One closing row per balance that held anything, in the unit it was held in.
    op.execute(
        """
        INSERT INTO credit_ledger (user_id, delta, balance_after, reason, unit, note, created_at)
        SELECT id, -credit_balance, 0, 'retired', 'credit',
               'credits retired: a balance is dollars now (ADR-0052)', now()
        FROM users
        WHERE credit_balance <> 0
        """
    )
    op.drop_column("users", "credit_balance")
    op.add_column("users", sa.Column("balance_usd", USD, server_default="0", nullable=False))

    # ---------------------------------------------------------------- a band, not a price
    op.add_column(
        "model_registry", sa.Column("price_tier", sa.Integer(), server_default="1", nullable=False)
    )
    # The derived band, not the override: an override was a charging decision, and the next
    # catalogue sync rewrites every band from the model's own prices anyway.
    op.execute(
        "UPDATE model_registry SET price_tier = CASE WHEN credit_cost >= 6 THEN 4 "
        "ELSE credit_cost END"
    )
    op.drop_column("model_registry", "credit_cost_override")
    op.drop_column("model_registry", "credit_cost")

    # A stored field re-resolves every tick, so its keys must be the ones `filter_from_json` reads.
    # Bounds map onto the same models: credits were 1, 2, 3 and 6, so a minimum of 4 or 5 meant
    # "the top band" and a maximum of 4 or 5 meant "up to the third".
    op.execute(
        """
        UPDATE tournaments
        SET field_filter = (field_filter - 'min_credit_cost')
            || jsonb_build_object('min_price_tier',
                CASE WHEN (field_filter->>'min_credit_cost')::int >= 4 THEN 4
                     ELSE (field_filter->>'min_credit_cost')::int END)
        WHERE field_filter ? 'min_credit_cost' AND field_filter->'min_credit_cost' <> 'null'
        """
    )
    op.execute(
        """
        UPDATE tournaments
        SET field_filter = (field_filter - 'max_credit_cost')
            || jsonb_build_object('max_price_tier',
                CASE WHEN (field_filter->>'max_credit_cost')::int >= 6 THEN 4
                     WHEN (field_filter->>'max_credit_cost')::int >= 4 THEN 3
                     ELSE (field_filter->>'max_credit_cost')::int END)
        WHERE field_filter ? 'max_credit_cost' AND field_filter->'max_credit_cost' <> 'null'
        """
    )
    op.execute(
        "UPDATE tournaments SET field_filter = field_filter - 'min_credit_cost' - 'max_credit_cost'"
    )

    # The stored one-line description says the bound in the old words ("≤1 credits"), and a
    # standings page prints it. Rewritten with the same mapping, so the page names the filter the
    # field is actually resolved by.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, field_filter->>'describes' FROM tournaments WHERE field_filter->>'describes' LIKE '%credits%'"
        )
    ).all()
    for tournament_id, describes in rows:
        connection.execute(
            sa.text(
                "UPDATE tournaments SET field_filter = jsonb_set(field_filter, '{describes}', "
                "to_jsonb(CAST(:describes AS text))) WHERE id = :id"
            ),
            {"id": tournament_id, "describes": _describe_in_tiers(describes)},
        )


def _describe_in_tiers(describes: str) -> str:
    def lower(match: re.Match[str]) -> str:
        return f"price tier ≥{min(int(match.group(1)), 4)}"

    def upper(match: re.Match[str]) -> str:
        credits = int(match.group(1))
        return f"price tier ≤{4 if credits >= 6 else min(credits, 3)}"

    return re.sub(r"≤(\d+) credits", upper, re.sub(r"≥(\d+) credits", lower, describes))


def downgrade() -> None:
    # Credit balances come back from their own history, less the `retired` row; the dollars since
    # have no meaning in credits and are dropped.
    op.execute(
        """
        UPDATE tournaments
        SET field_filter = (field_filter - 'min_price_tier' - 'max_price_tier')
            || jsonb_strip_nulls(jsonb_build_object(
                'min_credit_cost',
                CASE WHEN (field_filter->>'min_price_tier')::int = 4 THEN 6
                     ELSE (field_filter->>'min_price_tier')::int END,
                'max_credit_cost',
                CASE WHEN (field_filter->>'max_price_tier')::int = 4 THEN 6
                     ELSE (field_filter->>'max_price_tier')::int END))
        """
    )
    op.add_column(
        "model_registry", sa.Column("credit_cost", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("model_registry", sa.Column("credit_cost_override", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE model_registry SET credit_cost = CASE WHEN price_tier = 4 THEN 6 "
        "ELSE price_tier END"
    )
    op.drop_column("model_registry", "price_tier")

    op.drop_column("users", "balance_usd")
    op.add_column(
        "users", sa.Column("credit_balance", sa.Integer(), server_default="0", nullable=False)
    )

    op.execute(
        """
        UPDATE users SET credit_balance = held.total
        FROM (
            SELECT user_id, SUM(delta)::int AS total FROM credit_ledger
            WHERE unit = 'credit' AND reason <> 'retired' GROUP BY user_id
        ) AS held
        WHERE users.id = held.user_id
        """
    )

    op.drop_index("ix_credit_ledger_turn_id", table_name="credit_ledger")
    op.drop_constraint("fk_credit_ledger_turn_id_turns", "credit_ledger", type_="foreignkey")
    op.drop_column("credit_ledger", "turn_id")
    # Dollar rows cannot become credits; they go, and the credit rows are as they were.
    op.execute("DELETE FROM credit_ledger WHERE unit = 'usd'")
    op.execute("DELETE FROM credit_ledger WHERE reason = 'retired'")
    op.alter_column("credit_ledger", "balance_after", type_=sa.Integer(), existing_type=USD)
    op.alter_column("credit_ledger", "delta", type_=sa.Integer(), existing_type=USD)
    op.drop_column("credit_ledger", "unit")
