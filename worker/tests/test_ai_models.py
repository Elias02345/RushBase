"""Tests against the real models; skipped unless they are in the worker data dir.

Run `rushbase models pull` first. The Whisper test additionally needs a speech recording:
set RUSHBASE_TEST_SPEECH_WAV (e.g. whisper.cpp's public-domain samples/jfk.wav).
"""

import itertools
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rushbase_worker.assets.plan import IMAGE_EMBEDDING_MODEL, TEXT_EMBEDDING_MODEL
from rushbase_worker.audio.events import EventClassifier, load_labels
from rushbase_worker.audio.extract import SAMPLE_RATE
from rushbase_worker.config import WorkerSettings
from rushbase_worker.embedding.siglip import ImageEmbedder
from rushbase_worker.runtimes import onnx
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.runtimes.whisper import transcribe

FIXTURES = Path(__file__).parent / "fixtures" / "ai"
pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def runtimes() -> Runtimes:
    return Runtimes(WorkerSettings())


def need(runtimes: Runtimes, *model_ids: str) -> None:
    for model_id in model_ids:
        if runtimes.models.status(runtimes.spec(model_id)).state != "installed":
            pytest.skip(f"model {model_id} not installed (rushbase models pull)")


def cosine(a: np.ndarray, b: list[float]) -> float:
    return float(a @ np.array(b, dtype=np.float32))


def test_siglip_vision_matches_the_reference_implementation(runtimes: Runtimes) -> None:
    """Golden vectors made with transformers (worker/tests/fixtures/ai/make_siglip_fixture.py)."""
    need(runtimes, IMAGE_EMBEDDING_MODEL)
    golden = json.loads((FIXTURES / "siglip2_golden.json").read_text())
    image = Image.open(FIXTURES / "siglip_test.png")
    for role, minimum in (("vision-fp16", 0.99), ("vision-int8", 0.94)):
        path = runtimes.models.file(IMAGE_EMBEDDING_MODEL, role)
        vector = ImageEmbedder(onnx.open_session(path, use_gpu=False)).embed([image])[0]
        assert cosine(vector, golden["image_embedding"]) >= minimum, role


def test_siglip_text_tokens_and_vectors_match_the_reference(runtimes: Runtimes) -> None:
    need(runtimes, IMAGE_EMBEDDING_MODEL)
    golden = json.loads((FIXTURES / "siglip2_golden.json").read_text())
    embedder = runtimes.siglip_text_embedder()
    assert embedder.encode(golden["texts"]).tolist() == golden["transformers_input_ids"]
    vectors = embedder.embed(golden["texts"])
    for vector, reference in zip(vectors, golden["text_embeddings"], strict=True):
        assert cosine(vector, reference) >= 0.99


def test_siglip_zero_shot_ranks_the_matching_text_first(runtimes: Runtimes) -> None:
    need(runtimes, IMAGE_EMBEDDING_MODEL)
    golden = json.loads((FIXTURES / "siglip2_golden.json").read_text())
    image = np.array(golden["image_embedding"], dtype=np.float32)
    scores = runtimes.siglip_text_embedder().embed(golden["texts"]) @ image
    assert golden["texts"][int(np.argmax(scores))] == "a red square on a blue background"


def test_e5_prefers_paraphrases_over_unrelated_text(runtimes: Runtimes) -> None:
    need(runtimes, TEXT_EMBEDDING_MODEL)
    vectors = runtimes.e5_embedder().embed(
        ["Ein Hund rennt über die Wiese.", "A dog runs across the meadow.", "Quarterly tax filing"]
    )
    assert vectors.shape == (3, 768)
    assert vectors[0] @ vectors[1] > vectors[0] @ vectors[2] + 0.05


def test_ced_recognises_a_sine_tone(runtimes: Runtimes) -> None:
    need(runtimes, runtimes.events_model)
    model = runtimes.events_model
    labels = load_labels(runtimes.models.file(model, "config"))
    session = runtimes.onnx_session("ced", runtimes.models.file(model, "model"))
    classifier = EventClassifier(session, labels)
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(2 * SAMPLE_RATE) / SAMPLE_RATE)).astype(
        np.float32
    )
    probs, _ = classifier.probabilities(tone, 2.0, 0.5)
    assert labels[int(np.argmax(probs[0]))] == "Sine wave"


@pytest.mark.gpu
async def test_whisper_transcribes_speech_on_this_machine(runtimes: Runtimes) -> None:
    sample = os.environ.get("RUSHBASE_TEST_SPEECH_WAV")
    if not sample or not os.path.isfile(sample):  # noqa: ASYNC240
        pytest.skip("set RUSHBASE_TEST_SPEECH_WAV to a speech recording")
    need(runtimes, runtimes.asr_model)
    backend = runtimes.whisper_backend()
    if backend is None:
        pytest.skip("whisper-cli not installed")
    result = await transcribe(
        backend, runtimes.models.file(runtimes.asr_model, "model"), Path(sample),
        use_gpu="cpu" not in backend.name and runtimes.gpu_backend, threads=8, timeout=300,
        extra_lib_dirs=runtimes.backend_library_dirs(),
    )  # fmt: skip
    assert result.language == "en"
    text = " ".join(s.text for s in result.segments).lower()
    assert "country" in text
    words = [w for s in result.segments for w in s.words]
    assert words
    assert all(a.start_ms <= b.start_ms for a, b in itertools.pairwise(words))
