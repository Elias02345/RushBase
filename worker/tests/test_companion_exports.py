from pathlib import Path
from xml.etree import ElementTree

import pytest
from companion_data import ASSET_ID, asset_detail

from rushbase_worker.companion.clips import clip_from_asset
from rushbase_worker.companion.export_csv import DEFAULT_COLUMNS, export_csv
from rushbase_worker.companion.export_edl import export_edl, reel_name
from rushbase_worker.companion.export_fcpxml import export_fcpxml
from rushbase_worker.companion.timecode import FrameRate, format_timecode, parse_timecode

MEDIA = Path("/mnt/lib/Content/2026/A001_C002.mov")


def path_of(_clip: object) -> Path:
    return MEDIA


@pytest.mark.parametrize(
    ("fps", "frame_duration", "nominal"),
    [(23.976, "1001/24000s", 24), (24.0, "1/24s", 24), (25.0, "1/25s", 25),
     (29.97, "1001/30000s", 30), (30.0, "1/30s", 30), (59.94, "1001/60000s", 60)],
)  # fmt: skip
def test_frame_rates_are_exact(fps: float, frame_duration: str, nominal: int) -> None:
    rate = FrameRate.from_float(fps)
    assert rate.frame_duration == frame_duration
    assert rate.nominal == nominal


def test_rational_times() -> None:
    rate = FrameRate.from_float(29.97)
    assert rate.rational(0) == "0s"
    assert rate.rational(30) == "30030/30000s"
    assert rate.frames(1000) == 30


def test_timecode_roundtrip() -> None:
    rate = FrameRate.from_float(25.0)
    frames = parse_timecode("01:00:10:12", rate)
    assert frames == (3600 + 10) * 25 + 12
    assert format_timecode(frames or 0, rate) == "01:00:10:12"
    assert parse_timecode("00:00:01.500", rate) == 37
    assert parse_timecode("garbage", rate) is None


def test_csv_default_header_and_row() -> None:
    clip = clip_from_asset(asset_detail())
    text = export_csv([clip], path_of)
    lines = text.split("\r\n")
    assert lines[0] == ",".join(DEFAULT_COLUMNS)
    assert "A001_C002.mov" in lines[1]
    assert "Nachtaufnahme im Hafen" in lines[1]
    assert f"rushbase:{ASSET_ID}" in lines[1]


def test_csv_configurable_columns() -> None:
    clip = clip_from_asset(asset_detail())
    text = export_csv([clip], path_of, ("File Name", "Keywords"))
    assert text.split("\r\n")[:2] == ["File Name,Keywords", 'A001_C002.mov,"Hafen, Nacht"']


def test_edl_selected_shots_with_timecode() -> None:
    clip = clip_from_asset(asset_detail(), selected=[1])
    text, warnings = export_edl([clip])
    assert warnings == []
    event = [line for line in text.splitlines() if line.startswith("001")][0].split()
    assert event[1] == "A001C002"
    assert event[-4:] == ["01:00:04:00", "01:00:10:00", "01:00:00:00", "01:00:06:00"]
    assert "* FROM CLIP NAME: A001_C002.mov" in text


def test_edl_reel_names_are_short_and_unique() -> None:
    taken: set[str] = set()
    first = reel_name("Very Long Clip Name 0001.mov", taken)
    second = reel_name("Very Long Clip Name 0001.mov", taken)
    assert len(first) <= 8 and len(second) <= 8
    assert first != second


def test_edl_skips_photos() -> None:
    detail = asset_detail()
    detail["media_type"] = "photo"
    text, warnings = export_edl([clip_from_asset(detail)])
    assert "001" not in text
    assert "not a video" in warnings[0]


def test_fcpxml_structure_and_times_for_2997() -> None:
    clip = clip_from_asset(asset_detail(fps=29.97, timecode="00:00:00:00"), selected=[0, 1])
    text, warnings = export_fcpxml([clip], path_of)
    assert warnings == []
    root = ElementTree.fromstring(text.split("\n", 2)[2])
    assert root.tag == "fcpxml" and root.get("version") == "1.10"
    fmt = root.find("resources/format")
    assert fmt is not None and fmt.get("frameDuration") == "1001/30000s"
    assert fmt.get("name") == "FFVideoFormat1080p2997"
    asset = root.find("resources/asset")
    assert asset is not None
    assert asset.find("media-rep").get("src") == "file:///mnt/lib/Content/2026/A001_C002.mov"  # type: ignore[union-attr]
    assert asset.get("duration") == "300300/30000s"
    clips = root.findall(".//spine/asset-clip")
    assert [c.get("offset") for c in clips] == ["0s", "120120/30000s"]
    assert [c.get("start") for c in clips] == ["0s", "120120/30000s"]
    assert [c.get("duration") for c in clips] == ["120120/30000s", "180180/30000s"]
    sequence = root.find(".//sequence")
    assert sequence is not None and sequence.get("duration") == "300300/30000s"
    assert clips[0].find("keyword") is not None


def test_fcpxml_source_timecode_start() -> None:
    clip = clip_from_asset(asset_detail(fps=25.0, timecode="01:00:00:00"), selected=[1])
    text, _ = export_fcpxml([clip], path_of)
    root = ElementTree.fromstring(text.split("\n", 2)[2])
    asset = root.find("resources/asset")
    assert asset is not None and asset.get("start") == "90000/25s"
    assert root.find(".//asset-clip").get("start") == "90100/25s"  # type: ignore[union-attr]


def test_fcpxml_without_local_path_warns() -> None:
    text, warnings = export_fcpxml([clip_from_asset(asset_detail())], lambda _c: None)
    assert "no local path" in warnings[0]
    assert "<asset-clip" not in text
