"""Backlog and system statistics. All queries are answered from the database alone.

Nothing here depends on a worker being online (ADR-0012): the backlog per stage, with file
counts, bytes and media duration, is available at any time.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, AssetFile, Batch, InputFile, Job, Worker
from rushbase_server.services.queue import OPEN_STATES, is_paused


@dataclass
class StageBacklog:
    """Pending work of one stage."""

    jobs: dict[str, int] = field(default_factory=dict)
    pending_jobs: int = 0
    pending_files: int = 0
    pending_bytes: int = 0
    pending_duration_ms: int = 0
    duration_unknown_jobs: int = 0


@dataclass
class SystemStatus:
    """Snapshot returned by ``GET /api/v1/status``."""

    queue_paused: bool
    stages: dict[str, StageBacklog]
    assets: dict[str, int]
    input_files: dict[str, int]
    batches: dict[str, int]
    workers_online: int
    workers_total: int


async def stage_backlog(session: AsyncSession) -> dict[str, StageBacklog]:
    """Per stage: job counts by state and the size of the pending (open) work."""
    result: dict[str, StageBacklog] = {}
    for stage, state, count in await session.execute(
        select(Job.stage, Job.state, func.count()).group_by(Job.stage, Job.state)
    ):
        result.setdefault(stage, StageBacklog()).jobs[state] = count
    files = (
        select(AssetFile.asset_id, func.count().label("n")).group_by(AssetFile.asset_id).subquery()
    )
    pending = await session.execute(
        select(
            Job.stage,
            func.count(),
            func.coalesce(func.sum(files.c.n), 0),
            func.coalesce(func.sum(Asset.size_total), 0),
            func.coalesce(func.sum(Asset.duration_ms), 0),
            func.count().filter(Asset.duration_ms.is_(None)),
        )
        .join(Asset, Asset.id == Job.asset_id)
        .outerjoin(files, files.c.asset_id == Job.asset_id)
        .where(Job.state.in_(OPEN_STATES))
        .group_by(Job.stage)
    )
    for stage, jobs, file_count, size, duration, unknown in pending:
        entry = result.setdefault(stage, StageBacklog())
        entry.pending_jobs = jobs
        entry.pending_files = int(file_count)
        entry.pending_bytes = int(size)
        entry.pending_duration_ms = int(duration)
        entry.duration_unknown_jobs = unknown
    return result


async def _counts(session: AsyncSession, column: object) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).group_by(column))  # type: ignore[call-overload]
    return dict(rows.all())


async def system_status(
    session: AsyncSession, now: datetime, worker_online_seconds: float
) -> SystemStatus:
    """Collect the full status snapshot."""
    online_since = now - timedelta(seconds=worker_online_seconds)
    return SystemStatus(
        queue_paused=await is_paused(session),
        stages=await stage_backlog(session),
        assets=await _counts(session, Asset.status),
        input_files=await _counts(session, InputFile.state),
        batches=await _counts(session, Batch.status),
        workers_online=int(
            await session.scalar(
                select(func.count())
                .select_from(Worker)
                .where(Worker.last_seen_at >= online_since, Worker.status != "offline")
            )
            or 0
        ),
        workers_total=int(await session.scalar(select(func.count()).select_from(Worker)) or 0),
    )
