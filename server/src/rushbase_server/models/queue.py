"""Append-only stage results and the job queue."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
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

from rushbase_core import ALL_STAGES
from rushbase_server.models.base import Base, enum_check, now_column, pk

STAGES = tuple(s.value for s in ALL_STAGES)
JOB_STATES = ("blocked", "queued", "leased", "done", "failed", "cancelled")
DEFAULT_MAX_ATTEMPTS = 5


class StageResult(Base):
    """Result of one stage run. Never updated; the latest per (asset, stage) is current."""

    __tablename__ = "stage_result"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    job_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("job.id", ondelete="SET NULL"))
    stage: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("worker.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = now_column()
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    ok: Mapped[bool] = mapped_column(Boolean)
    output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        enum_check("stage", STAGES),
        Index(
            "ix_stage_result_asset_id_stage_finished_at",
            "asset_id",
            "stage",
            text("finished_at DESC"),
        ),
        Index("ix_stage_result_finished_at", "finished_at"),
        # At most one successful result per job: duplicate submissions are ignored.
        Index(
            "ux_stage_result_job_id",
            "job_id",
            unique=True,
            postgresql_where=text("job_id IS NOT NULL AND ok"),
        ),
    )


class Job(Base):
    """A unit of work for one stage of one asset."""

    __tablename__ = "job"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), server_default="queued", default="queued")
    depends_on: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid), server_default=text("'{}'::uuid[]"), default=list
    )
    priority: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    enqueued_at: Mapped[datetime] = now_column()  # FIFO key: first_seen_at of the batch
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, server_default=str(DEFAULT_MAX_ATTEMPTS), default=DEFAULT_MAX_ATTEMPTS
    )
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("worker.id", ondelete="SET NULL")
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (
        enum_check("stage", STAGES),
        enum_check("state", JOB_STATES),
        Index(
            "ix_job_queue",
            "stage",
            text("priority DESC"),
            "enqueued_at",
            postgresql_where=text("state = 'queued'"),
        ),
        Index(
            "ix_job_lease_expires_at", "lease_expires_at", postgresql_where=text("state = 'leased'")
        ),
        Index("ix_job_asset_id", "asset_id"),
        Index("ix_job_state_stage", "state", "stage"),
    )
