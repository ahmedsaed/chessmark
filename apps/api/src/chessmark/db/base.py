"""Declarative base and shared column conventions."""

from __future__ import annotations

import datetime as dt
import uuid
from enum import Enum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Deterministic constraint names. Without these, Alembic autogenerate produces churn on every
#: run and `alembic check` becomes useless as a drift detector.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: dict[Any, Any] = {  # noqa: RUF012
        dt.datetime: sa.TIMESTAMP(timezone=True),
        uuid.UUID: sa.Uuid(as_uuid=True),
    }


def enum_column(enum_cls: type[Enum], length: int = 40) -> sa.Enum:
    """A string-backed enum column.

    Stores the enum *value* (`"white"`), not its name (`"WHITE"`), and creates no database CHECK
    constraint. Python is the source of truth for the allowed set — adding a termination reason
    should not require a migration, and a stale CHECK is a worse failure than a stale value.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    """Primary key for anything with a public identity — game URLs must not be enumerable."""
    return mapped_column(primary_key=True, default=uuid.uuid4)


def bigint_pk() -> Mapped[int]:
    """Primary key for append-only log rows, which are never addressed from outside."""
    return mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)


def created_at() -> Mapped[dt.datetime]:
    return mapped_column(server_default=sa.func.now(), nullable=False)


def updated_at() -> Mapped[dt.datetime]:
    return mapped_column(server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False)


#: Money. Exact, never floating point — invariant 4 says cost is computed from real token counts,
#: and a float would quietly undermine that at the storage layer.
USD = sa.Numeric(16, 8)

#: Per-token prices are tiny; they need far more decimal places than a total does.
USD_PER_TOKEN = sa.Numeric(20, 12)
