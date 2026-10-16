from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ai_support import (
    audio_output,
    classify_output,
    description,
    embed_output,
    estimate,
    fake_job,
    seed_shots_asset,
    seed_taxonomy,
    tag,
    vision_output,
)
from rushbase_core.sidecar import SidecarV1
from rushbase_server.models import (
    Asset,
    AssetTag,
    AudioEvent,
    EmbeddingImage,
    EmbeddingText,
    Shot,
    TranscriptSegment,
)
from rushbase_server.services import ai_results, ai_sidecar
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def hits(server: Server, query: str) -> int:
    response = await server.client.get("/api/v1/assets", params={"q": query})
    assert response.status_code == 200, response.text
    return len(response.json()["items"])


async def test_ai_stages_store_and_make_assets_searchable(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        async with server.runtime.session_factory() as session:
            tax = await seed_taxonomy(session)
            asset = await seed_shots_asset(session)
            aid = asset.id
            await ai_results.apply_output(
                session, fake_job(aid, "audio"), audio_output(["Willkommen am Leuchtturm"])
            )
            await ai_results.apply_output(session, fake_job(aid, "embed"), embed_output([0, 1]))
            vision = vision_output(
                [
                    description(0, "Fischerboot"),
                    description(1, "Marktplatz", shot_size=estimate("close_up", 0.2)),
                ]
            )
            await ai_results.apply_output(session, fake_job(aid, "vision"), vision)
            classify = classify_output(
                [tag("harbour", 0)],
                proposals=[{"field": "location", "term": "pier", "confidence": 0.5}],
                review_reasons=["vlm_siglip_conflict"],
            )
            await ai_results.apply_output(session, fake_job(aid, "classify"), classify)
            await session.commit()
        async with server.runtime.session_factory() as session:
            row = await session.get_one(Asset, aid)
            assert row.summary_short == "Hafen kurz"
            assert row.review_state == "pending"
            assert "vlm_siglip_conflict" in row.review_reasons
            assert "shot1:low_confidence:shot_size" in row.review_reasons
            assert row.person_identifiable == "no"
            shots = (await session.execute(select(Shot).order_by(Shot.idx))).scalars().all()
            assert shots[0].description_short_de == "Fischerboot kurz"
            assert shots[1].shot_size == "close_up"
            for model, expected in (
                (TranscriptSegment, 1),
                (AudioEvent, 1),
                (EmbeddingImage, 2),
                (EmbeddingText, 0),
            ):
                assert await session.scalar(select(func.count()).select_from(model)) == expected
            tags = (await session.execute(select(AssetTag))).scalars().all()
            assert [(t.value_id, t.source) for t in tags] == [(tax["harbour"], "ai")]
        assert await hits(server, "Leuchtturm") == 1  # transcript
        assert await hits(server, "Hafen") == 1  # summary / tag label
        assert await hits(server, "harbour") == 1  # approved tag label
        assert await hits(server, "pier") == 0  # proposals are not searchable


async def test_reprocessing_replaces_ai_rows_but_keeps_human_ones(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        async with server.runtime.session_factory() as session:
            tax = await seed_taxonomy(session)
            asset = await seed_shots_asset(session, shots=1)
            asset.human_fields = ["summary_short"]
            asset.summary_short = "von Hand"
            human = AssetTag(
                asset_id=asset.id, field_id=tax["field"], value_id=tax["street"], source="human"
            )
            session.add(human)
            await session.flush()
            for value in ("harbour", "harbour", "street"):
                await ai_results.apply_output(
                    session, fake_job(asset.id, "classify"), classify_output([tag(value)])
                )
                await ai_results.apply_output(
                    session, fake_job(asset.id, "vision"), vision_output([description(0, "x")])
                )
            await session.commit()
            rows = (await session.execute(select(AssetTag.source, AssetTag.value_id))).all()
            # the human tag blocks every AI choice for the single-valued field
            assert [tuple(r) for r in rows] == [("human", tax["street"])]
            assert (await session.get_one(Asset, asset.id)).summary_short == "von Hand"


async def test_locked_assets_are_not_modified(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        async with server.runtime.session_factory() as session:
            asset = await seed_shots_asset(session, shots=1)
            asset.locked = True
            await ai_results.apply_output(
                session, fake_job(asset.id, "vision"), vision_output([description(0, "x")])
            )
            assert (await session.get_one(Asset, asset.id)).summary_short is None


async def test_unknown_shot_is_rejected(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        async with server.runtime.session_factory() as session:
            asset = await seed_shots_asset(session, shots=1)
            with pytest.raises(ai_results.AiResultError):
                await ai_results.apply_output(
                    session, fake_job(asset.id, "embed"), embed_output([5])
                )


async def test_sidecar_refresh_rewrites_in_place(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        async with server.runtime.session_factory() as session:
            tax = await seed_taxonomy(session)
            asset = await seed_shots_asset(session, shots=1)
            asset.content_rel_dir = "Content/2026/x"
            media = library_root / "Content/2026/x"
            media.mkdir(parents=True)
            await ai_results.apply_output(
                session, fake_job(asset.id, "vision"), vision_output([description(0, "Boot")])
            )
            await ai_results.apply_output(
                session, fake_job(asset.id, "classify"), classify_output([tag("harbour", 0)])
            )
            assert await ai_sidecar.refresh_for_runtime(server.runtime, session, asset.id)
            await session.commit()
        written = list((media / "_meta").glob("*.json"))
        assert len(written) == 1
        document = SidecarV1.model_validate_json(written[0].read_bytes())
        assert [(t.field, t.value, t.source) for t in document.tags] == [
            ("location", "harbour", "ai")
        ]
        assert document.ai is not None
        assert document.ai.summary_short_en == "Hafen short"
        assert document.ai.shot_descriptions[0]["short_de"] == "Boot kurz"
        assert tax
