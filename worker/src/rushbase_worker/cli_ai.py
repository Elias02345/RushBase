"""CLI commands of the AI toolchain: ``hardware``, ``models`` and ``bench``."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from rushbase_worker.assets import BackendStore, ModelStore, load_models_lock
from rushbase_worker.assets.download import DownloadError
from rushbase_worker.assets.plan import models_for_profile
from rushbase_worker.bench import bench_embeddings, bench_vision, bench_whisper, collect_items
from rushbase_worker.config import WorkerSettings
from rushbase_worker.hardware import HardwareReport, Profile, detect_hardware
from rushbase_worker.output import emit_json, envelope
from rushbase_worker.runtimes.manager import Runtimes

JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
ProfileOption = Annotated[
    Profile | None, typer.Option(help="Hardware profile (default: detected).")
]

bench_app = typer.Typer(help="Benchmark the local AI stack on this machine.", no_args_is_help=True)
models_app = typer.Typer(
    help="Manage the pinned AI models and inference binaries.", no_args_is_help=True
)


def hardware_command(as_json: JsonOption = False) -> None:
    """Detect GPUs, CPU features, RAM and Vulkan devices; show backend and profile."""
    report = detect_hardware()
    if as_json:
        emit_json(envelope("hardware", ok=True, data=report.model_dump(mode="json")))
        return
    typer.echo(f"cpu:      {report.cpu.model} ({report.cpu.cores} cores, avx2={report.cpu.avx2}, "
               f"avx512={report.cpu.avx512})")  # fmt: skip
    typer.echo(
        f"ram:      {report.ram_total_mb} MiB total, {report.ram_available_mb} MiB available"
    )
    for gpu in report.gpus:
        typer.echo(
            f"gpu:      {gpu.name} ({gpu.vendor}) {gpu.vram_free_mb}/{gpu.vram_total_mb} MiB free"
        )
    typer.echo(f"vulkan:   {', '.join(report.vulkan_devices) or 'none'}")
    typer.echo(f"backend:  {report.backend.value}")
    typer.echo(f"profile:  {report.profile.value} (based on {report.effective_vram_mb} MiB)")


def _emitter(command: str, as_json: bool) -> Any:
    def emit(event: dict[str, object]) -> None:
        if as_json:
            emit_json(envelope(command, ok=event.get("event") != "error", data=event))
        elif event["event"] not in {"download_progress"}:
            typer.echo(" ".join(f"{k}={v}" for k, v in event.items()))
        else:
            typer.echo(f"\r{event['file']} {int(event['bytes']) * 100 // int(event['total'])}%",  # type: ignore[call-overload]
                       nl=False)  # fmt: skip

    return emit


def _resolve(ids: list[str]) -> list[Any]:
    lock = load_models_lock()
    try:
        return [lock.get(i) for i in ids]
    except KeyError as exc:
        raise typer.BadParameter(str(exc.args[0])) from exc


@models_app.command("list")
def models_list(as_json: JsonOption = False) -> None:
    """Show pinned models, backends and their state on disk."""
    settings = WorkerSettings()
    report = detect_hardware()
    statuses = [s.to_dict() for s in ModelStore(settings.data_dir).statuses()]
    backends = BackendStore(settings.data_dir).installed()
    if as_json:
        emit_json(envelope("models list", ok=True, data={
            "data_dir": str(settings.data_dir), "profile": report.profile.value,
            "models": statuses, "backends": backends,
        }))  # fmt: skip
        return
    typer.echo(f"data dir: {settings.data_dir}   profile: {report.profile.value}")
    for entry in statuses:
        typer.echo(f"  [{entry['state']:9}] {entry['id']:30} {int(entry['size']) / 1e9:6.2f} GB  "  # type: ignore[call-overload]
                   f"{entry['kind']}")  # fmt: skip
    typer.echo(f"backends: {', '.join(backends) or 'none installed'}")


@models_app.command("pull")
def models_pull(
    model: Annotated[
        list[str] | None, typer.Argument(help="Model ids (default: profile set).")
    ] = None,
    profile: ProfileOption = None,
    variant: Annotated[
        list[str] | None, typer.Option(help="Extra VLM variants (profiles.toml).")
    ] = None,
    all_models: Annotated[bool, typer.Option("--all", help="Pull every pinned model.")] = False,
    only: Annotated[
        list[str] | None,
        typer.Option(
            help="Pull only some files: MODEL_ID:ROLE[,ROLE...] (repeatable); installs no "
            "inference binaries. The server uses siglip2-base-patch16-256:text-fp16,tokenizer "
            "and multilingual-e5-base:model,tokenizer."
        ),
    ] = None,
    backends: Annotated[
        bool, typer.Option(help="Also install llama.cpp (and whisper fallback).")
    ] = True,
    as_json: JsonOption = False,
) -> None:
    """Download models (and inference binaries) with SHA-256 verification; resumable."""
    settings = WorkerSettings()
    report: HardwareReport = detect_hardware()
    emit = _emitter("models pull", as_json)
    if only:
        _pull_only(settings.data_dir, only, emit)
        return
    if all_models:
        ids = list(load_models_lock().models)
    elif model:
        ids = model
    else:
        ids = models_for_profile(profile or report.profile, tuple(variant or ()))
    specs = _resolve(ids)
    store = ModelStore(settings.data_dir)
    try:
        if backends:
            backend_store = BackendStore(settings.data_dir)
            backend_store.install_llama(report, emit)
            if backend_store.find_whisper(report) is None:
                backend_store.install_whisper_fallback(report, emit)
        for spec in specs:
            store.pull(spec, emit)
    except (DownloadError, RuntimeError) as exc:
        emit({"event": "error", "message": str(exc)})
        raise typer.Exit(1) from exc


def _pull_only(data_dir: Path, selectors: list[str], emit: Any) -> None:
    """Download selected files of selected models (``MODEL_ID:ROLE,ROLE``)."""
    store = ModelStore(data_dir)
    try:
        for selector in selectors:
            model_id, _, roles = selector.partition(":")
            wanted = tuple(r for r in roles.split(",") if r) or None
            store.pull(_resolve([model_id])[0], emit, roles=wanted)
    except (DownloadError, RuntimeError, KeyError) as exc:
        emit({"event": "error", "message": str(exc.args[0] if exc.args else exc)})
        raise typer.Exit(1) from exc


@models_app.command("verify")
def models_verify(
    model: Annotated[
        list[str] | None, typer.Argument(help="Model ids (default: all installed).")
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Re-hash installed files against the lock file."""
    settings = WorkerSettings()
    store = ModelStore(settings.data_dir)
    emit = _emitter("models verify", as_json)
    targets = (
        _resolve(model) if model else [s.spec for s in store.statuses() if s.state != "missing"]
    )
    failed = False
    for spec in targets:
        problems = store.verify(spec, emit)
        failed |= bool(problems)
        emit({"event": "verified", "id": spec.id, "ok": not problems, "problems": problems})
    raise typer.Exit(1 if failed else 0)


