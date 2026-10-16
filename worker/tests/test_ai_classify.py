import base64
import json
import zlib
from collections.abc import Sequence

import httpx
import numpy as np
import pytest
from numpy.typing import NDArray

from ai_helpers import TAXONOMY, valid_answer
from rushbase_core.results_ai import (
    AudioEvent,
    AudioOutput,
    ModelRef,
    Transcript,
    TranscriptSegment,
    VisualDescription,
)
from rushbase_worker.classify.build import (
    Term,
    audio_tags,
    chunk_transcript,
    dedupe_tags,
    description_terms,
    guess_tags,
    map_terms,
    proposals_for,
)
from rushbase_worker.classify.matcher import TaxonomyMatcher, normalize
from rushbase_worker.classify.review import low_confidence_reasons
from rushbase_worker.classify.zeroshot import ZeroShot, class_probabilities
from rushbase_worker.client import WorkerClient
from rushbase_worker.embedding.siglip import from_vector, l2_normalize, preprocess, to_vector
from rushbase_worker.taxonomy import (
    TaxonomyField,
    TaxonomySnapshot,
    TaxonomySource,
    TaxonomyValue,
)


def hash_embed(texts: Sequence[str]) -> NDArray[np.float32]:
    """Deterministic fake embedding: character trigram hashing into 64 dimensions."""
    rows = np.zeros((len(texts), 64), dtype=np.float32)
    for r, text in enumerate(texts):
        padded = f"  {normalize(text.removeprefix('query: '))}  "
        for i in range(len(padded) - 2):
            rows[r, zlib.crc32(padded[i : i + 3].encode()) % 64] += 1.0
    return l2_normalize(rows)


SOUND = TaxonomyField(
    key="sound", label_de="Ton", label_en="Sound", kind="multi",
    values=[TaxonomyValue(value="gunshot", synonyms=["Schuss"]), TaxonomyValue(value="speech")],
)  # fmt: skip
SNAPSHOT = TaxonomySnapshot(version=7, fields=[*TAXONOMY.fields, SOUND], flags=TAXONOMY.flags)


def matcher(threshold: float = 0.8) -> TaxonomyMatcher:
    return TaxonomyMatcher(SNAPSHOT, hash_embed, threshold)


def test_normalize_folds_case_umlauts_and_separators() -> None:
    assert normalize("  Große_Küste-Weit ") == "grosse kuste weit"


def test_exact_synonym_and_embedding_matches() -> None:
    m = matcher(0.6)
    exact = m.match("Beach")
    assert exact is not None
    assert (exact.field, exact.value, exact.method, exact.confidence) == (
        "location", "beach", "exact", 1.0,
    )  # fmt: skip
    synonym = m.match("sports")
    assert synonym is not None
    assert (synonym.value, synonym.method) == ("sport", "synonym")
    near = m.match("beaches")
    assert near is not None
    assert near.method == "embedding"
    assert near.value == "beach"
    assert m.match("quantum chromodynamics") is None
    assert m.match("beach", field="topics") is None  # restricted to another field


def test_structured_terms_do_not_use_embeddings() -> None:
    terms = [Term("beaches", 0.9, 0, free=False), Term("beach", 0.9, 0, free=False)]
    tags, unmapped = map_terms(terms, matcher(), "vision")
    assert [t.value for t in tags] == ["beach"]
    assert unmapped == []


def test_confidence_is_scaled_by_match_quality() -> None:
    tags, _ = map_terms([Term("Schuss", 0.8, 2, free=True)], matcher(), "vision")
    assert tags[0].confidence == pytest.approx(0.8 * 0.95, abs=1e-3)
    assert (tags[0].method, tags[0].shot_idx, tags[0].origin) == ("synonym", 2, "vision")


def test_vlm_guesses_dedupe_single_fields_keep_best() -> None:
    from rushbase_worker.vision.schema import parse_description, to_visual_description

    raw = valid_answer(tax_location=[{"value": "beach", "confidence": 0.9}])
    description = to_visual_description(parse_description(json.dumps(raw)), 1, SNAPSHOT)
    tags = guess_tags([description])
    extra = tags[0].model_copy(update={"value": "forest", "confidence": 0.3})
    kept = dedupe_tags([*tags, extra], {"location"})
    assert [(t.field, t.value) for t in kept if t.field == "location"] == [("location", "beach")]


def test_audio_events_map_to_taxonomy_values_by_tag_or_display_name() -> None:
    audio = AudioOutput(has_audio=True, events=[
        AudioEvent(tag="gunshot", start_ms=0, end_ms=500, confidence=0.9),
        AudioEvent(tag="wind", start_ms=0, end_ms=500, confidence=0.9),
    ])  # fmt: skip
    tags = audio_tags(audio, matcher())
    assert [(t.field, t.value, t.origin) for t in tags] == [("sound", "gunshot", "audio")]


def test_unknown_recurring_terms_become_proposals() -> None:
    unmapped = [
        Term("drone", 0.9, 0, True), Term("Drone", 0.7, 1, True), Term("kite", 0.9, 0, True),
    ]  # fmt: skip
    proposals = proposals_for(unmapped, matcher(), n_descriptions=3, min_confidence=0.5)
    assert [p.term for p in proposals] == ["drone"]  # kite occurs once
    single = proposals_for(unmapped[2:], matcher(), n_descriptions=1, min_confidence=0.5)
    assert [p.term for p in single] == ["kite"]
    assert (
        proposals_for(unmapped, TaxonomyMatcher(TaxonomySnapshot(), hash_embed, 0.8), 3, 0.5) == []
    )


