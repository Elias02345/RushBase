"""Store the outputs of the AI stages (``audio``, ``embed``, ``vision``, ``classify``).

Each stage replaces its previous AI rows of the asset (idempotent per asset and stage). Edits by
people and by Claude win: ``human``/``claude`` tags are never touched, columns listed in
``asset.human_fields`` / ``shot.human_fields`` are never overwritten, and a locked asset is not
modified at all (its raw result stays in ``stage_result``).
"""

import struct
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core.results import Estimated
from rushbase_core.results_ai import (
    AudioOutput,
    ClassifyOutput,
    EmbedOutput,
    ModelRef,
    Vector,
    VisionOutput,
    VisualDescription,
)
from rushbase_server.models import (
    Asset,
    AssetTag,
    AudioEvent,
    EmbeddingImage,
    EmbeddingText,
    Job,
    Shot,
    TaxField,
    TaxValue,
    TranscriptSegment,
)
from rushbase_server.services import settings_store

THRESHOLDS_KEY = "ai.thresholds"
DEFAULT_THRESHOLDS: dict[str, float] = {
    "default": 0.5,
    "shot_size": 0.5,
    "camera_angle": 0.5,
    "interior_exterior": 0.5,
    "time_of_day": 0.5,
    "people_count": 0.5,
    "person_identifiable": 0.6,
}
MAX_EXAMPLES = 5
UNSORTED_FIELD = "unsorted"


class AiResultError(Exception):
    """An AI output is inconsistent with the stored asset (re-raised as an invalid result)."""


def decode_vector(vector: Vector) -> list[float]:
    """Floats of a contract vector (base64 little-endian float32)."""
    import base64

    return list(struct.unpack(f"<{vector.dim}f", base64.b64decode(vector.b64)))


async def thresholds(session: AsyncSession) -> dict[str, float]:
    """Per-field confidence thresholds (setting ``ai.thresholds`` over the defaults)."""
    stored = await settings_store.get_setting(session, THRESHOLDS_KEY, {})
    return {**DEFAULT_THRESHOLDS, **{k: float(v) for k, v in stored.items()}}


async def _shot_ids(session: AsyncSession, asset_id: UUID) -> dict[int, UUID]:
    rows = await session.execute(select(Shot.idx, Shot.id).where(Shot.asset_id == asset_id))
    return {idx: shot_id for idx, shot_id in rows}  # noqa: C416


def _set_review(asset: Asset, stage: str, reasons: list[str]) -> None:
    """Record the reasons of ``stage`` and derive the asset's review state from all stages."""
    details = dict(asset.ai_details)
    per_stage = dict(details.get("review", {}))
    per_stage[stage] = reasons
    details["review"] = per_stage
    asset.ai_details = details
    union = sorted({r for items in per_stage.values() for r in items})
    asset.review_reasons = union
    if union and asset.review_state != "done":
        asset.review_state = "pending"
    elif not union and asset.review_state == "pending":
        asset.review_state = "none"


def _models(asset: Asset, stage: str, ref: ModelRef | None) -> None:
    if ref is not None:
        details = dict(asset.ai_details)
        details["models"] = {**details.get("models", {}), stage: ref.model_dump(mode="json")}
        asset.ai_details = details


async def apply_output(session: AsyncSession, job: Job, parsed: BaseModel | None) -> bool:
    """Store a validated AI output; returns ``True`` if the output was an AI contract."""
    if not isinstance(parsed, AudioOutput | EmbedOutput | VisionOutput | ClassifyOutput):
        return False
    asset = await session.get_one(Asset, job.asset_id)
    if asset.locked:
        return True
    if isinstance(parsed, AudioOutput):
        await _apply_audio(session, asset, parsed)
    elif isinstance(parsed, EmbedOutput):
        await _apply_embed(session, asset, parsed)
    elif isinstance(parsed, VisionOutput):
        await _apply_vision(session, asset, parsed)
    else:
        await _apply_classify(session, asset, parsed)
    return True


# --- audio -----------------------------------------------------------------------------------


