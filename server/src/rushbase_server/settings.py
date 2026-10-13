"""Runtime configuration, read from ``RUSHBASE_*`` environment variables or ``.env``."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server settings.

    Security-relevant values have no defaults: the server refuses to start without them.
    """

    model_config = SettingsConfigDict(
        env_prefix="RUSHBASE_",
        env_file=".env",
        env_file_encoding="utf-8",
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
