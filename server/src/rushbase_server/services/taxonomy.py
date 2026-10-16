"""Controlled vocabulary: fields, values, proposals, versioning and the worker snapshot.

Values the AI proposes start as ``proposed``; people approve, rename, merge or deprecate them.
Every change of the *approved* vocabulary bumps ``taxonomy_version`` and queues ``classify`` for
the assets that were classified with an older version (CPU only, no GPU stage).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import Stage, uuid7
from rushbase_server.models import (
    Asset,
    AssetTag,
    Batch,
    Job,
    StageResult,
    TaxField,
    TaxonomyVersion,
    TaxValue,
)
from rushbase_server.services import audit, queue
from rushbase_server.services.queue import INACTIVE_ASSET_STATUSES, OPEN_STATES
from rushbase_server.services.settings_store import get_setting

FLAGS_KEY = "taxonomy.flags"
AUTO_RECLASSIFY_KEY = "taxonomy.auto_reclassify"
MAX_EXAMPLES = 5


class TaxonomyError(Exception):
    """A taxonomy operation is invalid; ``status`` is the suggested HTTP status."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Actor:
    """Who changes the taxonomy (for the audit log)."""

    user_id: UUID | None
    ip: str | None = None


def _audit(
    session: AsyncSession, actor: Actor, action: str, target: UUID, details: dict[str, Any]
) -> None:
    audit.record(
        session,
        actor_kind="user" if actor.user_id else "system",
        actor_id=actor.user_id,
        action=f"taxonomy.{action}",
        target_kind="taxonomy",
        target_id=target,
        details=details,
        ip=actor.ip,
    )


async def current_version(session: AsyncSession) -> int:
    """The taxonomy version (1 until the first approved change)."""
    row = await session.get(TaxonomyVersion, 1)
    return row.version if row else 1


async def bump_version(session: AsyncSession) -> int:
    """Increment the version counter (creating it on first use)."""
    row = await session.get(TaxonomyVersion, 1)
    if row is None:
        row = TaxonomyVersion(id=1, version=2)
        session.add(row)
    else:
        row.version += 1
    await session.flush()
    return row.version


async def enqueue_reclassify(
    session: AsyncSession, version: int, now: datetime, config: queue.QueueConfig
) -> int:
    """Queue ``classify`` for assets whose latest classify used an older taxonomy version."""
    if not await get_setting(session, AUTO_RECLASSIFY_KEY, True):
        return 0
    latest = (
        select(StageResult.asset_id, StageResult.output["taxonomy_version"].as_integer().label("v"))
        .where(StageResult.stage == Stage.CLASSIFY.value, StageResult.ok.is_(True))
        .order_by(StageResult.asset_id, StageResult.finished_at.desc(), StageResult.id.desc())
        .ext(distinct_on(StageResult.asset_id))
        .subquery()
    )
    open_assets = select(Job.asset_id).where(
        Job.stage == Stage.CLASSIFY.value, Job.state.in_(OPEN_STATES)
    )
    rows = (
        await session.execute(
            select(Asset, Batch)
            .join(latest, latest.c.asset_id == Asset.id)
            .outerjoin(Batch, Batch.id == Asset.batch_id)
            .where(
                Asset.status.notin_(INACTIVE_ASSET_STATUSES),
                Asset.locked.is_(False),
                func.coalesce(latest.c.v, 0) < version,
                Asset.id.notin_(open_assets),
            )
        )
    ).all()
    for asset, batch in rows:
        session.add(
            Job(
                id=uuid7(),
                asset_id=asset.id,
                stage=Stage.CLASSIFY.value,
                state="queued",
                depends_on=[],
                priority=batch.priority if batch else 0,
                enqueued_at=batch.first_seen_at if batch else now,
                max_attempts=config.max_attempts,
                requirements={},
            )
        )
    return len(rows)


async def _changed(
    session: AsyncSession,
    now: datetime,
    config: queue.QueueConfig,
) -> int:
    version = await bump_version(session)
    await enqueue_reclassify(session, version, now, config)
    return version


# --- fields ----------------------------------------------------------------------------------


async def create_field(
    session: AsyncSession,
    actor: Actor,
    *,
    key: str,
    label_de: str,
    label_en: str,
    kind: str,
    description: str,
    sort: int,
) -> TaxField:
    """Create a field; the key must be unique."""
    if (await session.execute(select(TaxField.id).where(TaxField.key == key))).first():
        raise TaxonomyError(f"field {key!r} already exists")
    field = TaxField(
        key=key,
        label_de=label_de,
        label_en=label_en,
        kind=kind,
        description=description,
        sort=sort,
    )
    session.add(field)
    await session.flush()
    _audit(session, actor, "field.create", field.id, {"key": key})
    return field