async def _apply_audio(session: AsyncSession, asset: Asset, out: AudioOutput) -> None:
    await session.execute(delete(TranscriptSegment).where(TranscriptSegment.asset_id == asset.id))
    await session.execute(delete(AudioEvent).where(AudioEvent.asset_id == asset.id))
    segments = out.transcript.segments if out.transcript else []
    default_language = out.transcript.language if out.transcript else None
    session.add_all(
        TranscriptSegment(
            asset_id=asset.id,
            idx=i,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            text=s.text,
            language=s.language or default_language,
            speaker=s.speaker,
            words=[w.model_dump(mode="json") for w in s.words],
        )
        for i, s in enumerate(segments)
    )
    session.add_all(
        AudioEvent(
            asset_id=asset.id,
            tag=e.tag,
            start_ms=e.start_ms,
            end_ms=e.end_ms,
            confidence=e.confidence,
            raw_top=[[label, score] for label, score in e.raw_top],
        )
        for e in out.events
    )
    asset.audio_info = {
        "has_audio": out.has_audio,
        "language": default_language,
        "loudness": out.loudness.model_dump(mode="json") if out.loudness else None,
        "audio_type": out.audio_type.value.value if out.audio_type else None,
        "bpm": out.bpm,
        "musical_key": out.musical_key,
    }
    _models(asset, "transcript", out.transcript.model if out.transcript else None)
    _models(asset, "audio_events", out.events_model)


# --- embed -----------------------------------------------------------------------------------


async def _apply_embed(session: AsyncSession, asset: Asset, out: EmbedOutput) -> None:
    shots = await _shot_ids(session, asset.id)
    for item in out.images:
        if item.shot_idx is not None and item.shot_idx not in shots:
            raise AiResultError(f"embed output names unknown shot {item.shot_idx}")
        if item.vector.dim != 768:
            raise AiResultError(f"image vectors must have 768 dimensions, got {item.vector.dim}")
    await session.execute(delete(EmbeddingImage).where(EmbeddingImage.asset_id == asset.id))
    session.add_all(
        EmbeddingImage(
            asset_id=asset.id,
            shot_id=shots.get(item.shot_idx) if item.shot_idx is not None else None,
            keyframe=item.keyframe,
            model_id=out.model.model_id,
            revision=out.model.revision,
            embedding=decode_vector(item.vector),
        )
        for item in out.images
    )
    _models(asset, "image_embedding", out.model)


# --- vision ----------------------------------------------------------------------------------


def _value(estimate: Estimated[Any] | None) -> Any:
    if estimate is None:
        return None
    value = estimate.value
    return getattr(value, "value", value)


def _confidence(estimate: Estimated[Any] | None) -> float | None:
    return None if estimate is None else estimate.confidence


def _low_confidence(
    description: VisualDescription, limits: dict[str, float], prefix: str
) -> list[str]:
    reasons = []
    for field in (
        "shot_size",
        "camera_angle",
        "interior_exterior",
        "time_of_day",
        "people_count",
        "person_identifiable",
    ):
        estimate = getattr(description, field)
        confidence = _confidence(estimate)
        if (
            estimate is not None
            and confidence is not None
            and confidence < limits.get(field, limits["default"])
        ):
            reasons.append(f"{prefix}low_confidence:{field}")
    for flag in description.flags:
        if flag.value:
            reasons.append(f"{prefix}flag:{flag.flag}")
    return reasons


def _attributes(description: VisualDescription) -> dict[str, Any]:
    return {
        "shot_size": _value(description.shot_size),
        "interior_exterior": _value(description.interior_exterior),
        "time_of_day": _value(description.time_of_day),
        "people_count": _value(description.people_count),
    }


