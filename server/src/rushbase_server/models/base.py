"""Declarative base, naming convention and column helpers shared by all models."""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from rushbase_core import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class of all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def enum_check(column: str, values: Iterable[str], name: str | None = None) -> CheckConstraint:
    """Return ``CHECK (column IN (...))``: enums are text + CHECK for easy additive evolution."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name or column)


def pk() -> Mapped[UUID]:
    """Primary key column holding a time-ordered UUIDv7 generated in Python."""
    return mapped_column(Uuid, primary_key=True, default=uuid7)


def now_column(*, onupdate: bool = False) -> Mapped[datetime]:
    """``timestamptz NOT NULL DEFAULT now()`` (optionally refreshed on update)."""
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now() if onupdate else None,
    )
