import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from rushbase_core import MediaType
from rushbase_core.results import DecodeStatus, Orientation, ProbeOutput
from rushbase_worker.probe import convert as c
from rushbase_worker.probe.decode import decide_decode_status, match_camera_proxy
from rushbase_worker.probe.mapping import Rule, TagIndex, apply_rules
from rushbase_worker.probe.normalize import build_probe_output, flatten_ffprobe
from rushbase_worker.probe.sidecar import flatten_sidecar

FIXTURES = Path(__file__).parent / "fixtures"
DECODERS = frozenset({"h264", "aac", "prores"})


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def clip_output(**overrides: Any) -> ProbeOutput:
    sidecar = flatten_sidecar((FIXTURES / "sidecar_nrt.xml").read_bytes())
    args: dict[str, Any] = {
        "media_type": MediaType.VIDEO,
        "primary_rel_path": "Input/b/PRIVATE/M4ROOT/CLIP/C0001.MP4",
        "ffprobe": load("ffprobe_clip.json"),
        "exiftool": load("exiftool_clip.json"),
        "sidecars": {"C0001M01.XML": sidecar},
        "decoders": DECODERS,
        "camera_proxy": None,
    }
    return build_probe_output(**{**args, **overrides})


def test_sidecar_flattening() -> None:
    flat = flatten_sidecar((FIXTURES / "sidecar_nrt.xml").read_bytes())
    assert flat["sidecar:Device.modelName"] == "EX-CAM1"
    assert flat["sidecar:item.CaptureGammaEquation"] == "s-log3-cine"
    assert flat["sidecar:table.ShutterSpeed_Time"] == "1/50"
    assert flat["sidecar:LtcChangeTable.LtcChange.value"] == "09120000"


def test_sidecar_rejects_entities() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'
    with pytest.raises(ValueError, match="invalid sidecar"):
        flatten_sidecar(bomb)


def test_clip_normalisation() -> None:
    out = clip_output()
    assert out.container == "mp4"
    assert out.duration_ms == 2000
    assert out.capture_at is not None
    assert out.capture_at.utcoffset() == timedelta(hours=2)
    assert out.capture_tz == "+02:00"
    assert out.camera.make == "Example Camera Co"
    assert out.camera.model == "EX-CAM1"
    assert out.lens.model == "EX 24-70mm F2.8"
    assert out.exposure.iso == 800
    assert out.exposure.shutter_s == pytest.approx(0.02)
    assert out.exposure.log_profile == "slog3"
    assert out.video is not None
    assert (out.video.codec, out.video.width, out.video.height) == ("h264", 1920, 1080)
    assert out.video.fps == 25.0
    assert out.video.capture_fps is None  # same as playback
    assert out.video.timecode == "09:12:00:00"
    assert out.orientation is Orientation.LANDSCAPE
    assert out.audio is not None
    assert out.audio.codec == "aac"
    assert out.decode_status is DecodeStatus.OK
    assert set(out.raw) >= {"ffprobe", "exiftool", "sidecar_xml"}


def test_without_sidecar_uses_file_tags_only() -> None:
    out = clip_output(sidecars={})
    assert out.camera.model is None
    assert out.capture_at is None  # the file's own creation time is unset (all zero)
    assert out.video is not None


def test_off_speed_and_rotation() -> None:
    ffprobe = load("ffprobe_clip.json")
    ffprobe["streams"][0]["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": 90}]
    xml = (
        (FIXTURES / "sidecar_nrt.xml").read_text().replace('captureFps="25p"', 'captureFps="100p"')
    )
    out = clip_output(ffprobe=ffprobe, sidecars={"x.xml": flatten_sidecar(xml.encode())})
    assert out.video is not None
    assert out.video.capture_fps == 100.0
    assert out.video.rotation == 270
    assert out.orientation is Orientation.PORTRAIT


def test_failed_ffprobe_gives_metadata_only() -> None:
    out = clip_output(ffprobe=None, sidecars={})
    assert out.decode_status is DecodeStatus.METADATA_ONLY
    assert out.video is not None
    assert out.video.width == 1920


