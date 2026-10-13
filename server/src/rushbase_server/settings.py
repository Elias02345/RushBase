"""Runtime configuration, read from ``RUSHBASE_*`` environment variables or ``.env``."""

import os
import socket
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rushbase_core import (
    DEFAULT_ENABLED_STAGES,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LEASE_SECONDS,
    Stage,
    parse_stages,
)


def default_holder_id() -> str:
    """Identify this server process: ``host:pid:random``."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


class Settings(BaseSettings):
    """Server settings.

    Security-relevant values have no defaults: the server refuses to start without them.
    """

    model_config = SettingsConfigDict(
        env_prefix="RUSHBASE_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # blank keys copied from .env.example count as unset
        extra="ignore",
    )

    secret_key: SecretStr = Field(
        min_length=32, description="Master secret for signing/encryption."
    )
    database_url: SecretStr = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/db",
    )
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    library_root: Path | None = Field(
        default=None, description="Local mount point of the media library."
    )

    # --- server lease (library.json) ---
    holder_id: str = Field(default_factory=default_holder_id, min_length=1)
    force_takeover: bool = False
    server_lease_seconds: float = Field(default=DEFAULT_LEASE_SECONDS, gt=0)
    server_lease_heartbeat_seconds: float = Field(default=DEFAULT_HEARTBEAT_SECONDS, gt=0)

    # --- background work ---
    background_tasks: bool = True
    scan_interval_seconds: float = Field(default=60, gt=0)
    stable_scans: int = Field(default=2, ge=1)
    stable_seconds: float = Field(default=120, ge=0)
    maintenance_interval_seconds: float = Field(default=15, gt=0)

    # --- pipeline and queue ---
    pipeline_stages: str = ",".join(DEFAULT_ENABLED_STAGES)
    job_lease_seconds: float = Field(default=120, gt=0)
    job_heartbeat_seconds: float = Field(default=30, gt=0)
    job_max_attempts: int = Field(default=5, ge=1)
    job_backoff_base_seconds: float = Field(default=30, ge=0)
    job_backoff_cap_seconds: float = Field(default=3600, ge=0)
    worker_online_seconds: float = Field(default=90, gt=0)

    inprocess_stages: str = Field(
        default="",
        description="Stages the server runs itself for worker-less setups (supported: hash).",
    )

    # --- auth ---
    cookie_secure: bool = Field(
        default=False, description="Send session cookies only over HTTPS (enable behind TLS)."
    )
    session_ttl_hours: float = Field(default=168, gt=0)
    login_max_failures: int = Field(default=5, ge=1)
    login_window_seconds: float = Field(default=900, gt=0)
    enrollment_ttl_hours: float = Field(default=24, gt=0)

    # --- backups ---
    backup_enabled: bool = True
    backup_interval_hours: float = Field(default=6, gt=0)
    backup_keep_interval: int = Field(default=8, ge=1)
    backup_keep_daily: int = Field(default=14, ge=1)
    pg_dump: str | None = Field(default=None, description="Path to pg_dump (default: PATH).")
    pg_restore: str | None = Field(default=None, description="Path to pg_restore (default: PATH).")
    pg_docker_container: str | None = Field(
        default=None, description="Development fallback: run pg_dump/pg_restore via docker exec."
    )

    # --- ops ---
    metrics_enabled: bool = False

    @field_validator("pipeline_stages")
    @classmethod
    def _validate_stages(cls, value: str) -> str:
        parse_stages(value.split(","))  # raises ValueError for unknown stage names
        return value

    @field_validator("inprocess_stages")
    @classmethod
    def _validate_inprocess(cls, value: str) -> str:
        names = [n.strip() for n in value.split(",") if n.strip()]
        unsupported = [n for n in names if Stage(n) not in {Stage.HASH}]
        if unsupported:
            raise ValueError(f"no in-process implementation for stage(s): {', '.join(unsupported)}")
        return value

    @property
    def inprocess(self) -> tuple[Stage, ...]:
        """Stages the in-process server worker executes."""
        return parse_stages(self.inprocess_stages.split(","))

    @property
    def enabled_stages(self) -> tuple[Stage, ...]:
        """Stages configured in ``pipeline_stages`` (``finalize`` is always added by the plan)."""
        return parse_stages(self.pipeline_stages.split(","))
