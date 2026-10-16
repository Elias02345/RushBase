from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from ai_support import seed_shots_asset
from rushbase_server.models import EmbeddingImage, EmbeddingText, Job, Shot, TranscriptSegment
from rushbase_server.search.encoder import FakeEncoder, fake_vector
from rushbase_server.search.hybrid import Channel, rrf
from support import Server

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


def test_rrf_rewards_agreement_between_channels() -> None:
    a, b, c = (UUID(int=i) for i in (1, 2, 3))
    fused = rrf(
        [
            Channel("x", [(a, None), (b, None)]),
            Channel("y", [(b, None), (c, None)]),
        ]
    )
    assert [h.key[0] for h in fused] == [b, a, c]
    assert fused[0].ranks == {"x": 2, "y": 1}
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)


async def seed(server: Server) -> dict[str, UUID]:
    """Two videos: ``harbour`` (shots 0/1) and ``street``; image + text vectors, no text hits."""
    ids: dict[str, UUID] = {}
    async with server.runtime.session_factory() as session:
        for name in ("harbour", "street"):
            asset = await seed_shots_asset(session, shots=2)
            ids[name] = asset.id
            shots = (
                (await session.execute(select(Shot).where(Shot.asset_id == asset.id)))
                .scalars()
                .all()
            )
            for shot in shots:
                shot.keyframes = [
                    {"t_ms": 10, "rel_path": f".rushbase/derived/{asset.id}/kf{shot.idx}.jpg",
                     "position": "middle"}
                ]  # fmt: skip
                label = f"{name} scene {shot.idx}"
                ids[f"{name}{shot.idx}"] = shot.id
                session.add(
                    EmbeddingImage(
                        asset_id=asset.id,
                        shot_id=shot.id,
                        keyframe="middle",
                        model_id="siglip-fake",
                        revision="r1",
                        embedding=fake_vector(label, "image"),
                    )
                )
                session.add(
                    EmbeddingText(
                        asset_id=asset.id,
                        shot_id=shot.id,
                        kind="shot_description",
                        model_id="e5-fake",
                        revision="r1",
                        embedding=fake_vector(label, "text"),
                    )
                )
        await session.commit()
    return ids


async def test_search_degrades_to_fulltext_without_models(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await seed(server)
        caps = (await server.client.get("/api/v1/search/capabilities")).json()
        assert caps["fulltext"] is True
        assert caps["text_semantic"]["available"] is False
        assert caps["image_semantic"]["reason"]
        response = await server.client.get("/api/v1/search", params={"q": "harbour"})
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert set(body["skipped"]) == {"text", "image"}
        assert (
            await server.client.post("/api/v1/search/by-image", headers=admin)
        ).status_code == 501


async def test_semantic_channels_fuse_and_respect_filters(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        server.runtime.encoder = FakeEncoder()
        ids = await seed(server)
        caps = (await server.client.get("/api/v1/search/capabilities")).json()
        assert caps["image_semantic"]["vectors_compatible"] == 4
        assert caps["reembed_needed"] is False
        assets = (await server.client.get("/api/v1/search", params={"q": "harbour scene 1"})).json()
        assert assets["items"][0]["asset"]["id"] == str(ids["harbour"])
        assert assets["items"][0]["shot"] is None
        assert set(assets["items"][0]["ranks"]) == {"text", "image"}
        scenes = (
            await server.client.get(
                "/api/v1/search", params={"q": "harbour scene 1", "level": "shot"}
            )
        ).json()
        top = scenes["items"][0]
        assert top["shot"]["id"] == str(ids["harbour1"])
        assert top["shot"]["start_ms"] == 1000
        assert top["shot"]["keyframe_url"].endswith("kf1.jpg")
        filtered = (
            await server.client.get(
                "/api/v1/search", params={"q": "harbour scene 1", "media_type": "photo"}
            )
        ).json()
        assert filtered["items"] == []


async def test_vectors_of_another_model_are_excluded_and_reembed_queues_jobs(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        server.runtime.encoder = FakeEncoder(image_model=("siglip-fake", "r2"))
        ids = await seed(server)
        caps = (await server.client.get("/api/v1/search/capabilities")).json()
        assert caps["image_semantic"]["vectors_mismatched"] == 4
        assert caps["reembed_needed"] is True
        body = (await server.client.get("/api/v1/search", params={"q": "harbour scene"})).json()
        assert "image" in body["skipped"]
        queued = await server.client.post("/api/v1/admin/reembed", headers=admin)
        assert queued.json() == {"assets": 2}
        async with server.runtime.session_factory() as session:
            stages = sorted(j.stage for j in (await session.execute(select(Job))).scalars())
        assert stages == ["classify", "classify", "embed", "embed"]
        assert ids


async def test_find_similar_uses_stored_vectors(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        ids = await seed(server)  # no encoder needed
        similar = (await server.client.get(f"/api/v1/assets/{ids['harbour']}/similar")).json()
        assert [i["asset"]["id"] for i in similar["items"]] == [str(ids["street"])]
        shots = (await server.client.get(f"/api/v1/shots/{ids['harbour0']}/similar")).json()
        assert shots["level"] == "shot"
        assert str(ids["harbour0"]) not in [i["shot"]["id"] for i in shots["items"]]
        assert len(shots["items"]) == 3
        missing = await server.client.get(f"/api/v1/assets/{UUID(int=5)}/similar")
        assert missing.status_code == 404


async def test_transcript_hits_map_to_the_shot_at_that_time(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        ids = await seed(server)
        async with server.runtime.session_factory() as session:
            session.add(
                TranscriptSegment(
                    asset_id=ids["harbour"], idx=0, start_ms=1500, end_ms=1900, text="Leuchtturm"
                )
            )
            await session.commit()
        body = (
            await server.client.get("/api/v1/search", params={"q": "Leuchtturm", "level": "shot"})
        ).json()
        assert [i["shot"]["id"] for i in body["items"]] == [str(ids["harbour1"])]
        assert "transcript" in body["items"][0]["ranks"]
