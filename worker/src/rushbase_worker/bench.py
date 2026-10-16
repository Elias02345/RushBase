"""Benchmarks of the local AI stack (``rushbase bench``): VLM variants, Whisper, embeddings."""

import asyncio
import re
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from rushbase_worker.assets.plan import IMAGE_EMBEDDING_MODEL, TEXT_EMBEDDING_MODEL
from rushbase_worker.config import WorkerSettings
from rushbase_worker.hardware.detect import nvidia_gpus
from rushbase_worker.runtimes.llama import LlamaStartError, start_server
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.runtimes.whisper import transcribe
from rushbase_worker.taxonomy import (
    TaxonomyField,
    TaxonomyFlag,
    TaxonomySnapshot,
    TaxonomyValue,
)
from rushbase_worker.vision.describe import Frame, VisionError, describe_photo, describe_shot

Emit = Callable[[dict[str, Any]], None]
SHOT_FRAME = re.compile(r"shot-(\d+)-(start|middle|end)\.(?:jpg|png)$")
SAMPLE_INTERVAL = 0.4


def _values(*names: str) -> list[TaxonomyValue]:
    return [TaxonomyValue(value=name) for name in names]


BENCH_TAXONOMY = TaxonomySnapshot(
    version=1,
    fields=[
        TaxonomyField(
            key="location",
            label_de="Ort",
            label_en="Location",
            kind="single",
            values=_values("studio", "street", "beach", "forest", "office"),
        ),
        TaxonomyField(
            key="topics",
            label_de="Themen",
            label_en="Topics",
            kind="multi",
            values=_values("sport", "technology", "nature", "people", "test"),
        ),
    ],
    flags=[
        TaxonomyFlag(
            key="sensitive",
            description_de="sensible Szene",
            description_en="scene with violence, nudity or other sensitive content",
        )
    ],
)
"""A taxonomy of realistic complexity so the grammar-constrained schema is exercised."""


class VramSampler:
    """Polls ``nvidia-smi`` in a thread and keeps the maximum used memory."""

    def __init__(self) -> None:
        self.peak_mb = 0
        self.total_mb = 0
        self.baseline_mb = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _used(self) -> tuple[int, int]:
        gpus = nvidia_gpus()
        if not gpus or gpus[0].vram_total_mb is None or gpus[0].vram_free_mb is None:
            return 0, 0
        return gpus[0].vram_total_mb - gpus[0].vram_free_mb, gpus[0].vram_total_mb

    def start(self) -> None:
        self.baseline_mb, self.total_mb = self._used()
        self.peak_mb = self.baseline_mb
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(SAMPLE_INTERVAL):
            self.peak_mb = max(self.peak_mb, self._used()[0])

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.peak_mb = max(self.peak_mb, self._used()[0])


@dataclass
class VisionItem:
    name: str
    frames: list[Frame]
    length_ms: int
    photo: bool = False


def collect_items(frames_dir: Path, photos: bool = True) -> list[VisionItem]:
    """Shot keyframe sets (``shot-NNNN-start|middle|end``) and loose photos below ``frames_dir``."""
    shots: dict[tuple[Path, int], dict[str, Path]] = {}
    loose: list[Path] = []
    for path in sorted(frames_dir.rglob("*")):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        match = SHOT_FRAME.search(path.name)
        if match:
            shots.setdefault((path.parent, int(match.group(1))), {})[match.group(2)] = path
        elif photos:
            loose.append(path)
    items: list[VisionItem] = []
    for (parent, index), positions in sorted(shots.items()):
        frames = [
            Frame(positions[p], 1000 * (i + 1), p)
            for i, p in enumerate(("start", "middle", "end"))
            if p in positions
        ]
        items.append(VisionItem(f"{parent.parent.name[:8]}/shot{index}", frames, 4000))
    items += [VisionItem(p.name, [Frame(p, label="photo")], 0, photo=True) for p in loose]
    return items


