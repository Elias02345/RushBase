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
