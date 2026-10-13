"""Decode status per ADR-0003 and matching of in-camera proxies."""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import PurePosixPath

from rushbase_core.results import DecodeStatus

VENDOR_RAW: dict[str, str] = {
    "r3d": "RED R3D",
    "braw": "Blackmagic RAW",
    "ari": "ARRIRAW",
    "crm": "Canon Cinema RAW Light",
}
"""File extensions of vendor RAW video that needs a user-installed decoder plugin."""
PRORES_RAW_CODEC = "prores_raw"


@dataclass(frozen=True)
class Decode:
    """Decode status plus an optional hint for the UI."""

    status: DecodeStatus
    hint: str | None = None


def match_camera_proxy(primary_rel_path: str, proxies: Collection[str]) -> str | None:
    """Pick the in-camera proxy that belongs to the clip, by clip name.

    Exact stem match wins, then a proxy whose stem starts with the clip stem (``C0001`` and
    ``C0001S03``), then ``None``. Timecode/duration matching is not implemented.
    """
    stem = PurePosixPath(primary_rel_path).stem.lower()
    ranked = sorted(
        (
            (0 if PurePosixPath(p).stem.lower() == stem else 1, p)
            for p in proxies
            if PurePosixPath(p).stem.lower().startswith(stem)
        ),
    )
    return ranked[0][1] if ranked else None


def decide_decode_status(
    *,
    extension: str,
    ffprobe_ok: bool,
    video_codec: str | None,
    decoders: Collection[str],
    has_proxy: bool,
) -> Decode:
    """Decide whether this worker can decode the original (ADR-0003).

    Vendor RAW and ProRes RAW without a decoder fall back to a camera proxy (``proxy_only``) or,
    without one, to ``decoder_missing`` (metadata only). A container ffprobe cannot read gives
    ``metadata_only``. A video codec absent from the ffmpeg build is ``decoder_error`` when the
    file is otherwise readable.
    """
    fallback = DecodeStatus.PROXY_ONLY if has_proxy else DecodeStatus.DECODER_MISSING
    vendor = VENDOR_RAW.get(extension.lower())
    if vendor is not None:
        return Decode(
            fallback,
            f"{vendor} needs a decoder plugin (entry-point group rushbase.decoders); "
            "metadata was read with ExifTool",
        )
    if video_codec == PRORES_RAW_CODEC and PRORES_RAW_CODEC not in decoders:
        return Decode(
            fallback, "ProRes RAW needs an ffmpeg build with the prores_raw decoder (FFmpeg 8.0+)"
        )
    if not ffprobe_ok:
        status = DecodeStatus.PROXY_ONLY if has_proxy else DecodeStatus.METADATA_ONLY
        return Decode(status, "ffprobe cannot read this container; metadata from ExifTool only")
    if video_codec is not None and video_codec not in decoders and video_codec != "unknown":
        return Decode(
            DecodeStatus.DECODER_ERROR, f"this ffmpeg build has no decoder for {video_codec}"
        )
    return Decode(DecodeStatus.OK)