async def update_field(
    session: AsyncSession,
    actor: Actor,
    field_id: UUID,
    changes: dict[str, Any],
    now: datetime,
    config: queue.QueueConfig,
) -> TaxField:
    """Change labels, kind, description or sort order (a version bump when it affects workers)."""
    field = await session.get(TaxField, field_id)
    if field is None:
        raise TaxonomyError("field not found", 404)
    for name, value in changes.items():
        setattr(field, name, value)
    await session.flush()
    _audit(session, actor, "field.update", field.id, changes)
    await _changed(session, now, config)
    return field


async def delete_field(session: AsyncSession, actor: Actor, field_id: UUID) -> None:
    """Delete a field that has neither values nor tags."""
    field = await session.get(TaxField, field_id)
    if field is None:
        raise TaxonomyError("field not found", 404)
    used = await session.scalar(
        select(func.count()).select_from(TaxValue).where(TaxValue.field_id == field_id)
    )
    if used:
        raise TaxonomyError("field still has values; deprecate or merge them first")
    await session.delete(field)
    _audit(session, actor, "field.delete", field_id, {"key": field.key})


# --- values ----------------------------------------------------------------------------------


async def _value(session: AsyncSession, value_id: UUID) -> TaxValue:
    row = await session.get(TaxValue, value_id)
    if row is None:
        raise TaxonomyError("value not found", 404)
    return row


async def _ensure_unique(
    session: AsyncSession, field_id: UUID, value: str, except_id: UUID | None = None
) -> None:
    stmt = select(TaxValue.id).where(
        TaxValue.field_id == field_id, func.lower(TaxValue.value) == value.lower()
    )
    if except_id is not None:
        stmt = stmt.where(TaxValue.id != except_id)
    if (await session.execute(stmt)).first():
        raise TaxonomyError(f"value {value!r} already exists in this field")


def _clean_synonyms(synonyms: Sequence[str], value: str) -> list[str]:
    seen = {value.lower()}
    cleaned = []
    for item in synonyms:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            cleaned.append(item)
    return cleaned


async def create_value(
    session: AsyncSession,
    actor: Actor,
    field_id: UUID,
    *,
    value: str,
    label_de: str | None,
    label_en: str | None,
    synonyms: Sequence[str],
    description: str,
    parent_id: UUID | None,
    approved: bool,
    now: datetime,
    config: queue.QueueConfig,
) -> TaxValue:
    """Add a value (``approved`` only for people allowed to approve)."""
    if await session.get(TaxField, field_id) is None:
        raise TaxonomyError("field not found", 404)
    value = value.strip()
    await _ensure_unique(session, field_id, value)
    row = TaxValue(
        field_id=field_id,
        value=value,
        label_de=label_de,
        label_en=label_en,
        synonyms=_clean_synonyms(synonyms, value),
        description=description,
        parent_id=parent_id,
        status="approved" if approved else "proposed",
    )
    row.example_asset_ids = []
    session.add(row)
    await session.flush()
    _audit(session, actor, "value.create", row.id, {"value": value, "status": row.status})
    if approved:
        await _changed(session, now, config)
    return row


async def update_value(
    session: AsyncSession,
    actor: Actor,
    value_id: UUID,
    changes: dict[str, Any],
    now: datetime,
    config: queue.QueueConfig,
) -> TaxValue:
    """Rename (the old name becomes a synonym), relabel, describe or re-parent a value."""
    row = await _value(session, value_id)
    details = dict(changes)
    if (new := changes.pop("value", None)) is not None and new.strip() != row.value:
        new = new.strip()
        await _ensure_unique(session, row.field_id, new, row.id)
        changes.setdefault("synonyms", list(row.synonyms))
        changes["synonyms"] = [*changes["synonyms"], row.value]
        row.value = new
    if "synonyms" in changes:
        changes["synonyms"] = _clean_synonyms(changes["synonyms"], row.value)
    for name, item in changes.items():
        setattr(row, name, item)
    await session.flush()
    _audit(session, actor, "value.update", row.id, details)
    if row.status == "approved":
        await _changed(session, now, config)
    return row


async def set_status(
    session: AsyncSession,
    actor: Actor,
    value_id: UUID,
    status: str,
    now: datetime,
    config: queue.QueueConfig,
) -> TaxValue:
    """Approve or deprecate a value (deprecated values vanish from snapshot and facets)."""
    row = await _value(session, value_id)
    previous = row.status
    row.status = status
    await session.flush()
    _audit(session, actor, f"value.{status}", row.id, {"value": row.value, "from": previous})
    if "approved" in (previous, status):
        await _changed(session, now, config)
    return row