def test_photo_exif_orientation_and_no_duration() -> None:
    exif = {
        "File:FileTypeExtension": "JPG",
        "ExifIFD:DateTimeOriginal": "2026:10:12 10:05:00",
        "ExifIFD:OffsetTimeOriginal": "+01:00",
        "ExifIFD:ExifImageWidth": 4000,
        "ExifIFD:ExifImageHeight": 3000,
        "IFD0:Orientation": 6,
        "ExifIFD:ExposureTime": "1 250",
        "ExifIFD:Flash": 1,
    }
    out = build_probe_output(
        media_type=MediaType.PHOTO,
        primary_rel_path="a/IMG_1.jpg",
        ffprobe=None,
        exiftool=exif,
        sidecars={},
        decoders=DECODERS,
        camera_proxy=None,
    )
    assert out.duration_ms is None
    assert out.orientation is Orientation.PORTRAIT
    assert out.capture_tz == "+01:00"
    assert out.exposure.shutter_s == pytest.approx(0.004)
    assert out.photo is not None
    assert out.photo.flash == "fired"


def test_mapping_first_convertible_candidate_wins() -> None:
    index = TagIndex({"A:Tag": "x", "B:Num": "7"}, {"sidecar:k": "bad"})
    rules = (
        Rule("a.n", ("sidecar:k", "Num"), c.to_int),
        Rule("a.n", ("Tag",)),
        Rule("a.s", ("Tag",)),
    )
    assert apply_rules(rules, index) == {"a": {"n": 7, "s": "x"}}


def test_flatten_ffprobe_rotation_sign() -> None:
    flat = flatten_ffprobe(
        {"streams": [{"codec_type": "video", "side_data_list": [{"rotation": -90}]}]}
    )
    assert flat["ffprobe:video.rotation_cw"] == 90


@pytest.mark.parametrize(
    ("func", "value", "expected"),
    [
        (c.rational, "1/50", 0.02),
        (c.rational, "1 250", 0.004),
        (c.rational, 0.5, 0.5),
        (c.rational, "1 0", None),
        (c.frame_rate, "25p", 25.0),
        (c.frame_rate, "30000/1001", pytest.approx(29.97, abs=0.001)),
        (c.frame_rate, "0/0", None),
        (c.timecode, "09120000", "09:12:00:00"),
        (c.timecode, "garbage", None),
        (c.log_profile, "s-log3-cine", "slog3"),
        (c.log_profile, "bt709", None),
        (c.bit_depth_from_pix_fmt, "yuv422p10le", 10),
        (c.chroma_from_pix_fmt, "yuv420p", "4:2:0"),
        (c.hdr_from_transfer, "smpte2084", "pq"),
        (c.iso6709_part(1), "+37.77-122.41+010.0/", -122.41),
    ],
)
def test_converters(func: c.Converter, value: Any, expected: Any) -> None:
    assert func(value) == expected


def test_datetime_parsing() -> None:
    assert c.parse_datetime("0000:00:00 00:00:00") is None
    moment = c.parse_datetime("2026-10-13T07:12:00Z")
    assert moment is not None
    assert c.format_offset(moment) == "UTC"
    assert c.parse_datetime("2026:10:13 09:12:00") is not None


def test_decode_status_rules() -> None:
    braw = decide_decode_status(
        extension="braw", ffprobe_ok=False, video_codec=None, decoders=DECODERS, has_proxy=False
    )
    assert braw.status is DecodeStatus.DECODER_MISSING
    with_proxy = decide_decode_status(
        extension="r3d", ffprobe_ok=False, video_codec=None, decoders=DECODERS, has_proxy=True
    )
    assert with_proxy.status is DecodeStatus.PROXY_ONLY
    raw = decide_decode_status(
        extension="mov",
        ffprobe_ok=True,
        video_codec="prores_raw",
        decoders=DECODERS,
        has_proxy=False,
    )
    assert raw.status is DecodeStatus.DECODER_MISSING
    capable = decide_decode_status(
        extension="mov",
        ffprobe_ok=True,
        video_codec="prores_raw",
        decoders=DECODERS | {"prores_raw"},
        has_proxy=False,
    )
    assert capable.status is DecodeStatus.OK


def test_camera_proxy_matching() -> None:
    proxies = ["a/C0002S03.MP4", "a/C0001.MP4", "a/C0001S03.MP4"]
    assert match_camera_proxy("a/C0001.MXF", proxies) == "a/C0001.MP4"
    assert match_camera_proxy("a/C0009.MXF", proxies) is None
