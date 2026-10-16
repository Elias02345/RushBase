"""The runtime container the AI stages share: hardware, assets, GPU scheduling, sessions."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from functools import cached_property
from pathlib import Path
from typing import Any

import onnxruntime as ort

from rushbase_core import Stage
from rushbase_core.results_ai import ModelRef
from rushbase_worker.assets.backends import BackendStore, InstalledBackend, llama_platform_keys
from rushbase_worker.assets.lock import ModelSpec, load_models_lock
from rushbase_worker.assets.models import ModelNotInstalledError, ModelStore
from rushbase_worker.assets.plan import IMAGE_EMBEDDING_MODEL, TEXT_EMBEDDING_MODEL
from rushbase_worker.config import WorkerSettings
from rushbase_worker.embedding import e5, siglip
from rushbase_worker.hardware import Backend, HardwareReport, Profile, detect_hardware, free_vram_mb
from rushbase_worker.runtimes import onnx
from rushbase_worker.runtimes.lifecycle import ModelLifecycle
from rushbase_worker.runtimes.llama import LlamaServer, start_with_fallback
from rushbase_worker.runtimes.profiles import ProfilesFile, Variant, load_profiles

EventSink = Callable[[dict[str, object]], None]
VAD_MODEL = "whisper-vad-silero-v5.1.2"


class MissingBackendError(Exception):
    """An inference binary is not installed."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} is not installed; run `rushbase models pull` (or set --auto-download)"
        )


