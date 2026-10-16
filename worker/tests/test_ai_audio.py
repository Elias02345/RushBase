import numpy as np
import pytest

from rushbase_core.results_ai import AudioEvent, AudioType
from rushbase_worker.audio.events import (
    EventClassifier,
    class_indices,
    group_events,
    load_tags,
    tag_display_name,
    window_starts,
)
from rushbase_worker.audio.extract import SAMPLE_RATE, parse_ebur128, write_wav
from rushbase_worker.audio.music import classify_audio_type, estimate_bpm

LABELS = ["Silence", "Speech", "Music", "Wind", "Gunshot", "Explosion", "Applause", "Noise"]
CLASS_NAMES = {name for spec in load_tags().values() for name in spec.classes}
ALL_LABELS = [*sorted(CLASS_NAMES), "Noise"]


def _probs(rows: list[dict[str, float]]) -> np.ndarray:
    matrix = np.zeros((len(rows), len(ALL_LABELS)), dtype=np.float32)
    for r, row in enumerate(rows):
        for name, value in row.items():
            matrix[r, ALL_LABELS.index(name)] = value
    return matrix


def _group(rows: list[dict[str, float]], **kw: object) -> list[AudioEvent]:
    tags = load_tags()
    n = len(rows)
    params: dict[str, object] = {
        "starts_s": [i * 0.5 for i in range(n)],
        "window_s": 2.0,
        "hop_s": 0.5,
        "duration_s": 0.5 * (n - 1) + 2.0,
        "thresholds": {name: spec.threshold for name, spec in tags.items()},
        "rms_db": [-30.0] * n,
        "silence_dbfs": -55.0,
        "min_hits": 2,
        "merge_gap_s": 1.0,
    }
    params.update(kw)
    return group_events(
        _probs(rows),
        ALL_LABELS,
        class_indices(ALL_LABELS, tags),
        **params,  # type: ignore[arg-type]
    )


def test_tag_table_names_resolve_and_missing_names_fail() -> None:
    tags = load_tags()
    assert set(tags) == {"speech", "music", "applause", "wind", "gunshot", "explosion", "silence"}
    assert class_indices(ALL_LABELS, tags)["speech"]
    with pytest.raises(ValueError, match="lacks classes"):
        class_indices(["Speech"], tags)


def test_display_names() -> None:
    assert tag_display_name("gunshot", "de") == "Schuss"
    assert tag_display_name("gunshot", "en") == "Gunshot"
    assert tag_display_name("custom", "de") == "custom"


def test_single_window_vote_is_smoothed_away() -> None:
    rows = [{"Speech": 0.1}] * 3 + [{"Gunshot": 0.9}] + [{"Speech": 0.1}] * 3
    assert _group(rows) == []


def test_consecutive_hits_form_a_segment_with_raw_top() -> None:
    rows = [{"Noise": 0.2}] * 2 + [{"Gunshot": 0.8, "Noise": 0.3}] * 3 + [{"Noise": 0.2}] * 2
    (event,) = _group(rows)
    assert event.tag == "gunshot"
    assert event.confidence == pytest.approx(0.8)
    assert event.raw_top[0][0] == "Gunshot"
    assert len(event.raw_top) <= 3
    assert 0 < event.start_ms < event.end_ms


def test_segments_with_small_gaps_merge_and_per_tag_threshold_applies() -> None:
    speech = {"Speech": 0.9}
    rows = [speech, speech, {"Noise": 0.1}, speech, speech, {"Noise": 0.1}]
    events = _group(rows, hop_s=0.5)
    assert [e.tag for e in events] == ["speech"]
    only_high = _group([{"Gunshot": 0.3}] * 4)
    assert only_high == []  # below the 0.35 gunshot threshold
    assert _group(
        [{"Gunshot": 0.3}] * 4, thresholds={**dict.fromkeys(load_tags(), 0.5), "gunshot": 0.2}
    )


def test_silence_by_level_even_when_classifier_is_unsure() -> None:
    events = _group([{"Noise": 0.1}] * 4, rms_db=[-90.0] * 4)
    assert [e.tag for e in events] == ["silence"]


