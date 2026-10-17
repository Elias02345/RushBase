"""Helpers shared by the server tests (importable as ``support``)."""

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from rushbase_core import MediaType, uuid7
from rushbase_server.clock import ManualClock
from rushbase_server.db import SessionFactory
from rushbase_server.models import Asset, AssetFile, Batch
from rushbase_server.runtime import Runtime
from rushbase_server.services import queue
from rushbase_server.settings import Settings

UNREACHABLE_URL = "postgresql+psycopg://nobody:nopass@127.0.0.1:1/none?connect_timeout=1"
TEST_DATABASE_URL = os.environ.get("RUSHBASE_TEST_DATABASE_URL")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
PG_CONTAINER = os.environ.get("RUSHBASE_TEST_PG_CONTAINER", "rushbase-dev-postgres-1")
PASSWORD = "correct horse battery"


def _admin_dsn(url: str) -> str:
    return (
        make_url(url)
        .set(drivername="postgresql", database="postgres")
        .render_as_string(hide_password=False)
    )


def create_database(base_url: str) -> str:
    """Create an empty, uniquely named database on the server of ``base_url``; return its URL."""
    name = f"rb_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_admin_dsn(base_url), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    return make_url(base_url).set(database=name).render_as_string(hide_password=False)


def drop_database(url: str) -> None:
    name = make_url(url).database
    with psycopg.connect(_admin_dsn(url), autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def migrate(url: str, target: str = "head") -> None:
    """Run Alembic against ``url`` (the env reads the settings from the environment)."""
    previous = {k: os.environ.get(k) for k in ("RUSHBASE_DATABASE_URL", "RUSHBASE_SECRET_KEY")}
    os.environ["RUSHBASE_DATABASE_URL"] = url
    os.environ["RUSHBASE_SECRET_KEY"] = "m" * 40
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), target)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def make_settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "secret_key": SecretStr("t" * 40),
        "database_url": SecretStr(database_url),
        "library_root": None,  # never inherit the developer's RUSHBASE_LIBRARY_ROOT
        "background_tasks": False,
        "pg_docker_container": PG_CONTAINER if shutil.which("docker") else None,
        "login_max_failures": 3,
        "cookie_secure": False,  # the test client speaks plain http
        **overrides,
    }
    return Settings(_env_file=None, **values)


@dataclass
class Server:
    """A running app (lifespan active) plus helpers for tests."""

    app: FastAPI
    client: httpx.AsyncClient
    clock: ManualClock

    @property
    def runtime(self) -> Runtime:
        runtime: Runtime = self.app.state.runtime
        return runtime

    async def setup_admin(self, username: str = "admin") -> dict[str, str]:
        """Run first-run setup; returns the CSRF header for subsequent unsafe requests."""
        response = await self.client.post(
            "/api/v1/setup", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 201, response.text
        return {"X-CSRF-Token": response.json()["csrf_token"]}


async def seed_asset(
    factory: SessionFactory,
    batch: Batch | None = None,
    *,
    stages: tuple[str, ...] = ("probe", "hash", "preview", "shots", "finalize"),
    media_type: MediaType = MediaType.VIDEO,
    config: queue.QueueConfig | None = None,
    size: int = 1000,
    duration_ms: int | None = None,
) -> tuple[Asset, Batch]:
    """Create a batch (unless given), an asset with one file and its job DAG."""
    async with factory() as session:
        if batch is None:
            batch = Batch(id=uuid7(), input_path=f"Input/b-{uuid7()}", label="b")
            session.add(batch)
            await session.flush()
            await session.refresh(batch)
        asset = Asset(
            id=uuid7(),
            batch_id=batch.id,
            media_type=media_type.value,
            size_total=size,
            duration_ms=duration_ms,
        )
        session.add(asset)
        await session.flush()
        session.add(
            AssetFile(
                id=uuid7(),
                asset_id=asset.id,
                role="primary",
                rel_path=f"Input/{asset.id}.mov",
                original_rel_path=f"Input/{asset.id}.mov",
                size=size,
            )
        )
        await queue.create_jobs_for_asset(
            session, asset, batch, stages, config or queue.QueueConfig()
        )
        await session.commit()
        return asset, batch
