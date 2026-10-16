"""AI sections of sidecars and the "sidecar refresh" for assets that are already filed.

Filing writes the sidecar once; when AI results or human edits arrive later the JSON sidecar is
rewritten in place (temp file + rename). Nothing is ever moved and originals are untouched.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import resolve_in_root
from rushbase_core.fsops import atomic_write_bytes
from rushbase_core.sidecar import SidecarAI, SidecarTag
from rushbase_server.models import (
    Asset,
    AssetFile,
    AssetMetadata,
    AssetTag,
    AudioEvent,
    Batch,
    FilingRule,
    Shot,
    TaxField,
    TaxValue,
    TranscriptSegment,
)
from rushbase_server.services import sidecars
from rushbase_server.services.settings_store import CENTRAL_META_KEY, get_setting

if TYPE_CHECKING:
    from rushbase_server.runtime import Runtime

logger = logging.getLogger(__name__)


async def load_sections(
    session: AsyncSession, asset: Asset, shots: list[Shot] | None = None
) -> tuple[list[SidecarTag], SidecarAI | None]:
    """Tags and the AI section of ``asset`` (``None`` while nothing AI-related exists)."""
    rows = (
        await session.execute(
            select(AssetTag, TaxField.key, TaxValue.value, Shot.idx)
            .join(TaxField, TaxField.id == AssetTag.field_id)
            .join(TaxValue, TaxValue.id == AssetTag.value_id)
            .outerjoin(Shot, Shot.id == AssetTag.shot_id)
            .where(AssetTag.asset_id == asset.id)
            .order_by(TaxField.key, TaxValue.value, AssetTag.source)
        )
    ).all()
    tags = [
        SidecarTag(
            field=key, value=value, source=tag.source, confidence=tag.confidence, shot_idx=idx
        )
        for tag, key, value, idx in rows
    ]
    if shots is None:
        shots = list(
            (await session.execute(select(Shot).where(Shot.asset_id == asset.id))).scalars()
        )
    transcript = (
        await session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.asset_id == asset.id)
            .order_by(TranscriptSegment.idx)
        )
    ).scalars()
    segment_rows = list(transcript)
    events = (
        await session.execute(
            select(AudioEvent)
            .where(AudioEvent.asset_id == asset.id)
            .order_by(AudioEvent.start_ms, AudioEvent.tag)
        )
    ).scalars()
    event_rows = list(events)
    has_ai = bool(
        asset.ai_details
        or asset.audio_info
        or asset.summary_short_en
        or tags
        or segment_rows
        or event_rows
    )
    if not has_ai:
        return tags, None
    ai = SidecarAI(
        summary_short_de=asset.summary_short,
        summary_short_en=asset.summary_short_en,
        shot_descriptions=[
            {
                "idx": s.idx,
                "short_de": s.description_short_de,
                "short_en": s.description_short_en,
                "long_de": s.description_long_de,
                "long_en": s.description_long_en,
                "shot_size": s.shot_size,
                "camera_angle": s.angle,
                "interior_exterior": s.interior_exterior,
                "time_of_day": s.time_of_day,
                "people_count": s.people_count,
                "person_identifiable": s.person_identifiable,
                "ai_details": s.ai_details,
                "human_fields": list(s.human_fields),
            }
            for s in sorted(shots, key=lambda x: x.idx)
            if s.description_short_de or s.description_long_de or s.ai_details
        ],
        attributes={
            "shot_size": asset.shot_size,
            "camera_angle": asset.camera_angle,
            "interior_exterior": asset.interior_exterior,
            "time_of_day": asset.time_of_day,
            "people_count": asset.people_count,
        },
        audio=asset.audio_info,
        transcript_segments=len(segment_rows),
        transcript_language=next((r.language for r in segment_rows if r.language), None),
        transcript=[
            {
                "idx": r.idx,
                "start_ms": r.start_ms,
                "end_ms": r.end_ms,
                "text": r.text,
                "language": r.language,
                "speaker": r.speaker,
                "words": r.words,
            }
            for r in segment_rows
        ],
        audio_events=[
            {
                "tag": e.tag,
                "start_ms": e.start_ms,
                "end_ms": e.end_ms,
                "confidence": e.confidence,
                "raw_top": e.raw_top,
            }
            for e in event_rows
        ],
        review_state=asset.review_state,
        review_reasons=list(asset.review_reasons),
        models=asset.ai_details.get("models", {}),
        ai_details=asset.ai_details,
        human_fields=list(asset.human_fields),
    )
    return tags, ai


def _write(root: Path, rel: str, data: bytes) -> None:
    atomic_write_bytes(resolve_in_root(root, rel), data)


async def refresh_sidecar(
    session: AsyncSession,
    root: Path | None,
    library_id: UUID | None,
    asset_id: UUID,
    now: datetime,
    central_copy: bool = False,
) -> bool:
    """Rewrite the JSON sidecar of a filed asset; returns ``False`` if there is nothing to do.

    Failures are logged and swallowed: the database is the source of truth and the next refresh
    (or ``rebuild``) fixes the file.
    """
    asset = await session.get(Asset, asset_id)
    if root is None or asset is None or asset.status != "filed" or not asset.content_rel_dir:
        return False
    files = list(
        (await session.execute(select(AssetFile).where(AssetFile.asset_id == asset_id))).scalars()
    )
    if not files:
        return False
    shots = list((await session.execute(select(Shot).where(Shot.asset_id == asset_id))).scalars())
    batch = await session.get(Batch, asset.batch_id) if asset.batch_id else None
    rule = await session.get(FilingRule, asset.rule_version_id) if asset.rule_version_id else None
    tags, ai = await load_sections(session, asset, shots)
    document = sidecars.build_sidecar(
        asset,
        batch,
        files,
        await session.get(AssetMetadata, asset_id),
        shots,
        rule,
        library_id,
        now,
        tags,
        ai,
    )
    primary = next((f for f in files if f.id == asset.primary_file_id), files[0])
    task = sidecars.SidecarTask(
        asset_id=asset_id,
        sidecar=document,
        meta_directory=asset.content_rel_dir,
        filename=PurePosixPath(primary.rel_path).name,
    )
    try:
        rel = await asyncio.to_thread(sidecars.json_rel, root, task)
        data = document.to_json()
        await asyncio.to_thread(_write, root, rel, data)
        if central_copy:
            await asyncio.to_thread(_write, root, sidecars.central_rel(asset_id), data)
    except OSError:
        logger.warning("sidecar refresh failed", extra={"asset": str(asset_id)}, exc_info=True)
        return False
    return True


async def refresh_for_runtime(runtime: "Runtime", session: AsyncSession, asset_id: UUID) -> bool:
    """:func:`refresh_sidecar` with the library, clock and central-copy setting of ``runtime``."""
    central = bool(await get_setting(session, CENTRAL_META_KEY, False))
    return await refresh_sidecar(
        session,
        runtime.binding.root,
        runtime.binding.library_id,
        asset_id,
        runtime.clock.now(),
        central,
    )
