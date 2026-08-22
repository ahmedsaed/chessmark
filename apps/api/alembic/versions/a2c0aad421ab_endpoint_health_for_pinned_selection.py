"""endpoint health for pinned selection

Endpoint selection is by uptime (ADR-0015), so these columns are load-bearing rather than
informational. Stored rather than fetched live, so a finished game can always say what the
numbers were at the moment its endpoint was chosen.

Revision ID: a2c0aad421ab
Revises: b9db296afe6c
Create Date: 2026-08-23 00:45:23.210601
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2c0aad421ab"
down_revision: str | None = "b9db296afe6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_endpoints", sa.Column("uptime_30m", sa.Float(), nullable=True))
    op.add_column("model_endpoints", sa.Column("uptime_1d", sa.Float(), nullable=True))
    op.add_column("model_endpoints", sa.Column("throughput", sa.Float(), nullable=True))
    op.add_column("model_endpoints", sa.Column("latency_ms", sa.Float(), nullable=True))
    op.add_column(
        "model_endpoints", sa.Column("supports_implicit_caching", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("model_endpoints", "supports_implicit_caching")
    op.drop_column("model_endpoints", "latency_ms")
    op.drop_column("model_endpoints", "throughput")
    op.drop_column("model_endpoints", "uptime_1d")
    op.drop_column("model_endpoints", "uptime_30m")
