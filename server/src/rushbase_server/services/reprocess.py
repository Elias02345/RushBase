"""Queue an analysis stage again for chosen assets. Only jobs are created; files are untouched.

Besides explicit ids and filters, a stage can be queued again by the model that produced its
latest successful result (``model_id``, ``model_version`` or ``profile``): "everything analysed
with a version below Y" or "everything not analysed with Y". The model attributes come from the
stage output (``model`` / ``text_model``) and fall back to the result's own columns.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import ColumnElement, Subquery, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from rushbase_core import Stage, uuid7, version_lt
from rushbase_server.models import Asset, Batch, Job, StageResult
from rushbase_server.services import audit, queue
from rushbase_server.services.catalog import AssetFilters, apply_filters

MAX_ASSETS = 500
MAX_BY_MODEL = 100_000
CHUNK = 2000

ModelField = Literal["model_version", "model_id", "profile"]
ModelOp = Literal["lt", "ne"]


def _attr(key: str, column: InstrumentedAttribute[str | None] | None) -> ColumnElement[str]:
    """Output ``model.<key>``, else ``text_model.<key>``, else the result column."""
    parts: list[Any] = [
        StageResult.output["model"][key].astext,
        StageResult.output["text_model"][key].astext,
    ]
    if column is not None:
        parts.append(column)
    return func.coalesce(*parts)


_ATTRIBUTES: dict[str, ColumnElement[str]] = {
    "model_id": _attr("model_id", StageResult.model_id),
    "model_version": _attr("revision", StageResult.model_version),
    "profile": _attr("profile", None),
}


def _latest_results() -> Subquery:
    """Latest successful result per (asset, stage) with its effective model attributes."""
    return (
        select(
            StageResult.asset_id.label("asset_id"),
            StageResult.stage.label("stage"),
            _ATTRIBUTES["model_id"].label("model_id"),
            _ATTRIBUTES["model_version"].label("model_version"),
            _ATTRIBUTES["profile"].label("profile"),
        )
        .where(StageResult.ok.is_(True))
        .order_by(
            StageResult.asset_id,
            StageResult.stage,
            StageResult.finished_at.desc(),
            StageResult.id.desc(),
        )
        .ext(distinct_on(StageResult.asset_id, StageResult.stage))
        .subquery()
    )


@dataclass(frozen=True)
class ModelUse:
    """How many assets have a stage's latest result from one model."""

    stage: str
    model_id: str | None
    model_version: str | None
    profile: str | None
    assets: int


async def model_usage(session: AsyncSession) -> list[ModelUse]:
    """Current models per stage with asset counts (one grouped pass over the latest results)."""
    latest = _latest_results()
    rows = await session.execute(
        select(
            latest.c.stage,
            latest.c.model_id,
            latest.c.model_version,
            latest.c.profile,
            func.count(),
        )
        .join(Asset, Asset.id == latest.c.asset_id)
        .where(Asset.status.notin_(queue.INACTIVE_ASSET_STATUSES))
        .group_by(latest.c.stage, latest.c.model_id, latest.c.model_version, latest.c.profile)
        .order_by(latest.c.stage, func.count().desc())
    )
    return [ModelUse(s, i, v, p, int(n)) for s, i, v, p, n in rows]


def _matches(current: str | None, op: ModelOp, wanted: str) -> bool:
    """``lt``: ``current`` is older (missing counts as oldest); ``ne``: it differs."""
    if op == "lt":
        return version_lt(current or "0", wanted)
    return current != wanted


@dataclass(frozen=True)
class ModelSelection:
    """Assets whose latest ``stage`` result matches a model comparison."""

    asset_ids: list[UUID]
    matched: int
    truncated: bool
    by_value: list[tuple[str | None, int]]


async def select_by_model(
    session: AsyncSession,
    stage: Stage,
    field: ModelField,
    op: ModelOp,
    value: str,
    limit: int = MAX_BY_MODEL,
) -> ModelSelection:
    """Select assets by comparing the model attribute ``field`` of their latest ``stage`` result.

    ``matched`` counts all matching assets; ``asset_ids`` holds at most ``limit`` of them.
    Assets without any successful result for the stage are not selected (nothing to compare).
    """
    latest = _latest_results()
    column = latest.c[field]
    base = (
        select(column, func.count())
        .join(Asset, Asset.id == latest.c.asset_id)
        .where(latest.c.stage == stage.value, Asset.status.notin_(queue.INACTIVE_ASSET_STATUSES))
        .group_by(column)
    )
    groups = [(v, int(n)) for v, n in await session.execute(base) if _matches(v, op, value)]
    groups.sort(key=lambda g: (-g[1], g[0] or ""))
    matched = sum(n for _, n in groups)
    if not groups:
        return ModelSelection([], 0, False, [])
    values = [v for v, _ in groups if v is not None]
    condition: ColumnElement[bool] = column.in_(values) if values else column.is_(None)
    if values and any(v is None for v, _ in groups):
        condition = or_(condition, column.is_(None))
    ids = (
        await session.execute(
            select(latest.c.asset_id)
            .join(Asset, Asset.id == latest.c.asset_id)
            .where(
                latest.c.stage == stage.value,
                Asset.status.notin_(queue.INACTIVE_ASSET_STATUSES),
                condition,
            )
            .order_by(latest.c.asset_id)
            .limit(limit)
        )
    ).scalars()
    asset_ids = list(ids)
    return ModelSelection(asset_ids, matched, matched > len(asset_ids), groups)


async def select_assets(session: AsyncSession, filters: AssetFilters) -> list[UUID]:
    """Ids of up to ``MAX_ASSETS`` assets matching ``filters`` (oldest first)."""
    stmt = apply_filters(select(Asset.id), filters).order_by(Asset.id).limit(MAX_ASSETS)
    return list((await session.execute(stmt)).scalars())


async def _enqueue_chunk(
    session: AsyncSession,
    stage: Stage,
    asset_ids: Sequence[UUID],
    now: datetime,
    config: queue.QueueConfig,
) -> int:
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
    return queued


async def enqueue_stage(
    session: AsyncSession,
    stage: Stage,
    asset_ids: Sequence[UUID],
    now: datetime,
    config: queue.QueueConfig,
    actor_kind: str,
    actor_id: UUID | None,
    details: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Create a queued ``stage`` job per asset; returns ``(queued, skipped)``.

    Skipped: unknown, quarantined/trashed/duplicate assets and assets with an open job already.
    ``details`` is added to the audit entry.
    """
    queued = 0
    for start in range(0, len(asset_ids), CHUNK):
        queued += await _enqueue_chunk(
            session, stage, asset_ids[start : start + CHUNK], now, config
        )
    audit.record(
        session,
        actor_kind=actor_kind,
        actor_id=actor_id,
        action="queue.reprocess",
        details={
            "stage": stage.value,
            "assets": queued,
            "requested": len(asset_ids),
            **(details or {}),
        },
    )
    return queued, len(asset_ids) - queued
