import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from pydantic import SecretStr

from rushbase_server.app import create_app
from rushbase_server.settings import Settings

UNREACHABLE_URL = "postgresql+psycopg://nobody:nopass@127.0.0.1:1/none?connect_timeout=1"
TEST_DATABASE_URL = os.environ.get("RUSHBASE_TEST_DATABASE_URL")


@pytest.fixture
def unreachable_url() -> str:
    return UNREACHABLE_URL


@pytest.fixture
def database_url() -> str:
    """URL of a real PostgreSQL (with pgvector); tests using it are skipped without one."""
    if not TEST_DATABASE_URL:
        pytest.skip("RUSHBASE_TEST_DATABASE_URL not set")
    return TEST_DATABASE_URL


def make_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        secret_key=SecretStr("t" * 40),
        database_url=SecretStr(database_url),
    )


@pytest.fixture
def client_factory() -> Callable[[str], AbstractAsyncContextManager[httpx.AsyncClient]]:
    @asynccontextmanager
    async def factory(database_url: str) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(make_settings(database_url))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return factory
