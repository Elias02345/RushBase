import errno
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

import pytest

from rushbase_core.fsops import FileInUseError, append_line, atomic_write_bytes, safe_rename
from rushbase_core.results import ProbeOutput
from rushbase_core.sidecar import SidecarFile, SidecarV1, render_xmp
from rushbase_core.summary import technical_summary

PROBE = ProbeOutput.model_validate(
    {
        "camera": {"make": "Sony", "model": "ILME-FX3"},
        "lens": {"focal_length_mm": 85},
        "exposure": {"log_profile": "S-Log3", "iso": 800},
        "video": {"codec": "hevc", "width": 3840, "height": 2160, "fps": 25.0},
        "duration_ms": 83_000,
    }
)


def test_technical_summary_de_en() -> None:
    expected = "4K · HEVC · 25 fps · Sony ILME-FX3 · S-Log3 · 85 mm"
    assert technical_summary(PROBE, "video", "en") == expected
    assert technical_summary(PROBE, "video", "de") == expected
    assert technical_summary(PROBE, "video", "en", duration=True).endswith(" · 1:23")


def test_summary_handles_slow_motion_photos_audio_and_missing_data() -> None:
    slow = ProbeOutput.model_validate(
        {"video": {"codec": "prores", "width": 1920, "height": 1080, "fps": 25, "capture_fps": 100}}
    )
    assert (
        technical_summary(slow, "video", "en") == "Full HD · ProRes · 25 fps (captured at 100 fps)"
    )
    assert "aufgenommen mit 100 fps" in technical_summary(slow, "video", "de")
    photo = ProbeOutput.model_validate(
        {
            "camera": {"make": "Canon", "model": "Canon EOS R5"},
            "exposure": {"iso": 100},
            "orientation": "portrait",
            "video": {"width": 4000, "height": 6000},
        }
    )
    assert technical_summary(photo, "photo", "de") == "4K · Canon EOS R5 · ISO 100 · Hochformat"
    audio = ProbeOutput.model_validate(
        {"audio": {"codec": "pcm_s24le", "channels": 2, "sample_rate": 48000}}
    )
    assert technical_summary(audio, "audio", "en") == "48 kHz · 2 ch · PCM_S24LE"
    assert technical_summary(None, "video", "en") == ""
    assert technical_summary(ProbeOutput(), "other", "en") == ""


def test_xmp_is_well_formed_and_escapes_text() -> None:
    data = render_xmp(
        title='Harbour <1> & "boats"',
        description_en="4K HEVC",
        description_de="4K HEVC (de)",
        keywords=["a&b", "c"],
        rating=4,
    )
    root = ElementTree.fromstring(  # noqa: S314 - our own output
        data.split(b"?>\n", 1)[1].rsplit(b"<?xpacket", 1)[0]
    )
    text = data.decode()
    assert root.tag.endswith("xmpmeta")
    assert "Harbour &lt;1&gt; &amp;" in text
    assert "<xmp:Rating>4</xmp:Rating>" in text
    assert "a&amp;b" in text
    minimal = render_xmp(
        title=None, description_en=None, description_de=None, keywords=[], rating=None
    )
    assert b"dc:title" not in minimal
    assert b"Rating" not in minimal


def test_sidecar_round_trip() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    sidecar = SidecarV1(
        asset_id=uuid4(),
        media_type="video",
        status="filed",
        content_rel_dir="Content/video/2026",
        primary_rel_path="Content/video/2026/a.mov",
        files=[
            SidecarFile(
                role="primary",
                rel_path="Content/video/2026/a.mov",
                original_rel_path="Input/x/a.mov",
                size=3,
            )
        ],
        created_at=now,
        written_at=now,
    )
    data = sidecar.to_json()
    assert b'"schema": "rushbase.sidecar/1"' in data
    assert SidecarV1.model_validate_json(data) == sidecar


def test_safe_rename_never_overwrites_and_creates_parents(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    with pytest.raises(FileExistsError):
        safe_rename(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b").read_text() == "b"
    safe_rename(tmp_path / "a", tmp_path / "deep" / "er" / "a")
    assert (tmp_path / "deep" / "er" / "a").read_text() == "a"
    (tmp_path / "dir").mkdir()
    with pytest.raises(FileExistsError):
        safe_rename(tmp_path / "deep", tmp_path / "dir")


def test_safe_rename_maps_permission_errors_to_file_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").write_text("a")

    def deny(*_: object) -> None:
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr("rushbase_core.fsops.os.rename", deny)
    with pytest.raises(FileInUseError):
        safe_rename(tmp_path / "a", tmp_path / "b")


def test_atomic_write_replaces_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "x.json"
    atomic_write_bytes(target, b"one")
    atomic_write_bytes(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_append_line_is_durable_and_newline_terminated(tmp_path: Path) -> None:
    log = tmp_path / "j" / "2026-01.jsonl"
    append_line(log, "one")
    append_line(log, "two\n")
    assert log.read_text() == "one\ntwo\n"