@dataclass
class VariantResult:
    variant: str
    model: str
    status: str = "ok"
    message: str = ""
    load_seconds: float = 0.0
    baseline_vram_mb: int = 0
    peak_vram_mb: int = 0
    total_vram_mb: int = 0
    calls: int = 0
    first_try_valid: int = 0
    eventually_valid: int = 0
    latencies: list[float] = field(default_factory=list)
    tokens_per_second: list[float] = field(default_factory=list)
    completion_tokens: list[int] = field(default_factory=list)
    prompt_tokens: list[int] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def med(values: list[float]) -> float | None:
            return round(statistics.median(values), 2) if values else None

        return {
            "variant": self.variant, "model": self.model, "status": self.status,
            "message": self.message[:300], "load_seconds": round(self.load_seconds, 1),
            "baseline_vram_mb": self.baseline_vram_mb, "peak_vram_mb": self.peak_vram_mb,
            "total_vram_mb": self.total_vram_mb,
            "headroom_mb": self.total_vram_mb - self.peak_vram_mb if self.total_vram_mb else None,
            "calls": self.calls, "first_try_valid": self.first_try_valid,
            "eventually_valid": self.eventually_valid,
            "json_validity_rate": (
                round(self.first_try_valid / self.calls, 3) if self.calls else None
            ),
            "latency_median_s": med(self.latencies),
            "latency_p95_s": (
                round(float(np.percentile(self.latencies, 95)), 2) if self.latencies else None
            ),
            "tokens_per_second_median": med(self.tokens_per_second),
            "completion_tokens_median": med([float(t) for t in self.completion_tokens]),
            "prompt_tokens_median": med([float(t) for t in self.prompt_tokens]),
        }  # fmt: skip


async def bench_vision(
    runtimes: Runtimes,
    variants: list[str],
    items: list[VisionItem],
    *,
    repeats: int,
    emit: Emit,
    taxonomy: TaxonomySnapshot = BENCH_TAXONOMY,
) -> list[VariantResult]:
    """Run every item ``repeats`` times against each variant (one fresh llama-server each)."""
    settings: WorkerSettings = runtimes.settings
    backend = await runtimes.ensure_backend("llama")
    results: list[VariantResult] = []
    for name in variants:
        variant = runtimes.profiles.variant(name)
        result = VariantResult(name, variant.model)
        results.append(result)
        try:
            await runtimes.ensure_models(variant.model)
        except Exception as exc:
            result.status, result.message = "not-installed", str(exc)
            continue
        sampler = VramSampler()
        sampler.start()
        began = time.monotonic()
        try:
            server = await start_server(
                backend, variant, runtimes.profiles.common,
                runtimes.models.file(variant.model, "model"),
                runtimes.models.file(variant.model, "mmproj"),
                settings.data_dir / "logs",
            )  # fmt: skip
        except LlamaStartError as exc:
            sampler.stop()
            result.status = "oom" if exc.oom else "error"
            result.message = str(exc)
            emit({"event": "variant_failed", "variant": name, "status": result.status})
            continue
        result.load_seconds = time.monotonic() - began
        emit(
            {
                "event": "variant_started",
                "variant": name,
                "load_seconds": round(result.load_seconds, 1),
            }
        )
        try:
            for repeat in range(repeats):
                for item in items:
                    result.calls += 1
                    try:
                        if item.photo:
                            _, stats = await describe_photo(
                                server,
                                item.frames[0],
                                taxonomy,
                                max_tokens=settings.vision_max_tokens,
                            )
                        else:
                            _, stats = await describe_shot(
                                server, item.frames, item.length_ms, 0, taxonomy,
                                max_tokens=settings.vision_max_tokens,
                            )  # fmt: skip
                    except VisionError as exc:
                        emit(
                            {
                                "event": "call_invalid",
                                "variant": name,
                                "item": item.name,
                                "error": str(exc)[:200],
                            }
                        )
                        continue
                    result.eventually_valid += 1
                    result.first_try_valid += 1 if stats.attempts == 1 else 0
                    result.latencies.append(stats.seconds)
                    result.prompt_tokens.append(stats.prompt_tokens)
                    result.completion_tokens.append(stats.completion_tokens)
                    if stats.tokens_per_second:
                        result.tokens_per_second.append(stats.tokens_per_second)
                    emit(
                        {
                            "event": "call_done",
                            "variant": name,
                            "item": item.name,
                            "repeat": repeat,
                            "seconds": round(stats.seconds, 2),
                            "attempts": stats.attempts,
                        }
                    )
        finally:
            await server.stop()
            sampler.stop()
        result.baseline_vram_mb, result.peak_vram_mb = sampler.baseline_mb, sampler.peak_mb
        result.total_vram_mb = sampler.total_mb
        await asyncio.sleep(3.0)  # let the driver release the memory before the next variant
    return results