def test_window_starts_cover_the_tail() -> None:
    assert window_starts(10, 20, 5) == [0]
    starts = window_starts(100, 40, 25)
    assert starts[0] == 0
    assert starts[-1] + 40 == 100


def test_classifier_batches_windows_and_pads_the_last() -> None:
    seen: list[tuple[int, ...]] = []

    class Session:
        def run(self, _names: object, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
            seen.append(feed["waveform"].shape)
            return [np.full((feed["waveform"].shape[0], len(ALL_LABELS)), 0.25, np.float32)]

    classifier = EventClassifier(Session(), ALL_LABELS)
    samples = np.zeros(int(3.3 * SAMPLE_RATE), dtype=np.float32)
    probs, starts = classifier.probabilities(samples, 2.0, 0.5)
    assert probs.shape == (len(starts), len(ALL_LABELS))
    assert starts[0] == 0
    assert all(shape[1] == 2 * SAMPLE_RATE for shape in seen)


def test_parse_ebur128_summary() -> None:
    stderr = """
[Parsed_ebur128_0 @ 0x1] t: 3.1 M: -20.0 S: -21.0 I: -30.0 LUFS LRA: 1.0 LU
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -15.4 LUFS
    Threshold: -25.6 LUFS

  Loudness range:
    LRA:         4.1 LU

  True peak:
    Peak:       -2.1 dBFS
"""
    loudness = parse_ebur128(stderr)
    assert (loudness.integrated_lufs, loudness.lra_lu, loudness.true_peak_dbtp) == (
        -15.4,
        4.1,
        -2.1,
    )
    assert parse_ebur128("I: -inf LUFS").integrated_lufs is None


def test_write_wav_is_16k_mono(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import wave

    path = tmp_path / "a.wav"
    write_wav(path, np.sin(np.linspace(0, 100, 16000)).astype(np.float32))
    with wave.open(str(path)) as handle:
        assert (handle.getframerate(), handle.getnchannels(), handle.getnframes()) == (
            16000,
            1,
            16000,
        )


def _event(tag: str, start: float, end: float) -> AudioEvent:
    return AudioEvent(
        tag=tag, start_ms=round(start * 1000), end_ms=round(end * 1000), confidence=0.9
    )


def test_audio_type_from_event_distribution() -> None:
    assert classify_audio_type([_event("music", 0, 50)], 60)[0] is AudioType.MUSIC  # type: ignore[index]
    assert classify_audio_type([_event("speech", 0, 50)], 60)[0] is AudioType.SPEECH  # type: ignore[index]
    mixed = [_event("speech", 0, 30), _event("music", 0, 30)]
    assert classify_audio_type(mixed, 60)[0] is AudioType.MIXED  # type: ignore[index]
    assert classify_audio_type([_event("silence", 0, 59)], 60)[0] is AudioType.SILENCE  # type: ignore[index]
    assert classify_audio_type([_event("gunshot", 0, 1)], 5)[0] is AudioType.SFX  # type: ignore[index]
    assert classify_audio_type([], 120)[0] is AudioType.AMBIENCE  # type: ignore[index]
    assert classify_audio_type([], 0) is None


def test_bpm_of_a_click_track() -> None:
    seconds, bpm = 30, 120
    samples = np.zeros(seconds * SAMPLE_RATE, dtype=np.float32)
    rng = np.random.default_rng(0)
    for beat in range(seconds * bpm // 60):
        start = int(beat * 60 / bpm * SAMPLE_RATE)
        samples[start : start + 400] = rng.standard_normal(400).astype(np.float32) * 0.5
    estimate = estimate_bpm(samples)
    assert estimate is not None
    assert estimate[0] == pytest.approx(120, abs=2)


def test_bpm_none_for_noise_and_short_clips() -> None:
    rng = np.random.default_rng(1)
    assert estimate_bpm(rng.standard_normal(30 * SAMPLE_RATE).astype(np.float32) * 0.1) is None
    assert estimate_bpm(np.zeros(2 * SAMPLE_RATE, dtype=np.float32)) is None
