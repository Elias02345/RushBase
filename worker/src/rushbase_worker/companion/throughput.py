"""Per-stage throughput history and the duration estimate shown before processing starts."""

import json
from pathlib import Path
from typing import Any

from rushbase_worker.config import WorkerSettings
from rushbase_worker.state import write_private

THROUGHPUT_FILE = "throughput.json"
MAX_SAMPLES = 2000

# Mean wall time of one job in milliseconds on a mid-range GPU PC (RTX 3070 class). Vision comes
# from docs/eval/2026-10-vlm-benchmark.md (about 11 s per call, two passes per asset); the others
# are typical values for 1080p footage on 8 cores.
DEFAULT_JOB_MS: dict[str, int] = {
    "probe": 400,
    "hash": 2500,
    "preview": 25000,
    "shots": 18000,
    "audio": 9000,
    "embed": 7000,
    "vision": 22000,
    "classify": 1500,
    "finalize": 200,
}
SERIAL_STAGES = frozenset({"vision", "audio", "embed", "classify"})
"""AI stages share one GPU/CPU model runtime, so they do not scale with the job concurrency."""


def history_path(settings: WorkerSettings) -> Path:
    """Location of the throughput history."""
    return settings.data_dir / THROUGHPUT_FILE


def load_history(settings: WorkerSettings) -> dict[str, list[int]]:
    """Recent per-stage job durations in milliseconds."""
    try:
        raw = json.loads(history_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: [int(x) for x in v][-MAX_SAMPLES:] for k, v in raw.items() if isinstance(v, list)}


def record_jobs(settings: WorkerSettings, durations: dict[str, list[int]]) -> None:
    """Append the durations of a finished run to the history."""
    if not any(durations.values()):
        return
    history = load_history(settings)
    for stage, values in durations.items():
        history[stage] = (history.get(stage, []) + values)[-MAX_SAMPLES:]
    write_private(history_path(settings), json.dumps(history) + "\n")


def mean_job_ms(history: dict[str, list[int]], stage: str) -> tuple[int, str]:
    """Mean duration of one job of ``stage`` and its source (``history`` or ``default``)."""
    samples = history.get(stage) or []
    if len(samples) >= 3:
        return sum(samples) // len(samples), "history"
    return DEFAULT_JOB_MS.get(stage, 10000), "default"


def estimate(
    backlog: dict[str, Any], history: dict[str, list[int]], concurrency: int
) -> dict[str, Any]:
    """Estimated wall time per stage and in total for a ``/status`` backlog document.

    Stages run one after the other in the estimate (a conservative upper bound); technical
    stages divide by ``concurrency``, AI stages run serially.
    """
    stages: dict[str, Any] = {}
    total = 0.0
    for name, entry in (backlog.get("stages") or {}).items():
        jobs = int(entry.get("pending_jobs", 0))
        mean, source = mean_job_ms(history, name)
        parallel = 1 if name in SERIAL_STAGES else max(1, concurrency)
        seconds = jobs * mean / parallel / 1000
        total += seconds
        stages[name] = {
            "pending_jobs": jobs,
            "mean_job_ms": mean,
            "seconds": round(seconds),
            "source": source,
        }
    return {"total_seconds": round(total), "stages": stages, "concurrency": concurrency}
