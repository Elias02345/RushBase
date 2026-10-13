from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from rushbase_core import init_library
from rushbase_server.app import create_app
from rushbase_server.clock import ManualClock
from rushbase_server.db import SessionFactory, create_engine, create_session_factory
from rushbase_server.models import Base
from support import (
    TEST_DATABASE_URL,
    UNREACHABLE_URL,
    Server,
    create_database,
    drop_database,
    make_settings,
    migrate,
)


@pytest.fixture
def unreachable_url() -> str:
    return UNREACHABLE_URL


@pytest.fixture(scope="session")
def _migrated_database_url() -> Iterator[str]:
    if not TEST_DATABASE_URL:
        pytest.skip("RUSHBASE_TEST_DATABASE_URL not set")
    url = create_database(TEST_DATABASE_URL)
    try:
        migrate(url)
        yield url
    finally:
        drop_database(url)


@pytest.fixture
def database_url(_migrated_database_url: str) -> str:
    """URL of a migrated, empty test database (tables truncated before each test)."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with psycopg.connect(
        make_url(_migrated_database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False),
        autocommit=True,
    ) as connection:
        connection.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    return _migrated_database_url


@pytest.fixture
def fresh_database_url() -> Iterator[str]:
    """A brand-new database without any schema (dropped afterwards)."""
    if not TEST_DATABASE_URL:
        pytest.skip("RUSHBASE_TEST_DATABASE_URL not set")
    url = create_database(TEST_DATABASE_URL)
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture
def library_root(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    init_library(root)
    return root


ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def client_factory() -> ClientFactory:
    @asynccontextmanager
    async def factory(database_url: str) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(make_settings(database_url))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return factory


@pytest.fixture
def server_factory() -> Callable[..., AbstractAsyncContextManager[Server]]:
    @asynccontextmanager
    async def factory(database_url: str, **overrides: Any) -> AsyncIterator[Server]:
        clock = ManualClock()
        app = create_app(make_settings(database_url, **overrides), clock=clock)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield Server(app, client, clock)

    return factory


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    created = create_engine(make_settings(database_url))
    yield created
    await created.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(engine)
