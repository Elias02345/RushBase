"""CLI commands of the AI toolchain: ``hardware``, ``models`` and ``bench``."""

from typing import Annotated, Any

import typer

from rushbase_worker.assets import BackendStore, ModelStore, load_models_lock
from rushbase_worker.assets.download import DownloadError
from rushbase_worker.assets.plan import models_for_profile
from rushbase_worker.config import WorkerSettings
from rushbase_worker.hardware import HardwareReport, Profile, detect_hardware
from rushbase_worker.output import emit_json, envelope

JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
ProfileOption = Annotated[
    Profile | None, typer.Option(help="Hardware profile (default: detected).")
]

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
    backends: Annotated[
        bool, typer.Option(help="Also install llama.cpp (and whisper fallback).")
    ] = True,
    as_json: JsonOption = False,
) -> None:
    """Download models (and inference binaries) with SHA-256 verification; resumable."""
    settings = WorkerSettings()
    report: HardwareReport = detect_hardware()
    emit = _emitter("models pull", as_json)
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
