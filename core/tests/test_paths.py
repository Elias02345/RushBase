from pathlib import Path

import pytest

from rushbase_core import (
    PathOutsideLibraryError,
    PlatformRoots,
    relative_to_root,
    resolve_in_root,
)


def test_relative_to_root_uses_forward_slashes(tmp_path: Path) -> None:
    assert relative_to_root(tmp_path, tmp_path / "Content" / "a" / "b.mov") == "Content/a/b.mov"


def test_relative_to_root_of_root_is_empty(tmp_path: Path) -> None:
    assert relative_to_root(tmp_path, tmp_path) == ""


def test_relative_to_root_rejects_foreign_path(tmp_path: Path) -> None:
    with pytest.raises(PathOutsideLibraryError):
        relative_to_root(tmp_path / "lib", tmp_path / "other" / "x")


def test_resolve_in_root_round_trip(tmp_path: Path) -> None:
    rel = "Content/2026/clip.mov"
    assert relative_to_root(tmp_path, resolve_in_root(tmp_path, rel)) == rel


def test_resolve_in_root_normalises_dots(tmp_path: Path) -> None:
    assert resolve_in_root(tmp_path, "Content/a/../b.mov") == tmp_path / "Content" / "b.mov"


@pytest.mark.parametrize("bad", ["Content\\a\\b.mov", "a/b\\..\\..\\x", "a/b\x00c"])
def test_resolve_in_root_rejects_backslashes_and_nul(tmp_path: Path, bad: str) -> None:
    """Stored paths use ``/`` only: a ``\\`` is part of a POSIX file name, never a separator."""
    with pytest.raises(PathOutsideLibraryError):
        resolve_in_root(tmp_path, bad)


@pytest.mark.parametrize("bad", ["/etc/passwd", "\\\\nas\\share", "C:\\x", "../x", "a/../../x"])
def test_resolve_in_root_rejects_escapes(tmp_path: Path, bad: str) -> None:
    with pytest.raises(PathOutsideLibraryError):
        resolve_in_root(tmp_path, bad)


def test_platform_roots_selects_per_os() -> None:
    roots = PlatformRoots(windows="M:\\", linux="/mnt/media", macos="/Volumes/media")
    assert roots.for_platform("win32") == Path("M:\\")
    assert roots.for_platform("linux") == Path("/mnt/media")
    assert roots.for_platform("darwin") == Path("/Volumes/media")


def test_platform_roots_unset_is_none() -> None:
    assert PlatformRoots(linux="/mnt/media").for_platform("darwin") is None
