"""Queue an analysis stage again for chosen assets. Only jobs are created; files are untouched."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import Stage, uuid7
from rushbase_server.models import Asset, Batch, Job
from rushbase_server.services import audit, queue
from rushbase_server.services.catalog import AssetFilters, apply_filters

MAX_ASSETS = 500


async def select_assets(session: AsyncSession, filters: AssetFilters) -> list[UUID]:
    """Ids of up to ``MAX_ASSETS`` assets matching ``filters`` (oldest first)."""
    stmt = apply_filters(select(Asset.id), filters).order_by(Asset.id).limit(MAX_ASSETS)
    return list((await session.execute(stmt)).scalars())


async def enqueue_stage(
    session: AsyncSession,
    stage: Stage,
    asset_ids: Sequence[UUID],
    now: datetime,
    config: queue.QueueConfig,
    actor_kind: str,
    actor_id: UUID | None,
) -> tuple[int, int]:
    """Create a queued ``stage`` job per asset; returns ``(queued, skipped)``.

    Skipped: unknown, quarantined/trashed/duplicate assets and assets with an open job already.
    """
    rows = (
        await session.execute(
            select(Asset, Batch)
            .outerjoin(Batch, Batch.id == Asset.batch_id)
            .where(Asset.id.in_(list(asset_ids)))
        )
    ).all()
    open_assets = set(
        (
            await session.execute(
                select(Job.asset_id).where(
                    Job.stage == stage.value,
                    Job.state.in_(queue.OPEN_STATES),
                    Job.asset_id.in_(list(asset_ids)),
                )
            )
        ).scalars()
    )
    queued = 0
    for asset, batch in rows:
        if asset.status in queue.INACTIVE_ASSET_STATUSES or asset.id in open_assets:
            continue
        session.add(
            Job(
                id=uuid7(),
                asset_id=asset.id,
                stage=stage.value,
                state="queued",
                depends_on=[],
                priority=batch.priority if batch else 0,
                enqueued_at=now,
                max_attempts=config.max_attempts,
                requirements={},
            )
        )
        queued += 1
    audit.record(
        session,
        actor_kind=actor_kind,
        actor_id=actor_id,
        action="queue.reprocess",
        details={"stage": stage.value, "assets": queued, "requested": len(asset_ids)},
    )
    return queued, len(asset_ids) - queued
