"""AI toolchain checks of ``rushbase doctor``: hardware, backends, models, profile."""

import json

from rushbase_worker.assets.lock import load_binaries_lock
from rushbase_worker.assets.plan import models_for_profile
from rushbase_worker.config import WorkerSettings
from rushbase_worker.doctor import Check
from rushbase_worker.runtimes import onnx
from rushbase_worker.runtimes.manager import Runtimes


def check_ai(settings: WorkerSettings) -> list[Check]:
    """Report backend, profile, inference binaries and the models of the active profile."""
    runtimes = Runtimes(settings)
    hardware = runtimes.hardware
    gpu = hardware.gpus[0] if hardware.gpus else None
    checks = [
        Check(
            "ai_hardware",
            "ok",
            f"backend {hardware.backend.value}, profile {hardware.profile.value}"
            + (f", {gpu.name} with {hardware.effective_vram_mb} MiB free" if gpu else ", no GPU"),
        ),
        Check("ai_onnx", "ok", f"ONNX Runtime providers: {', '.join(onnx.available_providers())}"),
    ]
    llama = runtimes.llama_backend()
    checks.append(
        Check("ai_llama_server", "ok", f"{llama.name} (pinned {load_binaries_lock().llama.tag})")
        if llama
        else Check("ai_llama_server", "warn", "llama-server not installed: `rushbase models pull`")
    )
    whisper = runtimes.whisper_backend()
    if whisper is None:
        checks.append(Check("ai_whisper_cli", "warn", "whisper-cli not installed"))
    else:
        info = whisper.path / "BUILD_INFO.json"
        origin = ""
        if info.is_file():
            build = json.loads(info.read_text())
            origin = f" (built from {build.get('tag')} for {build.get('backend')})"
        checks.append(Check("ai_whisper_cli", "ok", f"{whisper.name}{origin}"))
    missing: list[str] = []
    for model_id in models_for_profile(hardware.profile):
        if runtimes.models.status(runtimes.spec(model_id)).state != "installed":
            missing.append(model_id)
    checks.append(
        Check("ai_models", "ok", f"all models of profile {hardware.profile.value} installed")
        if not missing
        else Check("ai_models", "warn", f"missing: {', '.join(missing)} (`rushbase models pull`)")
    )
    return checks
