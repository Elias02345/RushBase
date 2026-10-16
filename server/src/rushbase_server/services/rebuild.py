"""Rebuild the database from sidecars (``rushbase-server rebuild-from-sidecars``).

Walks ``Content/**/_meta/*.json`` and ``.rushbase/meta/*.json`` and recreates batch, asset,
asset_file, asset_metadata and shot rows plus the AI section: shot and asset descriptions, tags
(with source and confidence), transcript segments and audio events. Idempotent: running it again
(or on a database that still has the rows) updates them to match the sidecars. Jobs, stage
results, embeddings (re-created by the ``embed`` stage, see ``/api/v1/admin/reembed``), audit
log, users and filing rule history are not part of the sidecars and are not restored. Taxonomy
fields and values that a tag names but the database lacks are created as approved.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import CONTENT_DIR, SYSTEM_DIR, resolve_in_root, uuid7
from rushbase_core.layout import META_DIR
from rushbase_core.sidecar import META_DIR_NAME, SidecarV1
from rushbase_server.models import (
    Asset,
    AssetFile,
    AssetMetadata,
    AssetTag,
    AudioEvent,
    Batch,
    Shot,
    TaxField,
    TaxValue,
    TranscriptSegment,
)
from rushbase_server.services import normalize

logger = logging.getLogger(__name__)


@dataclass
class RebuildReport:
    """What a rebuild found and did."""

    sidecars: int = 0
    created: int = 0
    updated: int = 0
    invalid: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)


def discover(root: Path) -> list[Path]:
    """Paths of all candidate sidecar files (``_meta`` folders below Content, central copies)."""
    found: list[Path] = []
    for directory, subdirs, names in os.walk(root / CONTENT_DIR):
        if Path(directory).name == META_DIR_NAME:
            found += [Path(directory) / n for n in names if n.endswith(".json")]
            subdirs.clear()
    central = root / SYSTEM_DIR / META_DIR
    if central.is_dir():
        found += [central / n.name for n in central.iterdir() if n.name.endswith(".json")]
    return sorted(found)


def load_sidecars(root: Path, report: RebuildReport) -> dict[str, SidecarV1]:
    """Parse sidecars; for duplicates of one asset the most recently written one wins."""
    result: dict[str, SidecarV1] = {}
    for path in discover(root):
        try:
            sidecar = SidecarV1.model_validate_json(path.read_bytes())
        except (ValidationError, OSError, ValueError) as exc:
            report.invalid.append(f"{path.relative_to(root).as_posix()}: {str(exc)[:120]}")
            continue
        key = str(sidecar.asset_id)
        if key not in result or sidecar.written_at > result[key].written_at:
            result[key] = sidecar
    return result


async def _batch_for(session: AsyncSession, sidecar: SidecarV1) -> Batch | None:
    info = sidecar.batch
    if info is None:
        return None
    batch = (
        await session.execute(select(Batch).where(Batch.input_path == info.input_path))
    ).scalar_one_or_none()
    if batch is None:
        batch = Batch(
            id=uuid7(),
            input_path=info.input_path,
            label=info.label,
            hint_date=info.hint_date,
            hint_location=info.hint_location,
            hint_project=info.hint_project,
            status="done",
        )
        session.add(batch)
        await session.flush()
    return batch


def _apply_asset(asset: Asset, sidecar: SidecarV1, batch: Batch | None) -> None:
    if sidecar.metadata.normalized:
        probe = normalize.probe_from_documents(sidecar.metadata.normalized, sidecar.metadata.raw)
        for column, value in normalize.probe_columns(probe).items():
            setattr(asset, column, value)
    d = sidecar.descriptive
    asset.batch_id = batch.id if batch else None
    asset.media_type = sidecar.media_type
    asset.status = sidecar.status
    asset.content_rel_dir = sidecar.content_rel_dir
    asset.filed_at = sidecar.filed_at
    asset.created_at = sidecar.created_at
    asset.size_total = sum(f.size for f in sidecar.files)
    asset.decode_status = sidecar.decode_status
    asset.derived = sidecar.derived
    asset.perceptual_hash = sidecar.perceptual_hash
    asset.title = d.title
    asset.summary_short = d.summary_short
    asset.summary_long_de = d.summary_long_de
    asset.summary_long_en = d.summary_long_en
    asset.tech_summary_de = d.tech_summary_de
    asset.tech_summary_en = d.tech_summary_en
    asset.rating = d.rating
    asset.person_identifiable = d.person_identifiable
    asset.consent = d.consent
    asset.consent_ref = d.consent_ref
    asset.locked = d.locked
    asset.analysis_profile = d.analysis_profile


async def _apply_files(
    session: AsyncSession, asset: Asset, sidecar: SidecarV1, report: RebuildReport
) -> None:
    existing = {
        f.original_rel_path: f
        for f in (await session.execute(select(AssetFile).where(AssetFile.asset_id == asset.id)))
        .scalars()
        .all()
    }
    keep = {f.original_rel_path for f in sidecar.files}
    stale = [f.id for p, f in existing.items() if p not in keep]
    if stale:
        await session.execute(delete(AssetFile).where(AssetFile.id.in_(stale)))
    for item in sidecar.files:
        row = existing.get(item.original_rel_path)
        clash = (
            await session.execute(
                select(AssetFile.asset_id).where(
                    AssetFile.rel_path == item.rel_path,
                    AssetFile.id != (row.id if row else uuid7()),
                )
            )
        ).scalar_one_or_none()
        if clash is not None and clash != asset.id:
            report.conflicts.append(f"{item.rel_path} belongs to asset {clash}")
            continue
        if row is None:
            row = AssetFile(id=uuid7(), asset_id=asset.id, original_rel_path=item.original_rel_path)
            session.add(row)
        row.role = item.role
        row.rel_path = item.rel_path
        row.size = item.size
        row.mtime = item.mtime
        row.blake3 = item.blake3
        row.quick_fp = item.quick_fp
        row.card_structure_root = item.card_structure_root
        if item.rel_path == sidecar.primary_rel_path:
            asset.primary_file_id = row.id
    await session.flush()


async def _apply_shots(session: AsyncSession, asset: Asset, sidecar: SidecarV1) -> None:
    await session.execute(delete(Shot).where(Shot.asset_id == asset.id))
    for s in sidecar.shots:
        session.add(
            Shot(
                asset_id=asset.id,
                idx=s.idx,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                keyframes=s.keyframes,
                camera_motion=s.camera_motion,
                sharpness=s.sharpness,
                shake=s.shake,
                exposure=s.exposure,
                noise=s.noise,
            )
        )


async def _taxonomy_ids(
    session: AsyncSession, cache: dict[tuple[str, str], tuple[UUID, UUID]], key: str, value: str
) -> tuple[UUID, UUID]:
    """``(field id, value id)`` for a tag, creating a missing field or value as approved."""
    known = cache.get((key, value.lower()))
    if known is not None:
        return known
    field = (
        await session.execute(select(TaxField).where(TaxField.key == key))
    ).scalar_one_or_none()
    if field is None:
        field = TaxField(id=uuid7(), key=key, label_de=key, label_en=key, kind="multi")
        session.add(field)
        await session.flush()
    row = (
        await session.execute(
            select(TaxValue).where(
                TaxValue.field_id == field.id, func.lower(TaxValue.value) == value.lower()
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TaxValue(id=uuid7(), field_id=field.id, value=value, status="approved")
        session.add(row)
        await session.flush()
    cache[(key, value.lower())] = (field.id, row.id)
    return field.id, row.id


async def _apply_tags(
    session: AsyncSession, asset: Asset, sidecar: SidecarV1, shots: dict[int, Shot]
) -> None:
    await session.execute(delete(AssetTag).where(AssetTag.asset_id == asset.id))
    cache: dict[tuple[str, str], tuple[UUID, UUID]] = {}
    for tag in sidecar.tags:
        if tag.shot_idx is not None and tag.shot_idx not in shots:
            continue
        field_id, value_id = await _taxonomy_ids(session, cache, tag.field, tag.value)
        session.add(
            AssetTag(
                id=uuid7(),
                asset_id=asset.id,
                shot_id=shots[tag.shot_idx].id if tag.shot_idx is not None else None,
                field_id=field_id,
                value_id=value_id,
                source=tag.source,
                confidence=tag.confidence,
            )
        )
    await session.flush()


def _apply_shot_descriptions(shots: dict[int, Shot], descriptions: list[dict[str, Any]]) -> None:
    for item in descriptions:
        shot = shots.get(int(item.get("idx", -1)))
        if shot is None:
            continue
        shot.description_short = item.get("short_de")
        shot.description_short_de = item.get("short_de")
        shot.description_short_en = item.get("short_en")
        shot.description_long_de = item.get("long_de")
        shot.description_long_en = item.get("long_en")
        shot.shot_size = item.get("shot_size")
        shot.angle = item.get("camera_angle")
        shot.interior_exterior = item.get("interior_exterior")
        shot.time_of_day = item.get("time_of_day")
        shot.people_count = item.get("people_count")
        shot.person_identifiable = item.get("person_identifiable") or "unknown"
        shot.ai_details = item.get("ai_details") or {}
        shot.human_fields = list(item.get("human_fields") or [])


async def _apply_ai(session: AsyncSession, asset: Asset, sidecar: SidecarV1) -> None:
    """Restore tags, descriptions, transcript, audio events and the review state."""
    await session.flush()
    shots = {
        s.idx: s
        for s in (await session.execute(select(Shot).where(Shot.asset_id == asset.id)))
        .scalars()
        .all()
    }
    await _apply_tags(session, asset, sidecar, shots)
    await session.execute(delete(TranscriptSegment).where(TranscriptSegment.asset_id == asset.id))
    await session.execute(delete(AudioEvent).where(AudioEvent.asset_id == asset.id))
    ai = sidecar.ai
    if ai is None:
        return
    asset.summary_short_en = ai.summary_short_en
    for column in ("shot_size", "camera_angle", "interior_exterior", "time_of_day", "people_count"):
        setattr(asset, column, ai.attributes.get(column))
    asset.audio_info = ai.audio
    asset.review_state = ai.review_state
    asset.review_reasons = list(ai.review_reasons)
    asset.ai_details = ai.ai_details or ({"models": ai.models} if ai.models else {})
    asset.human_fields = list(ai.human_fields)
    _apply_shot_descriptions(shots, ai.shot_descriptions)
    session.add_all(
        TranscriptSegment(
            id=uuid7(),
            asset_id=asset.id,
            idx=int(item["idx"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            text=str(item["text"]),
            language=item.get("language"),
            speaker=item.get("speaker"),
            words=item.get("words") or [],
        )
        for item in ai.transcript
    )
    session.add_all(
        AudioEvent(
            id=uuid7(),
            asset_id=asset.id,
            tag=str(item["tag"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            confidence=float(item["confidence"]),
            raw_top=item.get("raw_top") or [],
        )
        for item in ai.audio_events
    )
    await session.flush()


async def rebuild_from_sidecars(
    session: AsyncSession, root: Path, *, dry_run: bool = False
) -> RebuildReport:
    """Recreate database rows from the sidecars below ``root``; the caller commits."""
    report = RebuildReport()
    sidecars = await asyncio.to_thread(load_sidecars, root, report)
    report.sidecars = len(sidecars)
    for sidecar in sidecars.values():
        asset = await session.get(Asset, sidecar.asset_id)
        creating = asset is None
        report.created += creating
        report.updated += not creating
        for item in sidecar.files:
            if not resolve_in_root(root, item.rel_path).exists():
                report.missing_files.append(item.rel_path)
        if dry_run:
            continue
        batch = await _batch_for(session, sidecar)
        if asset is None:
            asset = Asset(id=sidecar.asset_id, media_type=sidecar.media_type)
            session.add(asset)
        _apply_asset(asset, sidecar, batch)
        await session.flush()
        await _apply_files(session, asset, sidecar, report)
        metadata = await session.get(AssetMetadata, asset.id)
        if metadata is None:
            metadata = AssetMetadata(asset_id=asset.id)
            session.add(metadata)
        metadata.normalized = sidecar.metadata.normalized
        metadata.raw = sidecar.metadata.raw
        metadata.version = sidecar.metadata.version
        await _apply_shots(session, asset, sidecar)
        await _apply_ai(session, asset, sidecar)
    return report
