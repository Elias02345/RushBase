"""Builders for AI stage outputs and seeded assets (importable as ``ai_support``)."""

import base64
import hashlib
import struct
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import uuid7
from rushbase_core.results_ai import (
    AudioOutput,
    ClassifyOutput,
    EmbedOutput,
    VisionOutput,
)
from rushbase_server.models import (
    Asset,
    AssetFile,
    Batch,
    Job,
    Shot,
    TaxField,
    TaxValue,
)

MODEL = {"model_id": "test-model", "revision": "r1"}


def fake_vector(seed: str, dim: int = 768) -> dict[str, Any]:
    """Deterministic L2-normalised vector as contract dict."""
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        values += [(b - 127.5) / 128 for b in digest]
        counter += 1
    values = values[:dim]
    norm = sum(v * v for v in values) ** 0.5
    packed = struct.pack(f"<{dim}f", *[v / norm for v in values])
    return {"dim": dim, "b64": base64.b64encode(packed).decode()}


def estimate(value: Any, confidence: float = 0.9) -> dict[str, Any]:
    return {"value": value, "source": "ai", "confidence": confidence}


def description(shot_idx: int | None, text: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "shot_idx": shot_idx,
        "short_de": f"{text} kurz",
        "short_en": f"{text} short",
        "long_de": f"{text} lang",
        "long_en": f"{text} long",
        "shot_size": estimate("wide"),
        "camera_angle": estimate("eye_level"),
        "people_count": estimate(2),
        "person_identifiable": estimate("no"),
    }
    return {**base, **overrides}


def vision_output(descriptions: list[dict[str, Any]], summary: str = "Hafen") -> VisionOutput:
    return VisionOutput.model_validate(
        {
            "model": MODEL,
            "descriptions": descriptions,
            "summary": {
                "short_de": f"{summary} kurz",
                "short_en": f"{summary} short",
                "long_de": f"{summary} lang",
                "long_en": f"{summary} long",
            },
        }
    )


def classify_output(tags: list[dict[str, Any]] | None = None, **overrides: Any) -> ClassifyOutput:
    return ClassifyOutput.model_validate(
        {"text_model": MODEL, "taxonomy_version": 1, "tags": tags or [], **overrides}
    )


def tag(value: str, shot_idx: int | None = None, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "field": "location",
        "value": value,
        "confidence": confidence,
        "shot_idx": shot_idx,
        "method": "exact",
        "origin": "vision",
    }


def embed_output(shot_indexes: list[int]) -> EmbedOutput:
    return EmbedOutput.model_validate(
        {
            "model": MODEL,
            "images": [
                {"shot_idx": i, "keyframe": "middle", "vector": fake_vector(f"img{i}")}
                for i in shot_indexes
            ],
        }
    )


def audio_output(texts: list[str]) -> AudioOutput:
    return AudioOutput.model_validate(
        {
            "has_audio": True,
            "transcript": {
                "language": "de",
                "model": MODEL,
                "segments": [
                    {"start_ms": i * 1000, "end_ms": i * 1000 + 900, "text": t}
                    for i, t in enumerate(texts)
                ],
            },
            "events": [{"tag": "applause", "start_ms": 0, "end_ms": 500, "confidence": 0.8}],
        }
    )


async def seed_shots_asset(
    session: AsyncSession, shots: int = 2, media_type: str = "video", status: str = "filed"
) -> Asset:
    """An asset with one file and ``shots`` shots (committed by the caller)."""
    batch = Batch(id=uuid7(), input_path=f"Input/{uuid7()}", label="b")
    session.add(batch)
    await session.flush()
    asset = Asset(id=uuid7(), batch_id=batch.id, media_type=media_type, status=status)
    session.add(asset)
    await session.flush()
    file = AssetFile(
        id=uuid7(),
        asset_id=asset.id,
        role="primary",
        rel_path=f"Input/{asset.id}.mov",
        original_rel_path=f"Input/{asset.id}.mov",
        size=1,
    )
    session.add(file)
    await session.flush()
    asset.primary_file_id = file.id
    for idx in range(shots):
        session.add(Shot(asset_id=asset.id, idx=idx, start_ms=idx * 1000, end_ms=idx * 1000 + 999))
    await session.flush()
    return asset


async def seed_taxonomy(session: AsyncSession) -> dict[str, UUID]:
    """Field ``location`` (single) with approved ``harbour`` and ``street``."""
    field = TaxField(key="location", label_de="Ort", label_en="Location", kind="single")
    session.add(field)
    await session.flush()
    ids = {"field": field.id}
    for value, de, en in (("harbour", "Hafen", "Harbour"), ("street", "Strasse", "Street")):
        row = TaxValue(field_id=field.id, value=value, label_de=de, label_en=en, status="approved")
        session.add(row)
        await session.flush()
        ids[value] = row.id
    return ids


def fake_job(asset_id: UUID, stage: str) -> Job:
    return Job(id=uuid7(), asset_id=asset_id, stage=stage, state="leased", depends_on=[])
