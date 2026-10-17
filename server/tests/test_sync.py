from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from rushbase_core import MediaType
from rushbase_server.models import Asset, AssetMetadata
from rushbase_server.services import sync
from support import Server, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


def test_timecode_parsing_and_scoring() -> None:
    assert sync.parse_timecode_ms("01:00:00.500", None) == 3_600_500
    assert sync.parse_timecode_ms("00:00:01:12", 24.0) == 1500
    assert sync.parse_timecode_ms("garbage", 24.0) is None
    video = sync.TimeRange(0, 60_000, "timecode")
    audio = sync.TimeRange(10_000, 70_000, "timecode")
    offset, confidence = sync.score(audio, video) or (0, 0.0)
    assert offset == 10_000
    assert 0.8 < confidence < 0.96
    assert sync.score(sync.TimeRange(61_000, 70_000, "timecode"), video) is None
    assert sync.score(sync.TimeRange(0, 1, "capture_at"), video) is None  # bases differ


async def test_suggestion_is_stored_and_needs_confirmation(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        factory = server.runtime.session_factory
        video, batch = await seed_asset(factory, duration_ms=60_000)
        audio, _ = await seed_asset(factory, batch, media_type=MediaType.AUDIO, duration_ms=60_000)
        far, _ = await seed_asset(factory, batch, media_type=MediaType.AUDIO, duration_ms=1000)
        async with factory() as session:
            for asset, section, code in (
                (video, "video", "10:00:00:00"),
                (audio, "audio", "10:00:05.000"),
                (far, "audio", "12:00:00.000"),
            ):
                session.add(
                    AssetMetadata(
                        asset_id=asset.id,
                        normalized={section: {"timecode": code, "fps": 25.0}},
                    )
                )
            await session.flush()
            for asset in (video, audio, far):
                await sync.suggest_for_asset(session, await session.get_one(Asset, asset.id))
            await session.commit()

        detail = (await server.client.get(f"/api/v1/assets/{video.id}")).json()
        assert [s["other_asset_id"] for s in detail["sync"]] == [str(audio.id)]
        suggestion = detail["sync"][0]
        assert suggestion["status"] == "suggested"
        assert suggestion["offset_ms"] == 5000
        assert suggestion["basis"] == "timecode"
        async with factory() as session:
            assert await sync.confirmed_sections(session, video.id) == []

        decide = await server.client.post(
            f"/api/v1/sync-suggestions/{suggestion['id']}",
            json={"decision": "confirmed"},
            headers=admin,
        )
        assert decide.status_code == 204
        async with factory() as session:
            sections = await sync.confirmed_sections(session, audio.id)
        assert sections[0].other_role == "video"
        assert sections[0].offset_ms == 5000
        again = (await server.client.get(f"/api/v1/assets/{audio.id}")).json()
        assert again["sync"][0]["status"] == "confirmed"
        missing = await server.client.post(
            f"/api/v1/sync-suggestions/{far.id}", json={"decision": "rejected"}, headers=admin
        )
        assert missing.status_code == 404
