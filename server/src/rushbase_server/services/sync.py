"""Audio/video sync suggestions (BWF timecode or capture time).

A recorder's BWF/iXML timecode (``audio.timecode``) or the capture time is compared with the
videos (or audios) of the same batch. When the time ranges overlap, a *suggestion* is stored with
the offset of the audio relative to the video start and a confidence. Nothing is linked until a
person confirms; only confirmed pairs reach the sidecars.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core.sidecar import SidecarSync
from rushbase_server.models import Asset, AssetMetadata, SyncSuggestion
from rushbase_server.services import audit

_TC = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})([:;.])(\d{2,3})$")
DEFAULT_FPS = 25.0
INACTIVE = ("trashed", "quarantined", "duplicate")


@dataclass(frozen=True)
class TimeRange:
    """When a recording ran, in milliseconds on a shared clock."""

    start_ms: float
    end_ms: float
    basis: str  # ``timecode`` or ``capture_at``


def parse_timecode_ms(value: str | None, fps: float | None) -> float | None:
    """Milliseconds since midnight for ``HH:MM:SS.mmm`` or ``HH:MM:SS:FF`` (``None`` if invalid)."""
    match = _TC.match((value or "").strip())
    if match is None:
        return None
    hours, minutes, seconds = (int(match.group(i)) for i in (1, 2, 3))
    last = match.group(5)
    fraction: float
    if match.group(4) == "." and len(last) == 3:
        fraction = int(last)
    else:
        fraction = int(last) / (fps or DEFAULT_FPS) * 1000
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + fraction


def _timecode_range(asset: Asset, normalized: dict[str, object]) -> TimeRange | None:
    section = normalized.get("audio" if asset.media_type == "audio" else "video")
    if not isinstance(section, dict) or not asset.duration_ms:
        return None
    video = normalized.get("video")
    fps = video.get("fps") if isinstance(video, dict) else None
    start = parse_timecode_ms(section.get("timecode"), fps if isinstance(fps, float) else None)
    if start is None:
        return None
    return TimeRange(start, start + asset.duration_ms, "timecode")


def _capture_range(asset: Asset) -> TimeRange | None:
    if asset.capture_at is None or not asset.duration_ms:
        return None
    start = asset.capture_at.timestamp() * 1000
    return TimeRange(start, start + asset.duration_ms, "capture_at")


async def _range_of(session: AsyncSession, asset: Asset, basis: str) -> TimeRange | None:
    if basis == "capture_at":
        return _capture_range(asset)
    metadata = await session.get(AssetMetadata, asset.id)
    return _timecode_range(asset, metadata.normalized if metadata else {})


def score(audio: TimeRange, video: TimeRange) -> tuple[int, float] | None:
    """``(offset_ms, confidence)`` of the audio against the video, or ``None`` without overlap."""
    overlap = min(audio.end_ms, video.end_ms) - max(audio.start_ms, video.start_ms)
    if overlap <= 0 or audio.basis != video.basis:
        return None
    ratio = overlap / min(audio.end_ms - audio.start_ms, video.end_ms - video.start_ms)
    base, span = (0.6, 0.35) if audio.basis == "timecode" else (0.3, 0.3)
    return round(audio.start_ms - video.start_ms), round(base + span * ratio, 3)


async def suggest_for_asset(session: AsyncSession, asset: Asset) -> int:
    """Create suggestions between ``asset`` and the audios/videos of its batch; returns how many.

    Existing pairs (whatever their status) are left alone. The asset's probe result must have
    been flushed.
    """
    if asset.batch_id is None or asset.media_type not in ("audio", "video"):
        return 0
    wanted = "video" if asset.media_type == "audio" else "audio"
    others = (
        (
            await session.execute(
                select(Asset).where(
                    Asset.batch_id == asset.batch_id,
                    Asset.media_type == wanted,
                    Asset.id != asset.id,
                    Asset.status.notin_(INACTIVE),
                )
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for other in others:
        audio, video = (asset, other) if asset.media_type == "audio" else (other, asset)
        exists = await session.scalar(
            select(SyncSuggestion.id).where(
                SyncSuggestion.asset_a == audio.id, SyncSuggestion.asset_b == video.id
            )
        )
        if exists is not None:
            continue
        for basis in ("timecode", "capture_at"):
            a_range = await _range_of(session, audio, basis)
            v_range = await _range_of(session, video, basis)
            result = score(a_range, v_range) if a_range and v_range else None
            if result is not None:
                session.add(
                    SyncSuggestion(
                        asset_a=audio.id,
                        asset_b=video.id,
                        offset_ms=result[0],
                        confidence=result[1],
                        basis=basis,
                    )
                )
                created += 1
                break
    await session.flush()
    return created


async def suggestions_of(
    session: AsyncSession, asset_id: UUID
) -> list[tuple[SyncSuggestion, Asset]]:
    """Suggestions that involve ``asset_id`` with the *other* asset, best first."""
    rows = (
        (
            await session.execute(
                select(SyncSuggestion)
                .where(or_(SyncSuggestion.asset_a == asset_id, SyncSuggestion.asset_b == asset_id))
                .order_by(SyncSuggestion.confidence.desc())
            )
        )
        .scalars()
        .all()
    )
    result = []
    for row in rows:
        other = await session.get_one(
            Asset, row.asset_b if row.asset_a == asset_id else row.asset_a
        )
        result.append((row, other))
    return result


async def decide(
    session: AsyncSession,
    suggestion_id: UUID,
    status: str,
    user_id: UUID,
    now: datetime,
) -> SyncSuggestion | None:
    """Confirm or reject a suggestion (``None`` when unknown); the caller commits."""
    row = await session.get(SyncSuggestion, suggestion_id)
    if row is None:
        return None
    row.status = status
    row.decided_by = user_id
    row.decided_at = now
    audit.record(
        session,
        actor_kind="user",
        actor_id=user_id,
        action=f"sync.{status}",
        target_kind="asset",
        target_id=row.asset_b,
        details={"audio": str(row.asset_a), "video": str(row.asset_b), "offset_ms": row.offset_ms},
    )
    return row


async def confirmed_sections(session: AsyncSession, asset_id: UUID) -> list[SidecarSync]:
    """Sidecar entries of the confirmed pairs of ``asset_id``."""
    rows = (
        (
            await session.execute(
                select(SyncSuggestion).where(
                    or_(SyncSuggestion.asset_a == asset_id, SyncSuggestion.asset_b == asset_id),
                    SyncSuggestion.status == "confirmed",
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        SidecarSync(
            other_asset_id=r.asset_b if r.asset_a == asset_id else r.asset_a,
            other_role="video" if r.asset_a == asset_id else "audio",
            offset_ms=r.offset_ms,
            confidence=r.confidence,
            basis=r.basis,
        )
        for r in rows
    ]
