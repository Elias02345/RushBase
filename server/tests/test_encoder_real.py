"""Optional: runs the real ONNX text towers when ``RUSHBASE_TEST_MODELS_DIR`` points at them."""

import os
from pathlib import Path

import pytest

from rushbase_server.search.encoder import OnnxEncoder
from support import make_settings

MODELS_DIR = os.environ.get("RUSHBASE_TEST_MODELS_DIR")

pytestmark = pytest.mark.skipif(not MODELS_DIR, reason="RUSHBASE_TEST_MODELS_DIR not set")


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_real_towers_produce_normalised_768_vectors_with_sensible_similarity() -> None:
    assert MODELS_DIR is not None
    settings = make_settings("postgresql+psycopg://x:y@127.0.0.1:1/z", models_dir=Path(MODELS_DIR))
    encoder = OnnxEncoder(settings)
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
