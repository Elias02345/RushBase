from pathlib import PurePosixPath

import pytest

from rushbase_core import is_ignored


@pytest.mark.parametrize(
    "path",
    [
        "._clip.mov",
        "Input/batch/._clip.mov",
        ".DS_Store",
        "a/b/.ds_store",
        "Thumbs.db",
        "thumbs.DB",
        "desktop.ini",
        "clip.mp4.part",
        "x.TMP",
        "~$notes.docx",
        "@eaDir/clip.mp4/thumb.jpg",
        "Input/#recycle/old.mov",
        "share/.snapshot/daily/clip.mov",
        "Input\\batch\\._x",
    ],
)
def test_ignored(path: str) -> None:
    assert is_ignored(path)


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "clip.mp4",
        "Input/2026-10-12_Shoot/C0001.MP4",
        "PRIVATE/M4ROOT/CLIP/C0001M01.XML",
        "part.mp4",
        "tmp/clip.mov",
        "notes~$.txt",
        ".hidden",
        "my.snapshot.mov",
    ],
)
def test_not_ignored(path: str) -> None:
    assert not is_ignored(path)


def test_accepts_pure_paths() -> None:
    assert is_ignored(PurePosixPath("a/._b"))
    assert not is_ignored(PurePosixPath("a/b"))