async def add_synonym(
    session: AsyncSession,
    actor: Actor,
    value_id: UUID,
    synonym: str,
    now: datetime,
    config: queue.QueueConfig,
) -> TaxValue:
    """Add one synonym to a value."""
    row = await _value(session, value_id)
    row.synonyms = _clean_synonyms([*row.synonyms, synonym], row.value)
    await session.flush()
    _audit(session, actor, "value.synonym", row.id, {"synonym": synonym})
    if row.status == "approved":
        await _changed(session, now, config)
    return row


async def merge_values(
    session: AsyncSession,
    actor: Actor,
    source_id: UUID,
    target_id: UUID,
    now: datetime,
    config: queue.QueueConfig,
) -> TaxValue:
    """Fold ``source`` into ``target``: tags move, name and synonyms become synonyms."""
    source, target = await _value(session, source_id), await _value(session, target_id)
    if source.id == target.id or source.field_id != target.field_id:
        raise TaxonomyError("values must be different values of the same field")
    duplicates = (
        select(AssetTag.id)
        .where(AssetTag.value_id == source.id)
        .where(
            text(
                "EXISTS (SELECT 1 FROM asset_tag o WHERE o.value_id = :target "
                "AND o.asset_id = asset_tag.asset_id AND o.source = asset_tag.source "
                "AND o.shot_id IS NOT DISTINCT FROM asset_tag.shot_id)"
            ).bindparams(target=target.id)
        )
    )
    await session.execute(delete(AssetTag).where(AssetTag.id.in_(duplicates)))
    await session.execute(
        update(AssetTag).where(AssetTag.value_id == source.id).values(value_id=target.id)
    )
    target.synonyms = _clean_synonyms(
        [*target.synonyms, source.value, *source.synonyms], target.value
    )
    target.proposal_count += source.proposal_count
    examples = [*target.example_asset_ids]
    examples += [e for e in source.example_asset_ids if e not in examples]
    target.example_asset_ids = examples[:MAX_EXAMPLES]
    approved = "approved" in (source.status, target.status)
    await session.delete(source)
    await session.flush()
    _audit(session, actor, "value.merge", target.id, {"merged": source.value, "into": target.value})
    if approved:
        await _changed(session, now, config)
    return target


# --- reads -----------------------------------------------------------------------------------


async def fields_with_values(
    session: AsyncSession, statuses: Sequence[str] | None = None
) -> list[tuple[TaxField, list[TaxValue]]]:
    """All fields (by sort order) with their values, optionally restricted by status."""
    fields = (
        (await session.execute(select(TaxField).order_by(TaxField.sort, TaxField.key)))
        .scalars()
        .all()
    )
    stmt = select(TaxValue).order_by(TaxValue.value)
    if statuses is not None:
        stmt = stmt.where(TaxValue.status.in_(statuses))
    values = (await session.execute(stmt)).scalars().all()
    by_field: dict[UUID, list[TaxValue]] = {}
    for row in values:
        by_field.setdefault(row.field_id, []).append(row)
    return [(f, by_field.get(f.id, [])) for f in fields]


async def tag_counts(session: AsyncSession) -> dict[UUID, int]:
    """Number of distinct assets per value."""
    rows = await session.execute(
        select(AssetTag.value_id, func.count(func.distinct(AssetTag.asset_id))).group_by(
            AssetTag.value_id
        )
    )
    return {value_id: count for value_id, count in rows}  # noqa: C416


async def worker_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Approved vocabulary and review flags for workers (``GET /worker/taxonomy``)."""
    fields = await fields_with_values(session, ("approved",))
    flags = await get_setting(session, FLAGS_KEY, [])
    return {
        "version": await current_version(session),
        "fields": [
            {
                "key": f.key,
                "label_de": f.label_de,
                "label_en": f.label_en,
                "kind": f.kind,
                "values": [
                    {"value": v.value, "synonyms": list(v.synonyms), "description": v.description}
                    for v in values
                ],
            }
            for f, values in fields
        ],
        "flags": [
            {
                "key": item["key"],
                "description_de": item.get("description_de", ""),
                "description_en": item.get("description_en", ""),
            }
            for item in flags
        ],
    }


async def get_flags(session: AsyncSession) -> list[dict[str, Any]]:
    """The configured review flags."""
    flags: list[dict[str, Any]] = await get_setting(session, FLAGS_KEY, [])
    return flags
