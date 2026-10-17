"""Rebuild the database from sidecars (``rushbase-server rebuild-from-sidecars``).

Walks ``Content/**/_meta/*.json`` and ``.rushbase/meta/*.json`` and recreates batch, asset,
asset_file, asset_metadata and shot rows. Idempotent: running it again (or on a database that
still has the rows) updates them to match the sidecars. Jobs, stage results, audit log, users
and filing rule history are not part of the sidecars and are not restored.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import CONTENT_DIR, SYSTEM_DIR, resolve_in_root, uuid7
from rushbase_core.layout import META_DIR
from rushbase_core.sidecar import META_DIR_NAME, SidecarV1
from rushbase_server.models import Asset, AssetFile, AssetMetadata, Batch, Shot
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
            found += [
                Path(directory) / n
                for n in names
                if n.endswith(".json") and not (Path(directory) / n).is_symlink()
            ]
            subdirs.clear()
    central = root / SYSTEM_DIR / META_DIR
    if central.is_dir():
        found += [
            central / n.name
            for n in central.iterdir()
            if n.name.endswith(".json") and not n.is_symlink()
        ]
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
    return report