class Runtimes:
    """Resolves backends and models, schedules the GPU and hands out sessions."""

    def __init__(
        self, settings: WorkerSettings, emit: EventSink = lambda _event: None,
        report: HardwareReport | None = None,
    ) -> None:  # fmt: skip
        self.settings = settings
        self.emit = emit
        self._report = report
        self.models = ModelStore(settings.data_dir)
        self.backends = BackendStore(settings.data_dir)
        self.lifecycle = ModelLifecycle()
        self.profiles: ProfilesFile = load_profiles()
        self._sessions: dict[str, ort.InferenceSession] = {}
        self._download_lock = asyncio.Lock()
        self.last_vlm_fallbacks: list[str] = []

    # --- hardware ------------------------------------------------------------------------

    @cached_property
    def hardware(self) -> HardwareReport:
        """The probe result with ``backend``/``profile`` overrides from the settings applied."""
        report = self._report or detect_hardware()
        overrides: dict[str, Any] = {}
        if self.settings.backend is not None:
            overrides["backend"] = self.settings.backend
        if self.settings.profile is not None:
            overrides["profile"] = self.settings.profile
        return report.model_copy(update=overrides) if overrides else report

    @property
    def profile(self) -> Profile:
        return self.hardware.profile

    @property
    def gpu_backend(self) -> bool:
        return self.hardware.backend is not Backend.CPU

    # --- model selection -------------------------------------------------------------------

    def vlm_chain(self) -> list[Variant]:
        """The chosen VLM variant followed by its smaller fallbacks."""
        name = self.settings.vision_variant or self.profiles.profile(self.profile).vlm
        return self.profiles.chain(name)

    @property
    def asr_model(self) -> str:
        return self.profiles.profile(self.profile).asr

    @property
    def events_model(self) -> str:
        return self.profiles.profile(self.profile).audio_events

    @property
    def vision_embed_role(self) -> str:
        """SigLIP vision file role: fp16 everywhere (int8 loses fidelity, see docs/worker-ai.md)."""
        return f"vision-{self.profiles.profile(self.profile).embed_vision}"

    def spec(self, model_id: str) -> ModelSpec:
        return load_models_lock().get(model_id)

    def model_ref(self, model_id: str, role: str = "model", *, prompt_version: str | None = None,
                  profile: bool = False) -> ModelRef:  # fmt: skip
        """Contract ``ModelRef`` for an installed model (revision = SHA-256 prefix of its file)."""
        spec = self.spec(model_id)
        return ModelRef(
            model_id=model_id,
            revision=spec.file(role).sha256[:12],
            profile=self.profile.value if profile else None,
            prompt_version=prompt_version,
        )

    # --- availability --------------------------------------------------------------------

    def _installed(self, model_id: str) -> bool:
        return self.models.status(self.spec(model_id)).state == "installed"

    def missing_assets(self, stage: Stage) -> list[str]:
        """What a stage still needs (empty = can run, or can download on demand)."""
        if self.settings.auto_download:
            return []
        missing: list[str] = []

        def need_models(*ids: str) -> None:
            missing.extend(f"model {i}" for i in ids if not self._installed(i))

        match stage:
            case Stage.AUDIO:
                need_models(self.asr_model, self.events_model)
                if self.whisper_backend() is None:
                    missing.append("whisper-cli")
            case Stage.EMBED:
                need_models(IMAGE_EMBEDDING_MODEL)
            case Stage.VISION:
                if self.llama_backend() is None:
                    missing.append("llama-server")
                if not self.installed_vlm_chain():
                    missing.append(f"model {self.vlm_chain()[0].model}")
            case Stage.CLASSIFY:
                need_models(TEXT_EMBEDDING_MODEL, IMAGE_EMBEDDING_MODEL)
            case _:
                pass
        return missing

    def installed_vlm_chain(self) -> list[Variant]:
        """Variants of the chain whose model files are installed (all when downloads are on)."""
        chain = self.vlm_chain()
        if self.settings.auto_download:
            return chain
        return [v for v in chain if self._installed(v.model)]

    def llama_backend(self) -> InstalledBackend | None:
        if self.settings.llama_server:
            path = Path(self.settings.llama_server)
            return InstalledBackend(path.parent.name, path.parent, path) if path.is_file() else None
        return self.backends.find_llama(llama_platform_keys(self.hardware))

    def whisper_backend(self) -> InstalledBackend | None:
        if self.settings.whisper_cli:
            path = Path(self.settings.whisper_cli)
            return InstalledBackend(path.parent.name, path.parent, path) if path.is_file() else None
        return self.backends.find_whisper(self.hardware)

    def backend_library_dirs(self) -> list[Path]:
        """Shared-library directories of the installed CUDA runtime (used by whisper-cli)."""
        backend = self.llama_backend()
        return backend.lib_dirs if backend else []

    # --- downloads (opt-in) ----------------------------------------------------------------

    async def ensure_models(self, *model_ids: str) -> None:
        """Make sure models are installed; pull them only with ``auto_download``."""
        for model_id in model_ids:
            spec = self.spec(model_id)
            if self.models.status(spec).state == "installed":
                continue
            if not self.settings.auto_download:
                raise ModelNotInstalledError(model_id)
            async with self._download_lock:
                await asyncio.to_thread(self.models.pull, spec, self.emit)

    async def ensure_backend(self, name: str) -> InstalledBackend:
        """Return an installed ``llama`` / ``whisper`` backend, downloading it if allowed."""
        found = self.llama_backend() if name == "llama" else self.whisper_backend()
        if found is not None:
            return found
        if not self.settings.auto_download:
            raise MissingBackendError("llama-server" if name == "llama" else "whisper-cli")
        async with self._download_lock:
            if name == "llama":
                return await asyncio.to_thread(
                    self.backends.install_llama, self.hardware, self.emit
                )
            return await asyncio.to_thread(
                self.backends.install_whisper_fallback, self.hardware, self.emit
            )

    # --- GPU resources ---------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def vlm(self) -> AsyncIterator[LlamaServer]:
        """The running llama-server (started on first use, unloaded when the GPU is needed)."""
        async with self.lifecycle.use(
            "vlm", self._start_vlm, _stop_server, gpu=self.gpu_backend
        ) as s:
            yield s

    async def _start_vlm(self) -> LlamaServer:
        backend = await self.ensure_backend("llama")
        chain = self.vlm_chain()
        if not self.settings.auto_download:
            chain = self.installed_vlm_chain()
        if not chain:
            raise ModelNotInstalledError(self.vlm_chain()[0].model)
        for variant in chain:
            await self.ensure_models(variant.model)

        def resolve(model_id: str) -> tuple[Path, Path]:
            return self.models.file(model_id, "model"), self.models.file(model_id, "mmproj")

        server = await start_with_fallback(
            backend, chain, self.profiles.common, resolve, free_vram_mb,
            self.settings.data_dir / "logs", self.emit,
        )  # fmt: skip
        self.last_vlm_fallbacks = server.fallbacks
        self.emit({"event": "vlm_started", "variant": server.variant.name,
                   "port": server.port, "fallbacks": server.fallbacks})  # fmt: skip
        return server

    @contextlib.asynccontextmanager
    async def whisper_slot(self) -> AsyncIterator[None]:
        """Reserve the GPU for a Whisper run (unloads the VLM if it is resident)."""

        async def load() -> None:
            return None

        async def close(_resource: None) -> None:
            return None

        async with self.lifecycle.use("whisper", load, close, gpu=self.gpu_backend):
            yield

    # --- ONNX sessions (cached; CPU unless onnxruntime-gpu is installed) -------------------

    def onnx_session(self, key: str, path: Path, *, gpu: bool = False) -> ort.InferenceSession:
        """A cached ONNX Runtime session."""
        if key not in self._sessions:
            use_gpu = gpu and self.settings.onnx_gpu and self.gpu_backend
            self._sessions[key] = onnx.open_session(path, use_gpu=use_gpu)
        return self._sessions[key]

    def image_embedder(self) -> siglip.ImageEmbedder:
        """SigLIP 2 vision tower (fp16; CUDA when onnxruntime-gpu is installed, else CPU)."""
        role = self.vision_embed_role
        path = self.models.file(IMAGE_EMBEDDING_MODEL, role)
        return siglip.ImageEmbedder(self.onnx_session(f"siglip:{role}", path, gpu=True))

    def siglip_text_embedder(self) -> siglip.TextEmbedder:
        """SigLIP 2 text tower (fp16: the int8 export has cosine 0.88-0.97 to the reference)."""
        path = self.models.file(IMAGE_EMBEDDING_MODEL, "text-fp16")
        tokenizer = onnx.load_tokenizer(self.models.file(IMAGE_EMBEDDING_MODEL, "tokenizer"))
        return siglip.TextEmbedder(self.onnx_session("siglip:text", path), tokenizer)

    def e5_embedder(self) -> e5.TextEmbedder:
        """multilingual-e5-base."""
        path = self.models.file(TEXT_EMBEDDING_MODEL, "model")
        tokenizer = onnx.load_tokenizer(self.models.file(TEXT_EMBEDDING_MODEL, "tokenizer"))
        return e5.TextEmbedder(self.onnx_session("e5", path), tokenizer)

    async def close(self) -> None:
        """Unload everything (worker shutdown)."""
        await self.lifecycle.close()
        self._sessions.clear()


async def _stop_server(server: LlamaServer) -> None:
    await server.stop()
