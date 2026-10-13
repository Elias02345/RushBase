from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest

from rushbase_server import get_version

ClientFactory = Callable[[str], AbstractAsyncContextManager[httpx.AsyncClient]]


async def test_health_degraded_without_database(
    client_factory: ClientFactory, unreachable_url: str
) -> None:
    async with client_factory(unreachable_url) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "version": get_version(), "database": False}


@pytest.mark.postgres
async def test_health_ok_with_database(client_factory: ClientFactory, database_url: str) -> None:
    async with client_factory(database_url) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": get_version(), "database": True}


async def test_openapi_documents_health(
    client_factory: ClientFactory, unreachable_url: str
) -> None:
    async with client_factory(unreachable_url) as client:
        schema = (await client.get("/openapi.json")).json()
    assert "/health" in schema["paths"]
