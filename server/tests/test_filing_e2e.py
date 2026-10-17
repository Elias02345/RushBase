import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree

import pytest
from sqlalchemy import select, update

from pipeline import enroll, finalize_all, put_file, run_workers, scan
from rushbase_server.models import Asset, AssetFile, InputFile, Job, MoveJournal
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def all_assets(server: Server) -> list[Asset]:
    async with server.runtime.session_factory() as session:
        return list((await session.execute(select(Asset).order_by(Asset.id))).scalars())


async def files_of(server: Server) -> dict[str, AssetFile]:
    async with server.runtime.session_factory() as session:
        rows = (await session.execute(select(AssetFile))).scalars()
        return {f.original_rel_path: f for f in rows}


async def test_video_and_photo_are_filed_with_sidecars_and_journal(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        put_file(library_root, "Input/Harbour Day/clip.mov", b"video-bytes")
        put_file(library_root, "Input/Harbour Day/IMG_1.CR3", b"raw-bytes")
        put_file(library_root, "Input/Harbour Day/IMG_1.JPG", b"jpg-bytes")
        put_file(library_root, "Input/Harbour Day/IMG_1.xmp", b"<camera-xmp/>")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)

        assets = await all_assets(server)
        assert {a.status for a in assets} == {"filed"}
        files = await files_of(server)
        video = files["Input/Harbour Day/clip.mov"]
        video_dir = "Content/video/2026/2026-03-09_Harbour Day"
        assert video.rel_path == f"{video_dir}/clip.mov"
        assert (library_root / video.rel_path).read_bytes() == b"video-bytes"
        assert not (library_root / "Input/Harbour Day/clip.mov").exists()

        photo_dir = library_root / "Content/video/2026/2026-03-09_Harbour Day"
        assert photo_dir.is_dir()
        sidecar = json.loads((photo_dir / "_meta" / "clip.mov.json").read_text())
        assert sidecar["schema"] == "rushbase.sidecar/1"
        assert sidecar["files"][0]["original_rel_path"] == "Input/Harbour Day/clip.mov"
        assert sidecar["files"][0]["blake3"]
        assert sidecar["metadata"]["normalized"]["camera"]["model"] == "ILME-FX3"
        assert sidecar["shots"][0]["idx"] == 0
        assert sidecar["descriptive"]["tech_summary_en"].startswith("4K · HEVC · 25 fps")

        raw = files["Input/Harbour Day/IMG_1.CR3"]
        jpg = files["Input/Harbour Day/IMG_1.JPG"]
        assert raw.asset_id == jpg.asset_id
        folder = (library_root / raw.rel_path).parent
        assert {p.name for p in folder.glob("IMG_1*")} >= {
            "IMG_1.CR3",
            "IMG_1.JPG",
            "IMG_1.xmp",
            "IMG_1.rushbase.xmp",
        }
        assert (folder / "IMG_1.xmp").read_bytes() == b"<camera-xmp/>"  # camera XMP untouched
        xmp = (folder / "IMG_1.rushbase.xmp").read_text()
        ElementTree.fromstring(xmp.split("?>\n", 1)[1].rsplit("<?xpacket", 1)[0])  # noqa: S314
        assert "dc:description" in xmp

        journal = next((library_root / ".rushbase" / "journal").glob("*.jsonl"))
        lines = [json.loads(line) for line in journal.read_text().splitlines()]
        assert {line["reason"] for line in lines} == {"filed"}
        assert len(lines) == 4
        async with server.runtime.session_factory() as session:
            rows = (await session.execute(select(MoveJournal))).scalars().all()
            assert len(rows) == 4
            inputs = (await session.execute(select(InputFile))).scalars().all()
            assert all(i.asset_id is None for i in inputs)


