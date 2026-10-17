from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from rushbase_core import MediaType
from rushbase_server.models import Asset, StageResult, TaxField, TaxValue
from support import Server, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_growth_throughput_and_heatmap(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        client = server.client
        factory = server.runtime.session_factory
        now = server.clock.now()
        video, batch = await seed_asset(factory, size=300)
        landscape, _ = await seed_asset(factory, batch, size=100)
        _audio, _ = await seed_asset(factory, batch, media_type=MediaType.AUDIO, size=50)
        async with factory() as session:
            await session.execute(
                update(Asset).where(Asset.id == video.id).values(orientation="landscape")
            )
            await session.execute(
                update(Asset)
                .where(Asset.id == landscape.id)
                .values(orientation="portrait", created_at=now - timedelta(days=70))
            )
            for age_hours, ok in ((1, True), (1, True), (50, True), (2, False), (24 * 9, True)):
                session.add(
                    StageResult(
                        asset_id=video.id,
                        stage="probe",
                        ok=ok,
                        finished_at=now - timedelta(hours=age_hours),
                    )
                )
            field = TaxField(key="mood", label_de="Stimmung", label_en="Mood")
            session.add(field)
            await session.flush()
            session.add_all(
                [
                    TaxValue(field_id=field.id, value="calm", status="approved"),
                    TaxValue(field_id=field.id, value="tense", status="approved"),
                ]
            )
            await session.commit()

        growth = (await client.get("/api/v1/stats/growth")).json()
        assert sum(m["bytes"] for m in growth) == 450
        assert len(growth) == 2
        recent = growth[-1]
        assert recent["by_media_type"]["audio"] == {"bytes": 50, "count": 1}

        flow = (await client.get("/api/v1/stats/throughput")).json()
        assert flow["total"] == 3  # failed and older than seven days results are not counted
        assert len(flow["hours"]) >= 7 * 24
        assert [h["count"] for h in flow["hours"] if h["count"]] == [1, 2]

        axes = {d["key"]: d["kind"] for d in (await client.get("/api/v1/stats/dimensions")).json()}
        assert axes["media_type"] == "technical"
        assert axes["mood"] == "taxonomy"
        assert "codec" not in axes

        heat = (
            await client.get(
                "/api/v1/stats/coverage", params={"rows": "media_type", "columns": "orientation"}
            )
        ).json()
        assert {(c["row"], c["col"], c["count"]) for c in heat["cells"]} == {
            ("video", "landscape", 1),
            ("video", "portrait", 1),
        }
        assert heat["max"] == 1
        assert [w["count"] for w in heat["weakest"]] == [0, 0, 1, 1]  # audio has no orientation

        tagged = (
            await client.get(
                "/api/v1/stats/coverage", params={"rows": "mood", "columns": "media_type"}
            )
        ).json()
        assert tagged["row_values"] == ["calm", "tense"]  # approved values show up even unused
        assert [w["count"] for w in tagged["weakest"]] == [0, 0, 0, 0]

        bad = await client.get("/api/v1/stats/coverage", params={"rows": "nope", "columns": "mood"})
        assert bad.status_code == 422
