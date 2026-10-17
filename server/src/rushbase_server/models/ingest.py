"""Workers, enrollment tokens, ingest batches and scanned input files."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rushbase_server.models.base import Base, enum_check, now_column, pk

WORKER_STATUSES = ("online", "offline", "draining")
BATCH_STATUSES = ("scanning", "waiting", "processing", "done", "error")
INPUT_FILE_STATES = ("copying", "stable", "grouped", "ignored", "gone")


class Worker(Base):
    """A registered worker process."""

    __tablename__ = "worker"

    id: Mapped[UUID] = pk()
    name: Mapped[str] = mapped_column(Text)
    hostname: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="online", default="online")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_stage: Mapped[str | None] = mapped_column(String(16))
    current_job_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (enum_check("status", WORKER_STATUSES),)


class EnrollmentToken(Base):
    """One-time token an admin hands to a new worker (stored hashed)."""

    __tablename__ = "enrollment_token"

    id: Mapped[UUID] = pk()
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    note: Mapped[str] = mapped_column(Text, server_default="", default="")
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_worker: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("worker.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()


class Batch(Base):
    """An ingest batch: a top-level sub folder of ``Input/`` (or an automatic per-day batch)."""

    __tablename__ = "batch"

    id: Mapped[UUID] = pk()
    input_path: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    hint_date: Mapped[date | None] = mapped_column(Date)
    hint_location: Mapped[str | None] = mapped_column(Text)
    hint_project: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="scanning", default="scanning")
    priority: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    paused: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    first_seen_at: Mapped[datetime] = now_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (enum_check("status", BATCH_STATUSES), Index("ix_batch_status", "status"))


class InputFile(Base):
    """A file seen in ``Input/``; tracks copy stability until it is grouped into an asset."""

    __tablename__ = "input_file"

    id: Mapped[UUID] = pk()
    batch_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("batch.id", ondelete="CASCADE"))
    rel_path: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stable_count: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    first_seen_at: Mapped[datetime] = now_column()
    last_seen_at: Mapped[datetime] = now_column()
    last_changed_at: Mapped[datetime] = now_column()
    state: Mapped[str] = mapped_column(String(16), server_default="copying", default="copying")
    quick_fp: Mapped[str | None] = mapped_column(Text)
    asset_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="SET NULL"))

    __table_args__ = (
        enum_check("state", INPUT_FILE_STATES),
        Index("ix_input_file_batch_id_state", "batch_id", "state"),
        Index("ix_input_file_asset_id", "asset_id"),
        Index("ix_input_file_quick_fp", "quick_fp"),
    )
