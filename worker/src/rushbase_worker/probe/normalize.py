"""Turn raw ffprobe/ExifTool/sidecar output into a :class:`ProbeOutput`."""

from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from rushbase_core import MediaType
from rushbase_core.results import Orientation, ProbeOutput
from rushbase_worker.probe import convert as c
from rushbase_worker.probe.decode import decide_decode_status
from rushbase_worker.probe.mapping import RULES, TagIndex, apply_rules

CAPTURE_TIME_KEYS = (
    "sidecar:CreationDate.value",
    "ExifIFD:DateTimeOriginal",
    "XMP-exif:DateTimeOriginal",
    "QuickTime:CreationDate",
    "Keys:CreationDate",
    "RIFF:DateTimeOriginal",
    "BWF:DateTimeOriginal",
    "XMP-photoshop:DateCreated",
    "ffprobe:format.tags.creation_time",
    "ffprobe:video.tags.creation_time",
    "QuickTime:CreateDate",
    "IFD0:ModifyDate",
)
"""Capture time candidates in priority order (original capture before file/encode times)."""
OFFSET_KEYS = ("ExifIFD:OffsetTimeOriginal", "ExifIFD:OffsetTime", "ExifIFD:OffsetTimeDigitized")
EXIF_ORIENTATION_SWAPS = frozenset({5, 6, 7, 8})
SPEED_UNITS_MPS = {"K": 1000 / 3600, "M": 1609.344 / 3600, "N": 1852 / 3600}


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}", item, out)
    elif isinstance(value, list | tuple):
        return  # side data and disposition-like lists are handled explicitly
    else:
        out[prefix] = value


