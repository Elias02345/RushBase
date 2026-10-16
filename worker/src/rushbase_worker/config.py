"""Worker configuration: environment variables (``RUSHBASE_*``), ``.env`` and ``worker.toml``.

Precedence: constructor arguments, environment, ``.env``, ``<config dir>/worker.toml``,
defaults. Unknown keys are ignored so the file can be shared with the server's ``.env``.
"""

import os
from pathlib import Path
from typing import Any
from uuid import UUID

import platformdirs
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from rushbase_core import PlatformRoots, Stage
from rushbase_worker.hardware.model import Backend, Profile

APP_NAME = "rushbase-worker"
DEFAULT_STAGES = (Stage.PROBE, Stage.HASH, Stage.PREVIEW, Stage.SHOTS)


def default_data_dir() -> Path:
    """Per-platform data directory (API key, local state)."""
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def default_config_file() -> Path:
    """Per-platform location of the optional ``worker.toml``."""
    explicit = os.environ.get("RUSHBASE_WORKER_CONFIG")
    if explicit:
        return Path(explicit)
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / "worker.toml"


class WorkerSettings(BaseSettings):
    """Everything configurable about a worker."""

    model_config = SettingsConfigDict(
        env_prefix="RUSHBASE_", env_file=".env", extra="ignore", frozen=True
    )

    server_url: str | None = Field(default=None, description="Base URL of the Rushbase server.")
    library_root: Path | None = Field(default=None, description="Local mount of the library root.")
    library_roots: PlatformRoots = Field(
        default_factory=PlatformRoots,
        description="Per-OS mount points (used when `library_root` is not set).",
    )
    library_id: UUID | None = Field(
        default=None, description="Expected library id; defaults to the one the server reports."
    )
    data_dir: Path = Field(default_factory=default_data_dir)
    api_key_file: Path | None = Field(default=None, description="Default: <data_dir>/api_key")
    api_token: str | None = Field(
        default=None, description="Personal API token, only used by `rushbase status`."
    )

    stages: tuple[Stage, ...] = DEFAULT_STAGES
    concurrency: int | None = Field(default=None, ge=1, description="Default: CPU count / 2.")
    heartbeat_seconds: float | None = Field(default=None, gt=0, description="Default: server's.")
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    claim_batch: int | None = Field(default=None, ge=1, description="Default: concurrency.")

    ffmpeg: str | None = None
    ffprobe: str | None = None
    exiftool: str | None = None
    toolchain_dir: Path | None = None
    tool_timeout_seconds: float = Field(default=300.0, gt=0)
    long_tool_timeout_seconds: float = Field(
        default=6 * 3600.0, gt=0, description="Proxy encodes and full-length analysis."
    )
    ffmpeg_hwaccel: bool = Field(default=False, description="Pass `-hwaccel auto` when decoding.")

    poster_width: int = Field(default=1280, ge=64)
    sprite_tiles: int = Field(default=10, ge=1, le=60)
    sprite_tile_width: int = Field(default=320, ge=32)
    proxy_enabled: bool = True
    proxy_height: int = Field(default=720, ge=144)
    proxy_crf: int = Field(default=28, ge=0, le=51)
    proxy_preset: str = "veryfast"
    photo_preview_width: int = Field(default=2048, ge=256)
    waveform_width: int = Field(default=1600, ge=100)
    waveform_height: int = Field(default=200, ge=20)
    waveform_peaks: int = Field(default=1000, ge=10)
    luts: dict[str, Path] = Field(
        default_factory=dict,
        description="Log profile (as in ExposureInfo.log_profile, lower case) -> .cube LUT.",
    )

    min_shot_seconds: float = Field(default=0.6, gt=0)
    analysis_width: int = Field(default=320, ge=64)
    analysis_max_fps: float = Field(default=25.0, gt=0)
    keyframe_width: int = Field(default=960, ge=64)

    # --- AI stages (docs/worker-ai.md) ---
    auto_download: bool = Field(
        default=False,
        description="Download missing backends/models on demand (default off: run "
        "`rushbase models pull` first).",
    )
    profile: Profile | None = Field(default=None, description="Override the detected profile.")
    backend: Backend | None = Field(default=None, description="Override the detected backend.")
    vision_variant: str | None = Field(
        default=None, description="VLM variant from profiles.toml (default: the profile's)."
    )
    llama_server: str | None = Field(default=None, description="Path of llama-server.")
    whisper_cli: str | None = Field(default=None, description="Path of whisper-cli.")
    onnx_gpu: bool = Field(default=True, description="Use CUDA for ONNX models if available.")
    whisper_vad: bool = Field(default=True, description="Use the Silero VAD model if installed.")
    whisper_threads: int = Field(default=8, ge=1)
    event_thresholds: dict[str, float] = Field(
        default_factory=dict, description="Per-tag overrides of the audio event thresholds."
    )
    event_window_seconds: float = Field(default=2.0, gt=0, le=10)
    event_hop_seconds: float = Field(default=0.5, gt=0, le=10)
    event_min_hits: int = Field(default=2, ge=1, description="Consecutive windows to confirm.")
    event_merge_gap_seconds: float = Field(default=1.0, ge=0)
    silence_dbfs: float = Field(default=-55.0, description="RMS below this counts as silence.")
    speech_min_seconds: float = Field(default=1.0, ge=0, description="Below: skip Whisper.")
    vision_max_tokens: int = Field(default=2048, ge=256)
    vision_frame_width: int = Field(default=896, ge=256, description="Keyframe size sent to VLM.")
    taxonomy_match_threshold: float = Field(default=0.80, ge=0, le=1)
    proposal_min_confidence: float = Field(default=0.5, ge=0, le=1)
    review_default_confidence: float = Field(default=0.5, ge=0, le=1)
    review_field_confidence: dict[str, float] = Field(
        default_factory=dict, description="Per-field confidence thresholds for the review list."
    )
    zero_shot_disagreement: float = Field(
        default=0.35, ge=0, le=1, description="SigLIP probability that contradicts the VLM."
    )
    transcript_chunk_seconds: float = Field(default=30.0, gt=0)

    # --- DaVinci Resolve bridge (docs/companion.md, ADR-0002) ---
    resolve_python: Path | None = Field(
        default=None, description="Interpreter for the bridge (default: Resolve's ResolvePython)."
    )
    resolve_script_api: Path | None = Field(
        default=None, description="RESOLVE_SCRIPT_API override."
    )
    resolve_script_lib: Path | None = Field(
        default=None, description="RESOLVE_SCRIPT_LIB override."
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Add ``worker.toml`` as the lowest-priority source."""
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        path = default_config_file()
        if path.is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=path))
        return tuple(sources)

    @property
    def resolved_library_root(self) -> Path | None:
        """The local library root: explicit ``library_root`` or the per-OS mapping."""
        return self.library_root or self.library_roots.for_platform()

    @property
    def resolved_api_key_file(self) -> Path:
        """Where the worker API key is stored."""
        return self.api_key_file or self.data_dir / "api_key"

    @property
    def state_file(self) -> Path:
        """Local worker identity written at enrollment."""
        return self.data_dir / "worker.json"

    @property
    def effective_concurrency(self) -> int:
        """Configured concurrency or half of the CPU count (at least 1)."""
        return self.concurrency or max(1, (os.cpu_count() or 2) // 2)

    def with_overrides(self, **values: Any) -> "WorkerSettings":
        """Return a copy with the non-``None`` ``values`` applied (CLI flags)."""
        return self.model_copy(update={k: v for k, v in values.items() if v is not None})