async def bench_whisper(
    runtimes: Runtimes, audio: Path, duration_s: float, emit: Emit
) -> list[dict[str, Any]]:
    """Whisper large-v3-turbo on GPU and CPU, plus the small model on CPU."""
    backend = runtimes.whisper_backend()
    if backend is None:
        raise RuntimeError("whisper-cli is not installed")
    settings = runtimes.settings
    gpu_possible = runtimes.gpu_backend and "cpu" not in backend.name
    plan = [("large-v3-turbo-q5_0", True), ("large-v3-turbo-q5_0", False), ("small-q8_0", False)]
    rows: list[dict[str, Any]] = []
    for label, use_gpu in plan:
        model_id = f"whisper-{label}"
        if use_gpu and not gpu_possible:
            continue
        await runtimes.ensure_models(model_id)
        sampler = VramSampler()
        sampler.start()
        runs: list[float] = []
        try:
            for _ in range(2):  # first run includes model load into VRAM/page cache
                result = await transcribe(
                    backend, runtimes.models.file(model_id, "model"), audio,
                    use_gpu=use_gpu, threads=settings.whisper_threads, timeout=1800,
                    extra_lib_dirs=runtimes.backend_library_dirs(),
                )  # fmt: skip
                runs.append(result.seconds)
        finally:
            sampler.stop()
        row = {
            "model": label, "device": "cuda" if use_gpu else "cpu",
            "audio_seconds": round(duration_s, 1), "first_run_s": round(runs[0], 2),
            "second_run_s": round(runs[1], 2), "realtime_factor": round(runs[1] / duration_s, 3),
            "peak_vram_mb": sampler.peak_mb - sampler.baseline_mb if use_gpu else 0,
            "words": sum(len(s.words) for s in result.segments),
            "language": result.language,
        }  # fmt: skip
        rows.append(row)
        emit({"event": "whisper_row", **row})
    return rows


def bench_embeddings(runtimes: Runtimes, images: list[Path], emit: Emit) -> list[dict[str, Any]]:
    """CPU latency of the SigLIP vision tower (fp16/int8), the text towers and CED."""
    from rushbase_worker.embedding.siglip import ImageEmbedder
    from rushbase_worker.runtimes import onnx

    rows: list[dict[str, Any]] = []
    pictures = [Image.open(p).convert("RGB") for p in images[:12]]
    for role in ("vision-fp16", "vision-int8"):
        embedder = ImageEmbedder(
            onnx.open_session(runtimes.models.file(IMAGE_EMBEDDING_MODEL, role), use_gpu=False)
        )
        embedder.embed(pictures[:1])
        began = time.perf_counter()
        for _ in range(3):
            embedder.embed(pictures)
        per_image = (time.perf_counter() - began) / (3 * len(pictures)) * 1000
        rows.append(
            {"model": f"siglip2 {role}", "device": "cpu", "ms_per_item": round(per_image, 1)}
        )
    text = runtimes.siglip_text_embedder()
    began = time.perf_counter()
    for _ in range(5):
        text.embed(["a wide shot of a beach at sunset"] * 8)
    rows.append({"model": "siglip2 text-fp16", "device": "cpu",
                 "ms_per_item": round((time.perf_counter() - began) / 40 * 1000, 1)})  # fmt: skip
    e5 = runtimes.e5_embedder()
    sample = "Ein Mann geht am Strand entlang und schaut auf das Meer. " * 3
    began = time.perf_counter()
    for _ in range(5):
        e5.embed([sample] * 8)
    rows.append({"model": f"{TEXT_EMBEDDING_MODEL} (~40 tokens)", "device": "cpu",
                 "ms_per_item": round((time.perf_counter() - began) / 40 * 1000, 1)})  # fmt: skip
    from rushbase_worker.audio.events import EventClassifier, load_labels

    events = runtimes.events_model
    classifier = EventClassifier(
        runtimes.onnx_session("bench-ced", runtimes.models.file(events, "model")),
        load_labels(runtimes.models.file(events, "config")),
    )
    minute = np.random.default_rng(0).standard_normal(60 * 16000).astype(np.float32) * 0.05
    began = time.perf_counter()
    classifier.probabilities(minute, 2.0, 0.5)
    rows.append({"model": f"{events} (60 s of audio, 2 s windows)", "device": "cpu",
                 "ms_per_item": round((time.perf_counter() - began) * 1000, 1)})  # fmt: skip
    for row in rows:
        emit({"event": "embedding_row", **row})
    return rows
