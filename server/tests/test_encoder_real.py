"""Encoder layout, and the real ONNX towers when RUSHBASE_TEST_MODELS_DIR is set."""

import os
from pathlib import Path

import pytest

from rushbase_server.search.encoder import OnnxEncoder, load_layout
from support import make_settings

MODELS_DIR = os.environ.get("RUSHBASE_TEST_MODELS_DIR")
needs_models = pytest.mark.skipif(not MODELS_DIR, reason="RUSHBASE_TEST_MODELS_DIR not set")
URL = "postgresql+psycopg://x:y@127.0.0.1:1/z"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_layout_follows_the_worker_lock_file() -> None:
    layout = load_layout(make_settings(URL))
    assert layout["image"] == (
        "siglip2-base-patch16-256",
        None,
        ("text_model_fp16.onnx", "tokenizer.json"),
    )
    e5_id, e5_revision, e5_files = layout["text"]
    assert e5_id == "multilingual-e5-base"
    assert e5_revision is not None
    assert len(e5_revision) == 12
    assert e5_files == ("model_quantized.onnx", "tokenizer.json")


@needs_models
def test_real_towers_produce_normalised_768_vectors_with_sensible_similarity() -> None:
    assert MODELS_DIR is not None
    encoder = OnnxEncoder(make_settings(URL, models_dir=Path(MODELS_DIR)))
    assert encoder.info("image").available
    assert encoder.info("text").available
    dog = encoder.encode_image_query("a photo of a dog")
    puppy = encoder.encode_image_query("a photo of a puppy")
    car = encoder.encode_image_query("a red sports car")
    assert len(dog) == 768
    assert cosine(dog, dog) == pytest.approx(1.0, abs=1e-4)
    assert cosine(dog, puppy) > cosine(dog, car)
    hund = encoder.encode_text_query("ein Hund im Park")
    dog_en = encoder.encode_text_query("a dog in the park")
    auto = encoder.encode_text_query("ein rotes Auto")
    assert cosine(hund, dog_en) > cosine(hund, auto)