def _apply_to_shot(shot: Shot, description: VisualDescription) -> None:
    free = set(shot.human_fields)

    def put(column: str, value: Any) -> None:
        if column not in free and value is not None:
            setattr(shot, column, value)

    put("description_short", description.short_de)
    put("description_short_de", description.short_de)
    put("description_short_en", description.short_en)
    put("description_long_de", description.long_de)
    put("description_long_en", description.long_en)
    put("shot_size", _value(description.shot_size))
    put("angle", _value(description.camera_angle))
    put("interior_exterior", _value(description.interior_exterior))
    put("time_of_day", _value(description.time_of_day))
    put("people_count", _value(description.people_count))
    put("person_identifiable", _value(description.person_identifiable))
    shot.ai_details = description.model_dump(
        mode="json", exclude={"short_de", "short_en", "long_de", "long_en"}
    )


def _apply_to_asset(asset: Asset, description: VisualDescription) -> None:
    free = set(asset.human_fields)

    def put(column: str, value: Any) -> None:
        if column not in free and value is not None:
            setattr(asset, column, value)

    put("shot_size", _value(description.shot_size))
    put("camera_angle", _value(description.camera_angle))
    put("interior_exterior", _value(description.interior_exterior))
    put("time_of_day", _value(description.time_of_day))
    put("people_count", _value(description.people_count))
    put("person_identifiable", _value(description.person_identifiable))
    asset.ai_details = {
        **asset.ai_details,
        "photo": description.model_dump(
            mode="json", exclude={"short_de", "short_en", "long_de", "long_en"}
        ),
    }


def _asset_person_identifiable(shots: list[Shot]) -> str:
    values = {s.person_identifiable for s in shots}
    if "yes" in values:
        return "yes"
    return "no" if values == {"no"} else "unknown"


async def _apply_vision(session: AsyncSession, asset: Asset, out: VisionOutput) -> None:
    limits = await thresholds(session)
    shots = {
        s.idx: s
        for s in (await session.execute(select(Shot).where(Shot.asset_id == asset.id)))
        .scalars()
        .all()
    }
    reasons: list[str] = []
    for description in out.descriptions:
        if description.shot_idx is None:
            _apply_to_asset(asset, description)
            reasons += _low_confidence(description, limits, "")
            if asset.media_type == "photo":
                _photo_summary(asset, description)
            continue
        shot = shots.get(description.shot_idx)
        if shot is None:
            raise AiResultError(f"vision output names unknown shot {description.shot_idx}")
        _apply_to_shot(shot, description)
        reasons += _low_confidence(description, limits, f"shot{description.shot_idx}:")
    if shots and "person_identifiable" not in asset.human_fields:
        asset.person_identifiable = _asset_person_identifiable(list(shots.values()))
    if out.summary is not None:
        free = set(asset.human_fields)
        for column, value in (
            ("summary_short", out.summary.short_de),
            ("summary_short_en", out.summary.short_en),
            ("summary_long_de", out.summary.long_de),
            ("summary_long_en", out.summary.long_en),
        ):
            if column not in free:
                setattr(asset, column, value)
    _models(asset, "vision", out.model)
    _set_review(asset, "vision", reasons)


def _photo_summary(asset: Asset, description: VisualDescription) -> None:
    """A photo without an explicit summary is described by its single analysis."""
    free = set(asset.human_fields)
    for column, value in (
        ("summary_short", description.short_de),
        ("summary_short_en", description.short_en),
        ("summary_long_de", description.long_de),
        ("summary_long_en", description.long_en),
    ):
        if column not in free:
            setattr(asset, column, value)


# --- classify --------------------------------------------------------------------------------


async def _field(session: AsyncSession, key: str, create: bool) -> TaxField | None:
    field = (
        await session.execute(select(TaxField).where(TaxField.key == key))
    ).scalar_one_or_none()
    if field is None and create:
        field = TaxField(key=key, label_de="Unsortiert", label_en="Unsorted", kind="multi")
        session.add(field)
        await session.flush()
    return field


async def _value_row(session: AsyncSession, field_id: UUID, value: str) -> TaxValue | None:
    from sqlalchemy import func

    return (
        await session.execute(
            select(TaxValue).where(
                TaxValue.field_id == field_id, func.lower(TaxValue.value) == value.lower()
            )
        )
    ).scalar_one_or_none()


