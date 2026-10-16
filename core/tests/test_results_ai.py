import base64
import struct

import pytest
from pydantic import ValidationError

from rushbase_core.pipeline import Stage
from rushbase_core.results import parse_stage_output
from rushbase_core.results_ai import ClassifyOutput, Vector, VisionOutput

MODEL = {"model_id": "qwen3.5-4b-q8_0", "revision": "abc123", "profile": "gpu8"}


def vec(values: list[float]) -> dict[str, object]:
    raw = struct.pack(f"<{len(values)}f", *values)
    return {"dim": len(values), "b64": base64.b64encode(raw).decode()}


def test_vector_length_must_match_dim() -> None:
    Vector.model_validate(vec([0.6, 0.8]))
    with pytest.raises(ValidationError):
        Vector.model_validate({"dim": 3, "b64": vec([0.6, 0.8])["b64"]})


def test_vision_output_validates_through_the_stage_registry() -> None:
    parsed = parse_stage_output(
        Stage.VISION,
        {
            "schema": 1,
            "model": MODEL,
            "descriptions": [
                {
                    "shot_idx": 0,
                    "short_de": "Totale eines Sees",
                    "short_en": "Wide shot of a lake",
                    "long_de": "…",
                    "long_en": "…",
                    "shot_size": {"value": "wide", "source": "ai", "confidence": 0.8},
                    "people_count": {"value": 0, "source": "ai", "confidence": 0.9},
                    "objects": [{"label": "boat", "confidence": 0.7}],
                }
            ],
        },
    )
    assert isinstance(parsed, VisionOutput)
    assert parsed.descriptions[0].shot_size is not None
    with pytest.raises(ValidationError):
        parse_stage_output(Stage.VISION, {"model": MODEL, "descriptions": [], "extra": 1})


def test_classify_output_and_stage_without_contract() -> None:
    parsed = parse_stage_output(
        Stage.CLASSIFY,
        {
            "text_model": MODEL,
            "taxonomy_version": 3,
            "tags": [
                {
                    "field": "subject",
                    "value": "Landscape",
                    "confidence": 0.7,
                    "method": "synonym",
                    "origin": "vision",
                }
            ],
            "text_embeddings": [{"kind": "asset_summary", "language": "de", "vector": vec([1.0])}],
        },
    )
    assert isinstance(parsed, ClassifyOutput)
    assert parse_stage_output(Stage.FINALIZE, {}) is None
