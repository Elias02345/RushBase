"""The server survives its library mount disappearing and coming back."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from pipeline import put_file
from rushbase_server.models import Asset, InputFile
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def count(server: Server, model: type) -> int:
    async with server.runtime.session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_library_mount_disappears_and_returns(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        put_file(library_root, "Input/Day/clip.mov", b"video-bytes")
        for _ in range(3):
            server.clock.advance(300)
            await server.runtime.scan_once()
        inputs_before = await count(server, InputFile)
        assets_before = await count(server, Asset)
        assert inputs_before == 1
        assert (await server.client.get("/health")).status_code == 200

        gone = library_root.with_name(library_root.name + "-unmounted")
        library_root.rename(gone)
        try:
            await server.runtime.binding.heartbeat()
            health = await server.client.get("/health")
            assert health.status_code == 503
            assert health.json()["library_mounted"] is False
            # Scanner and maintenance pause instead of marking everything as gone.
            assert await server.runtime.scan_once() is None
            await server.runtime.maintain_once()
            assert await count(server, InputFile) == inputs_before
            assert await count(server, Asset) == assets_before
            states = await server.client.get("/api/v1/status")
            assert states.status_code == 200  # the database side keeps serving
        finally:
            gone.rename(library_root)

        await server.runtime.binding.heartbeat()
        assert (await server.client.get("/health")).status_code == 200
        server.clock.advance(300)
        assert await server.runtime.scan_once() is not None
        assert await count(server, InputFile) == inputs_before
        assert (library_root / "Input/Day/clip.mov").read_bytes() == b"video-bytes"