def flatten_ffprobe(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten ffprobe JSON into ``ffprobe:format.*``, ``ffprobe:video.*``, ``ffprobe:audio.*``.

    Adds ``ffprobe:video.rotation_cw`` (clockwise display rotation; ffprobe reports the
    counter-clockwise angle of the display matrix) and ``ffprobe:audio.count``.
    """
    flat: dict[str, Any] = {}
    if not data:
        return flat
    _flatten("ffprobe:format", data.get("format", {}), flat)
    streams = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    ) or next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if video is not None:
        _flatten("ffprobe:video", video, flat)
        for side in video.get("side_data_list", []):
            if "rotation" in side:
                flat["ffprobe:video.rotation_cw"] = -float(side["rotation"])
    if audio:
        _flatten("ffprobe:audio", audio[0], flat)
        flat["ffprobe:audio.count"] = len(audio)
        flat["ffprobe:audio.channels_total"] = sum(int(s.get("channels", 0)) for s in audio)
    return flat


def _capture(index: TagIndex) -> tuple[datetime | None, str | None]:
    for key in CAPTURE_TIME_KEYS:
        moment = c.parse_datetime(index.get(key))
        if moment is None:
            continue
        if moment.tzinfo is None:
            for offset_key in OFFSET_KEYS:
                zone = c.parse_utc_offset(index.get(offset_key))
                if zone is not None:
                    moment = moment.replace(tzinfo=zone)
                    break
        return moment, c.format_offset(moment)
    return None, None


def _duration_ms(index: TagIndex) -> int | None:
    for key in ("ffprobe:format.duration", "ffprobe:video.duration", "ffprobe:audio.duration"):
        seconds = c.positive_float(index.get(key))
        if seconds is not None:
            return round(seconds * 1000)
    seconds = c.positive_float(index.get("Composite:Duration") or index.get("Duration"))
    return None if seconds is None else round(seconds * 1000)


def _orientation(width: int | None, height: int | None, rotation: int | None) -> Orientation | None:
    if not width or not height:
        return None
    if rotation in (90, 270):
        width, height = height, width
    if width == height:
        return Orientation.SQUARE
    return Orientation.PORTRAIT if height > width else Orientation.LANDSCAPE


def _container(index: TagIndex) -> str | None:
    extension = c.to_str(index.get("File:FileTypeExtension"))
    if extension:
        return extension.lower()
    names = c.to_str(index.get("ffprobe:format.format_name"))
    return names.split(",")[0] if names else None


def _timecode_from_reference(index: TagIndex, sample_rate: int | None) -> str | None:
    reference = c.to_int(index.get("RIFF:TimeReference") or index.get("BWF:TimeReference"))
    if reference is None or not sample_rate:
        return None
    millis = round(reference * 1000 / sample_rate)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    return f"{hours:02d}:{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


def _speed_mps(index: TagIndex) -> float | None:
    speed = c.to_float(index.get("GPS:GPSSpeed"))
    if speed is None:
        return None
    unit = c.to_str(index.get("GPS:GPSSpeedRef")) or "K"
    return speed * SPEED_UNITS_MPS.get(unit.upper()[:1], SPEED_UNITS_MPS["K"])


def build_probe_output(
    *,
    media_type: MediaType,
    primary_rel_path: str,
    ffprobe: Mapping[str, Any] | None,
    exiftool: Mapping[str, Any] | None,
    sidecars: Mapping[str, Mapping[str, str]],
    decoders: Collection[str],
    camera_proxy: str | None,
    raw_extra: Mapping[str, Any] | None = None,
) -> ProbeOutput:
    """Normalise tool outputs (``None`` = tool failed) into the probe contract.

    ``sidecars`` maps sidecar rel paths to their flattened keys (earlier entries win).
    """
    extra: dict[str, Any] = flatten_ffprobe(ffprobe)
    for flat in sidecars.values():
        for key, value in flat.items():
            extra.setdefault(key, value)
    index = TagIndex(exiftool or {}, extra)
    data = apply_rules(RULES, index)
    has_video_stream = "ffprobe:video.codec_name" in extra

    video = data.get("video", {})
    if media_type is MediaType.PHOTO:
        video = _photo_dimensions(index, video)
    elif not has_video_stream and media_type is not MediaType.VIDEO:
        video = {}
    if media_type is MediaType.VIDEO and "width" not in video:
        for width_key, height_key in (
            ("File:ImageWidth", "File:ImageHeight"),
            ("Track1:ImageWidth", "Track1:ImageHeight"),
            ("ImageWidth", "ImageHeight"),
        ):
            width, height = (
                c.positive_int(index.get(width_key)),
                c.positive_int(index.get(height_key)),
            )
            if width and height:
                video["width"], video["height"] = width, height
                break
    capture_fps = video.get("capture_fps")
    if capture_fps is not None and abs(capture_fps - (video.get("fps") or 0)) < 0.01:
        video.pop("capture_fps")

    audio = data.get("audio", {})
    if (
        audio
        and not extra.get("ffprobe:audio.codec_name")
        and "RIFF:NumChannels" not in (exiftool or {})
    ):
        audio = {}
    if audio:
        total = c.positive_int(extra.get("ffprobe:audio.channels_total"))
        if total:
            audio["channels"] = total
        if extra.get("ffprobe:audio.count", 1) > 1:
            audio["track_names"] = [f"A{n + 1}" for n in range(int(extra["ffprobe:audio.count"]))]
        timecode = _timecode_from_reference(index, audio.get("sample_rate"))
        if timecode:
            audio["timecode"] = timecode

    location = data.get("location", {})
    speed = _speed_mps(index)
    if speed is not None:
        location["speed_mps"] = speed

    capture_at, capture_tz = _capture(index)
    rotation = video.get("rotation")
    orientation = _orientation(video.get("width"), video.get("height"), rotation)
    proxy_present = camera_proxy is not None
    decode = decide_decode_status(
        extension=PurePosixPath(primary_rel_path).suffix.lstrip("."),
        ffprobe_ok=ffprobe is not None or media_type is not MediaType.VIDEO,
        video_codec=video.get("codec") if media_type is MediaType.VIDEO else None,
        decoders=decoders,
        has_proxy=proxy_present,
    )
    raw: dict[str, Any] = {
        "ffprobe": dict(ffprobe) if ffprobe is not None else None,
        "exiftool": dict(exiftool) if exiftool is not None else None,
        "sidecar_xml": {k: dict(v) for k, v in sidecars.items()} or None,
        **(raw_extra or {}),
    }
    if camera_proxy is not None:
        raw["camera_proxy"] = camera_proxy
    clip_name = data.get("clip_name") or PurePosixPath(primary_rel_path).stem
    photo = data.get("photo") if media_type is MediaType.PHOTO else None
    return ProbeOutput.model_validate(
        {
            "container": _container(index),
            "duration_ms": None if media_type is MediaType.PHOTO else _duration_ms(index),
            "capture_at": capture_at,
            "capture_tz": capture_tz,
            "reel": data.get("reel"),
            "clip_name": clip_name,
            "scene": data.get("scene"),
            "take": data.get("take"),
            "orientation": orientation,
            "camera": data.get("camera", {}),
            "lens": data.get("lens", {}),
            "exposure": data.get("exposure", {}),
            "video": video or None,
            "audio": audio or None,
            "location": location or None,
            "attitude": data.get("attitude") or None,
            "photo": photo or None,
            "decode_status": decode.status,
            "decoder_hint": decode.hint,
            "raw": raw,
        }
    )


def _photo_dimensions(index: TagIndex, video: dict[str, Any]) -> dict[str, Any]:
    """Photos carry their (EXIF-oriented) dimensions in ``video``; RAW files often lack ffprobe."""
    result = dict(video)
    width = c.positive_int(index.get("ExifIFD:ExifImageWidth")) or c.positive_int(
        index.get("File:ImageWidth")
    )
    height = c.positive_int(index.get("ExifIFD:ExifImageHeight")) or c.positive_int(
        index.get("File:ImageHeight")
    )
    width = width or result.get("width")
    height = height or result.get("height")
    if not width or not height:
        for key in ("SubIFD:ImageWidth", "IFD0:ImageWidth"):
            width = width or c.positive_int(index.get(key))
        for key in ("SubIFD:ImageHeight", "IFD0:ImageHeight"):
            height = height or c.positive_int(index.get(key))
    orientation = c.to_int(index.get("IFD0:Orientation"))
    if width and height:
        if orientation in EXIF_ORIENTATION_SWAPS:
            width, height = height, width
        result["width"], result["height"] = width, height
    result.pop("fps", None)
    result.pop("bitrate", None)
    return result
