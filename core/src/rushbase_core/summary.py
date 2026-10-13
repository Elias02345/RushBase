"""Deterministic technical summaries ("4K HEVC · 25 fps · Sony FX3 · S-Log3 · 85 mm").

Built from the normalized probe output with fixed templates (no model involved), in German and
English. Used for the asset detail view, the XMP description and as the summary of assets that
have not been described by an AI stage yet.
"""

from typing import Final, Literal

from rushbase_core.results import ProbeOutput

Lang = Literal["de", "en"]
SEPARATOR: Final = " · "
_CODEC_NAMES: Final = {
    "hevc": "HEVC",
    "h265": "HEVC",
    "h264": "H.264",
    "avc": "H.264",
    "avc1": "H.264",
    "prores": "ProRes",
    "av1": "AV1",
    "vp9": "VP9",
    "mpeg2video": "MPEG-2",
    "dnxhd": "DNxHD",
    "mjpeg": "MJPEG",
    "cinepak": "Cinepak",
}
_WORDS: Final = {
    "de": {"portrait": "Hochformat", "square": "Quadrat", "captured": "aufgenommen mit"},
    "en": {"portrait": "portrait", "square": "square", "captured": "captured at"},
}


def resolution_label(width: int | None, height: int | None) -> str | None:
    """Short label for a frame size (``4K``, ``Full HD`` ... or ``WxH``)."""
    if not width or not height:
        return None
    longest = max(width, height)
    if longest >= 7600:
        return "8K"
    if longest >= 3800:
        return "4K"
    if longest >= 2600:
        return "2.7K"
    if longest >= 1900:
        return "Full HD"
    if longest >= 1280:
        return "HD"
    return f"{width}x{height}"


def _number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def codec_label(codec: str | None) -> str | None:
    """Human readable codec name."""
    if not codec:
        return None
    return _CODEC_NAMES.get(codec.lower(), codec.upper() if len(codec) <= 5 else codec)


def _camera(probe: ProbeOutput) -> str | None:
    make = (probe.camera.make or "").strip()
    model = (probe.camera.model or "").strip()
    if not model:
        return make or None
    if make and not model.lower().startswith(make.lower()):
        return f"{make} {model}"
    return model


def technical_summary(
    probe: ProbeOutput | None, media_type: str, lang: Lang, *, duration: bool = False
) -> str:
    """Return the technical summary of an asset, or ``""`` when nothing is known."""
    if probe is None:
        return ""
    words = _WORDS[lang]
    parts: list[str | None] = []
    video = probe.video
    if media_type == "audio":
        audio = probe.audio
        if audio is not None:
            parts += [
                f"{_number(audio.sample_rate / 1000)} kHz" if audio.sample_rate else None,
                f"{audio.channels} ch" if audio.channels else None,
                (audio.codec or "").upper() or None,
            ]
    else:
        width = video.width if video else None
        height = video.height if video else None
        parts.append(resolution_label(width, height))
        if video is not None:
            parts.append(codec_label(video.codec))
            if media_type == "video" and video.fps:
                fps = f"{_number(video.fps)} fps"
                if video.capture_fps and abs(video.capture_fps - video.fps) > 0.5:
                    fps += f" ({words['captured']} {_number(video.capture_fps)} fps)"
                parts.append(fps)
    parts.append(_camera(probe))
    parts.append(probe.exposure.log_profile)
    if probe.lens.focal_length_mm:
        parts.append(f"{_number(probe.lens.focal_length_mm)} mm")
    if media_type == "photo" and probe.exposure.iso:
        parts.append(f"ISO {probe.exposure.iso}")
    if probe.orientation is not None and probe.orientation.value in ("portrait", "square"):
        parts.append(words[probe.orientation.value])
    if duration and probe.duration_ms:
        seconds = probe.duration_ms // 1000
        parts.append(f"{seconds // 60}:{seconds % 60:02d}")
    return SEPARATOR.join(p for p in parts if p)
