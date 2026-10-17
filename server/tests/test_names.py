"""Hostile file names and links on the share (share-write attacker)."""

from pathlib import Path

import pytest

from rushbase_server.services import rebuild, scanner


@pytest.mark.parametrize("name", ["a\\b.mp4", "..\\..\\x.mp4", "evil\nname.mp4", "tab\there.mp4"])
def test_walk_input_skips_names_with_backslash_or_control_characters(
    tmp_path: Path, name: str
) -> None:
    folder = tmp_path / "Input" / "Batch"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"x")
    (folder / "good.mp4").write_bytes(b"x")
    entries, complete = scanner.walk_input(tmp_path)
    assert [e.rel_path for e in entries] == ["Input/Batch/good.mp4"]
    assert complete


def test_rebuild_ignores_symlinked_sidecars(tmp_path: Path) -> None:
    meta = tmp_path / "Content" / "video" / "_meta"
    meta.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (meta / "linked.json").symlink_to(outside)
    (meta / "real.json").write_text("{}")
    assert [p.name for p in rebuild.discover(tmp_path)] == ["real.json"]
