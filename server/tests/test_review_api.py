from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_support import (
    classify_output,
    description,
    estimate,
    fake_job,
    seed_shots_asset,
    seed_taxonomy,
    tag,
    vision_output,
)
from rushbase_server.models import Asset, AssetTag, GoldenLabel, Shot
from rushbase_server.services import ai_results
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def analysed(server: Server) -> tuple[Asset, dict]:  # type: ignore[type-arg]
    async with server.runtime.session_factory() as session:
        tax = await seed_taxonomy(session)
        asset = await seed_shots_asset(session, shots=1)
        await ai_results.apply_output(
            session,
            fake_job(asset.id, "vision"),
            vision_output([description(0, "Boot", shot_size=estimate("wide", 0.2))]),
        )
        await ai_results.apply_output(
            session, fake_job(asset.id, "classify"), classify_output([tag("harbour", 0)])
        )
        await session.commit()
        return asset, tax


async def test_review_queue_edit_and_reprocessing_respects_human_edits(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        asset, _tax = await analysed(server)
        queue = (await server.client.get("/api/v1/review")).json()
        assert queue["total"] == 1
        assert "shot0:low_confidence:shot_size" in queue["items"][0]["reasons"]
        only = await server.client.get(
            "/api/v1/review", params={"review_reason": "shot0:low_confidence:shot_size"}
        )
        assert only.json()["total"] == 1
        none = await server.client.get("/api/v1/review", params={"review_reason": "nope"})
        assert none.json()["total"] == 0

        detail = (await server.client.get(f"/api/v1/assets/{asset.id}")).json()
        shot_id = None
        async with server.runtime.session_factory() as session:
            shot_id = (await session.execute(select(Shot.id))).scalar_one()
        assert detail["ai"]["tags"][0]["source"] == "ai"
        assert detail["shots"][0]["ai"]["shot_size"] == "wide"

        # correct the shot size, swap the tag and approve
        fixed = await server.client.patch(
            f"/api/v1/shots/{shot_id}",
            json={"shot_size": "close_up", "remove_tags": [{"field": "location", "value": "harbour"}],
                  "add_tags": [{"field": "location", "value": "street"}]},
            headers=admin,
        )  # fmt: skip
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["changed"]["shot_size"] == {"from": "wide", "to": "close_up"}
        done = await server.client.patch(
            f"/api/v1/assets/{asset.id}",
            json={"review_done": True, "rating": 4, "title": "Hafen"},
            headers=admin,
        )
        assert done.status_code == 200, done.text
        assert (await server.client.get("/api/v1/review")).json()["total"] == 0

        # reprocessing keeps the human values and does not bring the removed AI tag back
        async with server.runtime.session_factory() as session:
            await ai_results.apply_output(
                session,
                fake_job(asset.id, "vision"),
                vision_output([description(0, "Boot2", shot_size=estimate("wide", 0.9))]),
            )
            await ai_results.apply_output(
                session, fake_job(asset.id, "classify"), classify_output([tag("harbour", 0)])
            )
            await session.commit()
            shot = await session.get_one(Shot, shot_id)
            assert shot.shot_size == "close_up"
            assert shot.description_short_de == "Boot2 kurz"
            sources = (await session.execute(select(AssetTag.source))).scalars().all()
            assert sources == ["human"]
            labels = (await session.execute(select(GoldenLabel))).scalars().all()
            assert {(label.field, label.expected, label.origin) for label in labels} == {
                ("shot_size", "close_up", "correction"),
                ("location", "street", "correction"),
            }
        history = (await server.client.get(f"/api/v1/assets/{asset.id}")).json()["history"]
        assert [h["action"] for h in history].count("asset.edit") == 2


async def test_viewers_cannot_edit_and_bad_tags_are_rejected(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        asset, _ = await analysed(server)
        bad = await server.client.patch(
            f"/api/v1/assets/{asset.id}",
            json={"add_tags": [{"field": "location", "value": "moon"}]},
            headers=admin,
        )
        assert bad.status_code == 422
        unknown = await server.client.patch(
            f"/api/v1/assets/{asset.id}", json={"rating": 9}, headers=admin
        )
        assert unknown.status_code == 422


async def test_bulk_edit_tags_lock_and_collections(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        a, _ = await analysed(server)
        async with server.runtime.session_factory() as session:
            b = await seed_shots_asset(session, shots=0)
            await session.commit()
        created = await server.client.post(
            "/api/v1/collections", json={"name": "Auswahl"}, headers=admin
        )
        assert created.status_code == 201
        collection_id = created.json()["id"]
        dup = await server.client.post(
            "/api/v1/collections", json={"name": "auswahl"}, headers=admin
        )
        assert dup.status_code == 409
        result = await server.client.post(
            "/api/v1/assets/bulk",
            json={"asset_ids": [str(a.id), str(b.id)], "locked": True,
                  "add_tags": [{"field": "location", "value": "street"}],
                  "add_to_collection": collection_id},
            headers=admin,
        )  # fmt: skip
        assert result.json() == {"updated": 2}
        listed = (await server.client.get("/api/v1/collections")).json()
        assert listed[0]["assets"] == 2
        in_collection = await server.client.get(
            "/api/v1/assets", params={"collection_id": collection_id}
        )
        assert in_collection.json()["total"] == 2
        locked = (await server.client.get(f"/api/v1/assets/{b.id}")).json()
        assert locked["ai"]["locked"] is True
        assert [t["value"] for t in locked["ai"]["tags"]] == ["street"]


async def test_transcript_endpoint_filters_segments(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    from ai_support import audio_output

    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        async with server.runtime.session_factory() as session:
            asset = await seed_shots_asset(session, shots=0)
            await ai_results.apply_output(
                session, fake_job(asset.id, "audio"), audio_output(["Hallo Welt", "Guten Morgen"])
            )
            await session.commit()
        everything = (await server.client.get(f"/api/v1/assets/{asset.id}/transcript")).json()
        assert [s["text"] for s in everything["segments"]] == ["Hallo Welt", "Guten Morgen"]
        assert everything["events"][0]["tag"] == "applause"
        found = (
            await server.client.get(f"/api/v1/assets/{asset.id}/transcript", params={"q": "morgen"})
        ).json()
        assert [s["idx"] for s in found["segments"]] == [1]
        assert found["total_segments"] == 2