async def _record_proposal(
    session: AsyncSession, asset: Asset, field_key: str | None, term: str
) -> None:
    field = await _field(session, field_key or UNSORTED_FIELD, create=field_key is None)
    if field is None:
        return
    row = await _value_row(session, field.id, term)
    if row is None:
        row = TaxValue(field_id=field.id, value=term, status="proposed", proposal_count=0)
        row.example_asset_ids = []
        session.add(row)
    if row.status == "deprecated" or asset.id in row.example_asset_ids:
        return
    row.proposal_count += 1
    if len(row.example_asset_ids) < MAX_EXAMPLES:
        row.example_asset_ids = [*row.example_asset_ids, asset.id]


async def _apply_classify(session: AsyncSession, asset: Asset, out: ClassifyOutput) -> None:
    shots = await _shot_ids(session, asset.id)
    protected = (
        await session.execute(
            select(AssetTag.shot_id, AssetTag.field_id, AssetTag.value_id).where(
                AssetTag.asset_id == asset.id, AssetTag.source.in_(("human", "claude"))
            )
        )
    ).all()
    await session.execute(
        delete(AssetTag).where(AssetTag.asset_id == asset.id, AssetTag.source == "ai")
    )
    rejected = set(asset.ai_details.get("rejected_tags", []))
    field_cache: dict[str, TaxField | None] = {}
    seen: set[tuple[UUID | None, UUID]] = set()
    for tag in out.tags:
        if tag.shot_idx is not None and tag.shot_idx not in shots:
            raise AiResultError(f"classify output names unknown shot {tag.shot_idx}")
        if tag.field not in field_cache:
            field_cache[tag.field] = await _field(session, tag.field, create=False)
        field = field_cache[tag.field]
        row = await _value_row(session, field.id, tag.value) if field else None
        if field is None or row is None or row.status != "approved":
            await _record_proposal(session, asset, tag.field if field else None, tag.value)
            continue
        shot_id = shots.get(tag.shot_idx) if tag.shot_idx is not None else None
        if (
            f"{tag.field}:{row.value.lower()}:{'' if tag.shot_idx is None else tag.shot_idx}"
            in rejected
        ):
            continue
        key = (shot_id, row.id)
        if key in seen or any(
            p.shot_id == shot_id
            and p.field_id == field.id
            and (field.kind == "single" or p.value_id == row.id)
            for p in protected
        ):
            continue
        seen.add(key)
        session.add(
            AssetTag(
                asset_id=asset.id,
                shot_id=shot_id,
                field_id=field.id,
                value_id=row.id,
                source="ai",
                confidence=tag.confidence,
                model_ref={
                    **out.text_model.model_dump(mode="json"),
                    "method": tag.method,
                    "origin": tag.origin,
                    "taxonomy_version": out.taxonomy_version,
                },
            )
        )
    for proposal in out.proposals:
        await _record_proposal(session, asset, proposal.field, proposal.term)
    await _replace_text_embeddings(session, asset, out, shots)
    details = dict(asset.ai_details)
    details["taxonomy_version"] = out.taxonomy_version
    asset.ai_details = details
    _models(asset, "classify", out.text_model)
    _set_review(asset, "classify", sorted(set(out.review_reasons)))


async def _replace_text_embeddings(
    session: AsyncSession, asset: Asset, out: ClassifyOutput, shots: dict[int, UUID]
) -> None:
    for item in out.text_embeddings:
        if item.vector.dim != 768:
            raise AiResultError(f"text vectors must have 768 dimensions, got {item.vector.dim}")
        if item.shot_idx is not None and item.shot_idx not in shots:
            raise AiResultError(f"classify output names unknown shot {item.shot_idx}")
    await session.execute(delete(EmbeddingText).where(EmbeddingText.asset_id == asset.id))
    session.add_all(
        EmbeddingText(
            asset_id=asset.id,
            shot_id=shots.get(item.shot_idx) if item.shot_idx is not None else None,
            kind=item.kind,
            model_id=out.text_model.model_id,
            revision=out.text_model.revision,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            language=item.language,
            embedding=decode_vector(item.vector),
        )
        for item in out.text_embeddings
    )