def test_description_terms_cover_structured_and_free_fields() -> None:
    from rushbase_worker.vision.schema import parse_description, to_visual_description

    description: VisualDescription = to_visual_description(
        parse_description(json.dumps(valid_answer())), 0, TAXONOMY
    )
    terms = {t.text: t.free for t in description_terms(description)}
    assert terms["wide"] is False
    assert terms["sand"] is True
    assert "unknown" not in terms


def test_low_confidence_and_true_flags_become_review_reasons() -> None:
    from rushbase_worker.vision.schema import parse_description, to_visual_description

    raw = valid_answer(
        shot_size_confidence=0.2, flag_critical={"value": True, "confidence": 0.9, "reason": "x"}
    )
    description = to_visual_description(parse_description(json.dumps(raw)), 3, TAXONOMY)
    reasons = low_confidence_reasons([description], lambda name: 0.5)
    assert "low_confidence:shot_size (shot 3)" in reasons
    assert "flag:critical (shot 3)" in reasons
    assert "low_confidence:topics (shot 3)" in reasons  # tax guess at 0.4
    relaxed = low_confidence_reasons([description], lambda name: 0.1)
    assert not [r for r in relaxed if r.startswith("low_confidence")]


def test_zero_shot_flags_only_clear_disagreements() -> None:
    prompts = {"wide": "wide", "close_up": "close", "full": "full"}

    def embed(texts: Sequence[str]) -> NDArray[np.float32]:
        index = {"wide": 0, "close": 1, "full": 2}
        out = np.zeros((len(texts), 4), dtype=np.float32)
        for r, text in enumerate(texts):
            for key, i in index.items():
                if key in text:
                    out[r, i] = 1.0
        return l2_normalize(out + 1e-3)

    import rushbase_worker.classify.zeroshot as module

    original = module.SHOT_SIZE_PROMPTS
    module.SHOT_SIZE_PROMPTS = prompts
    try:
        zero_shot = ZeroShot(embed)
    finally:
        module.SHOT_SIZE_PROMPTS = original
    close_image = l2_normalize(np.array([[0.05, 1.0, 0.0, 0.0]], dtype=np.float32))[0]
    assert zero_shot.check("shot_size", "wide", close_image, 0.35).siglip == "close_up"  # type: ignore[union-attr]
    assert zero_shot.check("shot_size", "close_up", close_image, 0.35) is None
    assert (
        zero_shot.check("shot_size", "medium", close_image, 0.35) is None
    )  # not in the prompt set
    assert class_probabilities(
        close_image, embed(["wide", "close", "full"])
    ).sum() == pytest.approx(1.0)


def test_transcript_chunks_of_about_thirty_seconds() -> None:
    segments = [
        TranscriptSegment(start_ms=i * 10_000, end_ms=i * 10_000 + 9_000, text=f"s{i}")
        for i in range(7)
    ]
    transcript = Transcript(language="de", segments=segments, model=_model())
    chunks = chunk_transcript(transcript, 30_000)
    assert [(c[0], c[2]) for c in chunks] == [(0, "s0 s1 s2 s3"), (40_000, "s4 s5 s6")]


def _model() -> ModelRef:
    return ModelRef(model_id="m", revision="r")


def test_vector_roundtrip_is_normalised_float32() -> None:
    vector = l2_normalize(np.random.default_rng(0).standard_normal((1, 768)).astype(np.float32))[0]
    packed = to_vector(vector)
    assert packed.dim == 768
    assert len(base64.b64decode(packed.b64)) == 768 * 4
    assert np.allclose(from_vector(packed), vector)
    assert np.linalg.norm(from_vector(packed)) == pytest.approx(1.0, abs=1e-5)


def test_siglip_preprocessing_shape_and_range() -> None:
    from PIL import Image

    pixels = preprocess(Image.new("RGB", (640, 360), (255, 0, 128)))
    assert pixels.shape == (3, 256, 256)
    assert pixels.dtype == np.float32
    assert pixels[0].min() == pytest.approx(1.0)
    assert pixels[1].max() == pytest.approx(-1.0)


async def test_taxonomy_client_falls_back_to_empty_on_404(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/taxonomy") and calls < 3:
            return httpx.Response(404, json={"detail": "no"})
        return httpx.Response(200, json={"version": 5, "fields": [], "flags": [], "extra": 1})

    async with WorkerClient("http://srv", "key", transport=httpx.MockTransport(handler)) as client:
        first = await client.taxonomy()
        second = await client.taxonomy()
        third = await client.taxonomy()
    assert (first.version, second.version, third.version) == (0, 0, 5)
    assert len([r for r in caplog.records if "taxonomy" in r.message]) == 1  # logged once


async def test_taxonomy_source_caches_and_defaults_to_empty() -> None:
    fetched = 0

    async def fetch() -> TaxonomySnapshot:
        nonlocal fetched
        fetched += 1
        return SNAPSHOT

    source = TaxonomySource(fetch)
    assert (await source.get()).version == 7
    assert (await source.get()).version == 7
    assert fetched == 1
    assert (await TaxonomySource().get()).version == 0
