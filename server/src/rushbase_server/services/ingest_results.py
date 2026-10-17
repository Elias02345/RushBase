"""Validate stage outputs and normalise them into the database.

``probe`` fills the technical asset columns and ``asset_metadata``, ``hash`` fills
``asset_file.blake3``, ``preview`` records the derived files and ``shots`` replaces the shot rows.
An output that violates the contract (:mod:`rushbase_core.results`) is rejected with
:class:`InvalidResultError`; the queue turns that into a permanent job error with a clear message.
"""

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import Stage
from rushbase_core.results import (
    HashOutput,
    PreviewOutput,
    ProbeOutput,
    ShotsOutput,
    parse_stage_output,
)
from rushbase_server.models import Asset, AssetFile, AssetMetadata, Job, Shot
from rushbase_server.services import ai_results, normalize, sync
from rushbase_server.services.derived import DerivedPathError, check_derived_rel_path


class InvalidResultError(Exception):
    """A worker reported output that does not satisfy the stage contract."""


def parse_output(stage: Stage, output: dict[str, Any]) -> BaseModel | None:
    """Validate ``output`` against the contract of ``stage`` (``None``: no contract yet).

    Raises:
        InvalidResultError: schema violation, with a short human readable message.
    """
    try:
        return parse_stage_output(stage, output)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'output'}: {e['msg']}"
            for e in exc.errors()[:5]
        )
        raise InvalidResultError(f"invalid {stage.value} output ({problems})") from exc


async def apply_output(session: AsyncSession, job: Job, parsed: BaseModel | None) -> None:
    """Store a validated output; raises :class:`InvalidResultError` for inconsistent content."""
    if isinstance(parsed, ProbeOutput):
        await _apply_probe(session, job, parsed)
    elif isinstance(parsed, HashOutput):
        await _apply_hash(session, job, parsed)
    elif isinstance(parsed, PreviewOutput):
        await _apply_preview(session, job, parsed)
    elif isinstance(parsed, ShotsOutput):
        await _apply_shots(session, job, parsed)
    else:
        try:
            await ai_results.apply_output(session, job, parsed)
        except ai_results.AiResultError as exc:
            raise InvalidResultError(str(exc)) from exc


async def _apply_probe(session: AsyncSession, job: Job, probe: ProbeOutput) -> None:
    asset = await session.get_one(Asset, job.asset_id)
    for column, value in normalize.probe_columns(probe).items():
        setattr(asset, column, value)
    normalized, raw = normalize.probe_documents(probe)
    metadata = await session.get(AssetMetadata, asset.id)
    if metadata is None:
        session.add(AssetMetadata(asset_id=asset.id, normalized=normalized, raw=raw, version=1))
    else:
        metadata.normalized = normalized
        metadata.raw = raw
        metadata.version += 1
    await session.flush()
    await sync.suggest_for_asset(session, asset)


async def _apply_hash(session: AsyncSession, job: Job, output: HashOutput) -> None:
    files = {
        f.rel_path: f
        for f in (
            await session.execute(select(AssetFile).where(AssetFile.asset_id == job.asset_id))
        )
        .scalars()
        .all()
    }
    for item in output.files:
        known = files.get(item.rel_path)
        if known is None:
            raise InvalidResultError(
                f"hash output names a file that is not part of the asset: {item.rel_path}"
            )
        if item.size != known.size:
            raise InvalidResultError(
                f"{item.rel_path} has {item.size} bytes but {known.size} were recorded "
                "(changed after ingest?)"
            )
    for item in output.files:
        files[item.rel_path].blake3 = item.blake3


def _derived_entries(job: Job, output: PreviewOutput) -> list[dict[str, Any]]:
    entries = []
    for item in output.files:
        try:
            check_derived_rel_path(job.asset_id, item.rel_path)
        except DerivedPathError as exc:
            raise InvalidResultError(str(exc)) from exc
        entries.append(item.model_dump(mode="json"))
    return entries


async def _apply_preview(session: AsyncSession, job: Job, output: PreviewOutput) -> None:
    asset = await session.get_one(Asset, job.asset_id)
    asset.derived = {
        **asset.derived,
        "files": _derived_entries(job, output),
        "normalization": output.normalization,
    }
    asset.perceptual_hash = output.perceptual_hash


async def _apply_shots(session: AsyncSession, job: Job, output: ShotsOutput) -> None:
    indexes = [s.idx for s in output.shots]
    if len(set(indexes)) != len(indexes):
        raise InvalidResultError("shots output contains duplicate shot indexes")
    for shot in output.shots:
        for keyframe in shot.keyframes:
            try:
                check_derived_rel_path(job.asset_id, keyframe.rel_path)
            except DerivedPathError as exc:
                raise InvalidResultError(str(exc)) from exc
        if shot.end_ms < shot.start_ms:
            raise InvalidResultError(f"shot {shot.idx} ends before it starts")
    existing = {
        s.idx: s
        for s in (await session.execute(select(Shot).where(Shot.asset_id == job.asset_id)))
        .scalars()
        .all()
    }
    stale = set(existing) - set(indexes)
    if stale:
        await session.execute(
            delete(Shot).where(Shot.asset_id == job.asset_id, Shot.idx.in_(stale))
        )
    for item in output.shots:
        row = existing.get(item.idx) or Shot(asset_id=job.asset_id, idx=item.idx)
        row.start_ms = item.start_ms
        row.end_ms = item.end_ms
        row.keyframes = [k.model_dump(mode="json") for k in item.keyframes]
        row.camera_motion = item.camera_motion.value.value if item.camera_motion else None
        row.sharpness = item.sharpness
        row.shake = item.shake
        row.exposure = item.exposure
        row.noise = item.noise
        if item.idx not in existing:
            session.add(row)
