import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from rushbase_core import Stage
from rushbase_server.db import SessionFactory
from rushbase_server.models import Asset, AssetFile, Batch, InputFile, Job
from rushbase_server.services import queue, scanner

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SCAN = scanner.ScanConfig(stable_scans=2, stable_seconds=120)
STAGES = [Stage.PROBE, Stage.HASH, Stage.PREVIEW, Stage.SHOTS, Stage.FINALIZE]


def put(root: Path, rel: str, size: int = 100) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (T0.timestamp() - 3600, T0.timestamp() - 3600))


async def scan(factory: SessionFactory, root: Path, now: datetime) -> scanner.ScanReport:
    async with factory() as session:
        report = await scanner.scan_once(session, root, now, SCAN, queue.QueueConfig(), STAGES)
        await session.commit()
    return report


async def states(factory: SessionFactory) -> dict[str, str]:
    async with factory() as session:
        rows = await session.execute(select(InputFile.rel_path, InputFile.state))
        return dict(rows.all())


async def test_file_becomes_stable_after_two_scans_and_the_time_window(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/2026-10-12_Dreh/clip.mp4")
    first = await scan(session_factory, library_root, T0)
    assert first.new == 1
    assert await states(session_factory) == {"Input/2026-10-12_Dreh/clip.mp4": "copying"}
    await scan(session_factory, library_root, T0 + timedelta(seconds=60))  # 2 scans, only 60 s
    assert set((await states(session_factory)).values()) == {"copying"}
    report = await scan(session_factory, library_root, T0 + timedelta(seconds=121))
    assert report.became_stable == 1
    assert report.assets_created == 1
    async with session_factory() as session:
        assert set((await session.execute(select(InputFile.state))).scalars()) == {"grouped"}
        assert await session.scalar(select(func.count()).select_from(Asset)) == 1


async def test_growing_file_stays_unstable_and_resets_the_window(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/b/clip.mp4", 100)
    await scan(session_factory, library_root, T0)
    put(library_root, "Input/b/clip.mp4", 200)  # still being copied
    report = await scan(session_factory, library_root, T0 + timedelta(seconds=100))
    assert report.changed == 1
    await scan(session_factory, library_root, T0 + timedelta(seconds=150))
    assert (await states(session_factory))["Input/b/clip.mp4"] == "copying"  # 50 s since change
    await scan(session_factory, library_root, T0 + timedelta(seconds=230))
    assert (await states(session_factory))["Input/b/clip.mp4"] == "grouped"


async def ready(factory: SessionFactory, root: Path) -> None:
    for step in (0, 60, 130):
        await scan(factory, root, T0 + timedelta(seconds=step))


async def test_ignored_files_are_never_recorded(
    session_factory: SessionFactory, library_root: Path
) -> None:
    for rel in (
        "Input/b/._clip.mp4",
        "Input/b/.DS_Store",
        "Input/b/@eaDir/x.jpg",
        "Input/b/a.part",
    ):
        put(library_root, rel)
    put(library_root, "Input/b/real.jpg")
    await ready(session_factory, library_root)
    assert list(await states(session_factory)) == ["Input/b/real.jpg"]


async def test_batches_hint_dates_and_loose_files(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/2026-10-12_Drehtag_Standort-A/a.jpg")
    put(library_root, "Input/Urlaub/b.jpg")
    put(library_root, "Input/loose.jpg")
    await ready(session_factory, library_root)
    async with session_factory() as session:
        batches = {b.input_path: b for b in (await session.execute(select(Batch))).scalars()}
    assert set(batches) == {
        "Input/2026-10-12_Drehtag_Standort-A",
        "Input/Urlaub",
        "Input/@auto/2026-10-12",
    }
    dated = batches["Input/2026-10-12_Drehtag_Standort-A"]
    assert dated.label == "Drehtag_Standort-A"
    assert str(dated.hint_date) == "2026-10-12"
    assert batches["Input/Urlaub"].hint_date is None
    assert batches["Input/@auto/2026-10-12"].label == "Auto 2026-10-12"


async def test_grouping_creates_one_asset_with_roles_and_a_job_dag(
    session_factory: SessionFactory, library_root: Path
) -> None:
    for name in ("IMG_1.CR3", "IMG_1.JPG", "IMG_1.xmp"):
        put(library_root, f"Input/b/{name}")
    await ready(session_factory, library_root)
    async with session_factory() as session:
        asset = (await session.execute(select(Asset))).scalar_one()
        files = {f.rel_path: f for f in (await session.execute(select(AssetFile))).scalars()}
        jobs = {j.stage: j for j in (await session.execute(select(Job))).scalars()}
    assert asset.media_type == "photo"
    assert asset.status == "processing"
    assert asset.size_total == 300
    assert {f.rel_path.rsplit("/", 1)[1]: f.role for f in files.values()} == {
        "IMG_1.CR3": "raw",
        "IMG_1.JPG": "jpeg",
        "IMG_1.xmp": "sidecar",
    }
    assert all(f.quick_fp and f.original_rel_path == f.rel_path for f in files.values())
    assert asset.primary_file_id == files["Input/b/IMG_1.CR3"].id
    # photo: no shots; probe/hash start queued, preview and finalize are blocked
    assert set(jobs) == {"probe", "hash", "preview", "finalize"}
    assert {s: j.state for s, j in jobs.items()} == {
        "probe": "queued",
        "hash": "queued",
        "preview": "blocked",
        "finalize": "blocked",
    }
    assert jobs["preview"].depends_on == [jobs["probe"].id]


async def test_card_structure_waits_until_the_whole_card_is_quiet(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/b/A001/PRIVATE/M4ROOT/CLIP/C0001.MP4")
    put(library_root, "Input/b/A001/PRIVATE/M4ROOT/CLIP/C0002.MP4")
    await scan(session_factory, library_root, T0)
    await scan(session_factory, library_root, T0 + timedelta(seconds=60))
    put(library_root, "Input/b/A001/PRIVATE/M4ROOT/CLIP/C0003.MP4")  # still copying the card
    await scan(session_factory, library_root, T0 + timedelta(seconds=130))
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Asset)) == 0
    await scan(session_factory, library_root, T0 + timedelta(seconds=190))
    await scan(session_factory, library_root, T0 + timedelta(seconds=260))
    async with session_factory() as session:
        assets = (await session.execute(select(Asset))).scalars().all()
        roots = set((await session.execute(select(AssetFile.card_structure_root))).scalars())
    assert len(assets) == 3
    assert roots == {"Input/b/A001"}


async def test_vanished_files_are_marked_gone_but_unreadable_input_is_not_trusted(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/b/a.jpg")
    put(library_root, "Input/b/b.jpg")
    await ready(session_factory, library_root)
    (library_root / "Input/b/a.jpg").unlink()
    report = await scan(session_factory, library_root, T0 + timedelta(seconds=200))
    assert report.gone == 1
    assert (await states(session_factory))["Input/b/a.jpg"] == "gone"
    # Input/ missing entirely (unmounted share): abort, change nothing
    (library_root / "Input/b/b.jpg").unlink()
    (library_root / "Input/b").rmdir()
    (library_root / "Input").rmdir()
    async with session_factory() as session:
        with pytest.raises(scanner.InputUnavailableError):
            await scanner.scan_once(
                session,
                library_root,
                T0 + timedelta(seconds=300),
                SCAN,
                queue.QueueConfig(),
                STAGES,
            )
    assert (await states(session_factory))["Input/b/b.jpg"] == "grouped"


async def test_late_sidecar_attaches_to_the_existing_asset(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/b/clip.mov")
    await ready(session_factory, library_root)
    put(library_root, "Input/b/clip.xml")
    for step in (200, 260, 330):
        await scan(session_factory, library_root, T0 + timedelta(seconds=step))
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Asset)) == 1
        roles = set((await session.execute(select(AssetFile.role))).scalars())
        assert (
            await session.scalar(select(func.count()).select_from(Job).where(Job.stage == "probe"))
            == 1
        )
    assert roles == {"primary", "sidecar"}


async def test_batch_status_follows_files_and_jobs(
    session_factory: SessionFactory, library_root: Path
) -> None:
    put(library_root, "Input/b/a.jpg")
    await scan(session_factory, library_root, T0)
    async with session_factory() as session:
        assert (await session.scalar(select(Batch.status))) == "scanning"
    await ready(session_factory, library_root)
    async with session_factory() as session:
        assert (await session.scalar(select(Batch.status))) == "processing"