@models_app.command("remove")
def models_remove(
    model: Annotated[list[str], typer.Argument(help="Model ids to delete.")],
    as_json: JsonOption = False,
) -> None:
    """Delete models from the data directory."""
    store = ModelStore(WorkerSettings().data_dir)
    emit = _emitter("models remove", as_json)
    for spec in _resolve(model):
        emit({"event": "removed", "id": spec.id, "removed": store.remove(spec)})


@bench_app.command("vision")
def bench_vision_command(
    frames: Annotated[
        Path, typer.Option(exists=True, file_okay=False, help="Directory with keyframes.")
    ],
    profiles: Annotated[
        str, typer.Option(help="Comma separated VLM variants (profiles.toml).")
    ] = "gpu8-4b,gpu8-9b,baseline-qwen3vl8b",
    repeats: Annotated[int, typer.Option(min=1, help="Passes over all items per variant.")] = 2,
    output: Annotated[Path | None, typer.Option(help="Write the full result JSON here.")] = None,
    as_json: JsonOption = False,
) -> None:
    """Peak VRAM, latency per shot (3 images), tokens/s and JSON validity per VLM variant."""
    runtimes = Runtimes(WorkerSettings())
    items = collect_items(frames)
    if not items:
        raise typer.BadParameter(f"no keyframes (shot-NNNN-start|middle|end.jpg) below {frames}")
    emit = _emitter("bench vision", as_json)
    results = asyncio.run(
        bench_vision(
            runtimes, [v.strip() for v in profiles.split(",")], items, repeats=repeats, emit=emit
        )
    )
    summaries = [r.summary() for r in results]
    document = {"hardware": runtimes.hardware.model_dump(mode="json"), "items": len(items),
                "repeats": repeats, "results": summaries}  # fmt: skip
    if output:
        output.write_text(json.dumps(document, indent=2, ensure_ascii=False))
    emit({"event": "bench_done", **document})


@bench_app.command("whisper")
def bench_whisper_command(
    audio: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="16 kHz speech WAV.")],
    seconds: Annotated[
        float, typer.Option(help="Duration of the audio (for the realtime factor).")
    ],
    output: Annotated[Path | None, typer.Option(help="Write the result JSON here.")] = None,
    as_json: JsonOption = False,
) -> None:
    """Whisper large-v3-turbo on CUDA vs CPU, and the small model on CPU."""
    runtimes = Runtimes(WorkerSettings())
    emit = _emitter("bench whisper", as_json)
    rows = asyncio.run(bench_whisper(runtimes, audio, seconds, emit))
    if output:
        output.write_text(json.dumps(rows, indent=2))
    emit({"event": "bench_done", "results": rows})


@bench_app.command("embed")
def bench_embed_command(
    images: Annotated[
        Path, typer.Option(exists=True, file_okay=False, help="Directory with images.")
    ],
    output: Annotated[Path | None, typer.Option(help="Write the result JSON here.")] = None,
    as_json: JsonOption = False,
) -> None:
    """CPU latency of SigLIP 2 (fp16/int8), the text towers and CED."""
    runtimes = Runtimes(WorkerSettings())
    emit = _emitter("bench embed", as_json)
    files = sorted(p for p in images.rglob("*") if p.suffix.lower() in {".jpg", ".png"})
    rows = bench_embeddings(runtimes, files, emit)
    if output:
        output.write_text(json.dumps(rows, indent=2))
    emit({"event": "bench_done", "results": rows})
