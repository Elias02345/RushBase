"""Audio event classification: CED (AudioSet) windows grouped into Rushbase tags."""

import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushbase_core.results_ai import AudioEvent
from rushbase_worker.audio.extract import SAMPLE_RATE

TAGS_FILE = Path(__file__).resolve().parent / "tags.toml"
BATCH = 32
RAW_TOP = 3
MAX_MODEL_SECONDS = 10.0


@dataclass(frozen=True)
class TagSpec:
    """A Rushbase tag and the AudioSet classes it groups."""

    name: str
    classes: tuple[str, ...]
    threshold: float
    de: str
    en: str


def load_tags(path: Path = TAGS_FILE) -> dict[str, TagSpec]:
    """The grouping table (``tags.toml``)."""
    data = tomllib.loads(path.read_text())
    return {
        name: TagSpec(name, tuple(raw["classes"]), float(raw["threshold"]), raw["de"], raw["en"])
        for name, raw in data["tags"].items()
    }


def tag_display_name(tag: str, language: str) -> str:
    """Display name of a tag (``de`` or ``en``); unknown tags are returned unchanged."""
    spec = load_tags().get(tag)
    if spec is None:
        return tag
    return spec.de if language == "de" else spec.en


def load_labels(config_path: Path) -> list[str]:
    """AudioSet display names in model output order, from the model's ``config.json``."""
    id2label = json.loads(config_path.read_text())["id2label"]
    return [str(id2label[str(i)]) for i in range(len(id2label))]


def class_indices(labels: list[str], tags: dict[str, TagSpec]) -> dict[str, list[int]]:
    """Output indices per tag, looked up by display name; a missing name is an error."""
    index = {name: i for i, name in enumerate(labels)}
    missing = [c for spec in tags.values() for c in spec.classes if c not in index]
    if missing:
        raise ValueError(f"audio event model lacks classes: {', '.join(missing)}")
    return {name: [index[c] for c in spec.classes] for name, spec in tags.items()}


def window_starts(n_samples: int, window: int, hop: int) -> list[int]:
    """Start sample of every analysis window; the last one is zero-padded by the caller."""
    if n_samples <= window:
        return [0]
    starts = list(range(0, n_samples - window + 1, hop))
    if starts[-1] + window < n_samples:
        starts.append(n_samples - window)
    return starts


def window_rms_db(samples: NDArray[np.float32], starts: list[int], window: int) -> list[float]:
    """RMS level of every window in dBFS (``-120`` for digital silence)."""
    levels: list[float] = []
    for start in starts:
        chunk = samples[start : start + window]
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
        levels.append(20 * math.log10(rms) if rms > 1e-6 else -120.0)
    return levels


def group_events(
    probs: NDArray[np.float32],
    labels: list[str],
    indices: dict[str, list[int]],
    *,
    starts_s: list[float],
    window_s: float,
    hop_s: float,
    duration_s: float,
    thresholds: dict[str, float],
    rms_db: list[float],
    silence_dbfs: float,
    min_hits: int,
    merge_gap_s: float,
) -> list[AudioEvent]:
    """Turn per-window class probabilities into merged, smoothed tag segments.

    A window votes for a tag when its best class of the tag reaches the threshold. Every window
    owns the ``hop`` seconds around its centre; a tag needs ``min_hits`` consecutive votes, then
    segments closer than ``merge_gap_s`` are merged. Raw top-3 classes per segment are kept.
    """
    events: list[AudioEvent] = []
    centers = [s + window_s / 2 for s in starts_s]
    for tag, columns in indices.items():
        scores = probs[:, columns].max(axis=1)
        if tag == "silence":
            scores = np.where(np.array(rms_db) < silence_dbfs, 1.0, scores)
        active = scores >= thresholds[tag]
        for first, last in _runs(active, min_hits):
            start = max(0.0, centers[first] - hop_s / 2) if first > 0 else 0.0
            end = (
                min(duration_s, centers[last] + hop_s / 2)
                if last < len(centers) - 1
                else duration_s
            )
            segment = probs[first : last + 1].mean(axis=0)
            top = np.argsort(-segment)[:RAW_TOP]
            events.append(
                AudioEvent(
                    tag=tag,
                    start_ms=round(start * 1000),
                    end_ms=round(max(end, start) * 1000),
                    confidence=min(1.0, float(scores[first : last + 1].max())),
                    raw_top=[(labels[i], round(float(segment[i]), 4)) for i in top],
                )
            )
    return _merge(events, round(merge_gap_s * 1000))


def _runs(active: NDArray[np.bool_], min_len: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate([*active.tolist(), False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    return runs


def _merge(events: list[AudioEvent], gap_ms: int) -> list[AudioEvent]:
    merged: list[AudioEvent] = []
    for event in sorted(events, key=lambda e: (e.tag, e.start_ms)):
        previous = merged[-1] if merged else None
        if previous and previous.tag == event.tag and event.start_ms - previous.end_ms <= gap_ms:
            best = previous if previous.confidence >= event.confidence else event
            merged[-1] = AudioEvent(
                tag=event.tag,
                start_ms=previous.start_ms,
                end_ms=max(previous.end_ms, event.end_ms),
                confidence=best.confidence,
                raw_top=best.raw_top,
            )
        else:
            merged.append(event)
    return sorted(merged, key=lambda e: (e.start_ms, e.tag))


def total_seconds(events: list[AudioEvent], tag: str) -> float:
    """Summed duration of all events of ``tag``."""
    return sum((e.end_ms - e.start_ms) for e in events if e.tag == tag) / 1000


class EventClassifier:
    """Runs a CED ONNX model over overlapping windows."""

    def __init__(self, session: object, labels: list[str]) -> None:
        self._session = session
        self.labels = labels
        self.tags = load_tags()
        self.indices = class_indices(labels, self.tags)

    def probabilities(
        self, samples: NDArray[np.float32], window_s: float, hop_s: float
    ) -> tuple[NDArray[np.float32], list[float]]:
        """Per-window class probabilities and the window start times in seconds."""
        window = min(round(window_s * SAMPLE_RATE), round(MAX_MODEL_SECONDS * SAMPLE_RATE))
        hop = max(1, round(hop_s * SAMPLE_RATE))
        starts = window_starts(len(samples), window, hop)
        rows: list[NDArray[np.float32]] = []
        for offset in range(0, len(starts), BATCH):
            batch = np.zeros((len(starts[offset : offset + BATCH]), window), dtype=np.float32)
            for row, start in enumerate(starts[offset : offset + BATCH]):
                chunk = samples[start : start + window]
                batch[row, : len(chunk)] = chunk
            logits = self._session.run(None, {"waveform": batch})[0]  # type: ignore[attr-defined]
            # The exported graph already applies the sigmoid: outputs are probabilities.
            rows.append(np.clip(np.asarray(logits, dtype=np.float32), 0.0, 1.0))
        return np.concatenate(rows, axis=0), [s / SAMPLE_RATE for s in starts]