async def test_existing_files_are_never_overwritten(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        target = "Content/video/2026/2026-03-09_shoot"
        (library_root / target).mkdir(parents=True)
        (library_root / target / "clip.mov").write_bytes(b"precious")
        put_file(library_root, "Input/shoot/clip.mov", b"new-clip")
        put_file(library_root, "Input/shoot/CLIP2.mov", b"other")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        files = await files_of(server)
        assert files["Input/shoot/clip.mov"].rel_path == f"{target}/clip_2.mov"
        assert (library_root / target / "clip.mov").read_bytes() == b"precious"
        assert (library_root / target / "clip_2.mov").read_bytes() == b"new-clip"


async def test_card_structure_moves_as_one_directory_after_the_last_member(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        base = "Input/trip/CARD_A/PRIVATE/M4ROOT"
        put_file(library_root, f"{base}/CLIP/C0001.MP4", b"one")
        put_file(library_root, f"{base}/CLIP/C0002.MP4", b"two")
        put_file(library_root, f"{base}/CLIP/C0001M01.XML", b"<xml/>")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        assert {a.status for a in await all_assets(server)} == {"filed"}
        files = await files_of(server)
        new_root = "Content/video/2026/2026-03-09_trip/CARD_A"
        assert {f.card_structure_root for f in files.values()} == {new_root}
        assert (library_root / new_root / "PRIVATE/M4ROOT/CLIP/C0001.MP4").read_bytes() == b"one"
        assert (library_root / new_root / "PRIVATE/M4ROOT/CLIP/C0001M01.XML").exists()
        assert not (library_root / "Input/trip/CARD_A").exists()
        assert (library_root / new_root / "_meta" / "C0001.MP4.json").is_file()
        journal = next((library_root / ".rushbase" / "journal").glob("*.jsonl"))
        kinds = [json.loads(line)["kind"] for line in journal.read_text().splitlines()]
        assert kinds == ["dir"]


async def test_card_waits_until_every_member_is_analysed(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        base = "Input/trip/CARD_B/PRIVATE/M4ROOT/CLIP"
        put_file(library_root, f"{base}/C0001.MP4", b"one")
        put_file(library_root, f"{base}/C0002.MP4", b"two")
        await scan(server)
        worker = await enroll(server, admin)
        first, second = (a.id for a in await all_assets(server))
        async with server.runtime.session_factory() as session:
            await session.execute(
                update(Job)
                .where(Job.asset_id == second)
                .values(not_before=server.clock.now() + timedelta(days=1))
            )
            await session.commit()
        await run_workers(server, worker, library_root)
        async with server.runtime.session_factory() as session:
            await session.execute(update(Job).where(Job.asset_id == second).values(not_before=None))
            await session.commit()
        await finalize_all(server)
        by_id = {str(a.id): a for a in await all_assets(server)}
        assert by_id[str(first)].status == "review"
        assert (library_root / base / "C0001.MP4").exists()
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        assert {a.status for a in await all_assets(server)} == {"filed"}
        assert not (library_root / "Input/trip/CARD_B").exists()


async def test_card_with_foreign_files_is_filed_file_by_file_keeping_structure(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        put_file(library_root, "Input/trip/CARD_C/PRIVATE/M4ROOT/CLIP/C0001.MP4", b"one")
        put_file(library_root, "Input/trip/CARD_C/MISC/readme.txt", b"keep me")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        files = await files_of(server)
        clip = files["Input/trip/CARD_C/PRIVATE/M4ROOT/CLIP/C0001.MP4"]
        assert clip.rel_path.endswith("/CARD_C/PRIVATE/M4ROOT/CLIP/C0001.MP4")
        assert (library_root / clip.rel_path).read_bytes() == b"one"
        readme = files["Input/trip/CARD_C/MISC/readme.txt"]
        assert readme.rel_path.startswith("Content/other/")
        assert {a.status for a in await all_assets(server)} == {"filed"}


async def test_existing_folder_spelling_is_adopted_case_insensitively(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        (library_root / "Content" / "VIDEO").mkdir(parents=True)
        put_file(library_root, "Input/shoot/clip.mov", b"clip")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        files = await files_of(server)
        assert files["Input/shoot/clip.mov"].rel_path.startswith("Content/VIDEO/2026/")
        assert [p.name for p in (library_root / "Content").iterdir()] == ["VIDEO"]


async def test_edits_refresh_the_rushbase_xmp_but_never_a_camera_xmp(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        put_file(library_root, "Input/Day/IMG_1.JPG", b"jpg-with-camera-xmp")
        put_file(library_root, "Input/Day/IMG_1.xmp", b"<camera-xmp/>")
        put_file(library_root, "Input/Day/IMG_2.JPG", b"jpg-alone")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        files = await files_of(server)
        with_camera = (library_root / files["Input/Day/IMG_1.JPG"].rel_path).parent
        alone = (library_root / files["Input/Day/IMG_2.JPG"].rel_path).parent
        assert "Plain title" not in (alone / "IMG_2.xmp").read_text()
        for name in ("IMG_1.JPG", "IMG_2.JPG"):
            asset_id = files[f"Input/Day/{name}"].asset_id
            response = await server.client.patch(
                f"/api/v1/assets/{asset_id}",
                json={"title": f"Title of {name}", "rating": 4},
                headers=admin,
            )
            assert response.status_code == 200, response.text
        assert "Title of IMG_2.JPG" in (alone / "IMG_2.xmp").read_text()
        assert "xmp:Rating>4<" in (alone / "IMG_2.xmp").read_text()
        assert "Title of IMG_1.JPG" in (with_camera / "IMG_1.rushbase.xmp").read_text()
        assert (with_camera / "IMG_1.xmp").read_bytes() == b"<camera-xmp/>"
        assert not list(alone.glob("*.tmp*")) and not list(with_camera.glob("*.tmp*"))
