"""Media type detection by file extension and the roles files play inside an asset."""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final


class MediaType(StrEnum):
    """Coarse media class of an asset."""

    VIDEO = "video"
    PHOTO = "photo"
    AUDIO = "audio"
    OTHER = "other"


class FileRole(StrEnum):
    """Role of a file within an asset (``asset_file.role``)."""

    PRIMARY = "primary"
    RAW = "raw"
    JPEG = "jpeg"
    SIDECAR = "sidecar"
    PROXY_CAMERA = "proxy_camera"
    TELEMETRY = "telemetry"
    AUDIO_SYNC = "audio_sync"
    MEMBER = "member"


VIDEO_EXTENSIONS: Final = frozenset(
    [
        "mp4",
        "mov",
        "m4v",
        "mxf",
        "mkv",
        "avi",
        "mts",
        "m2ts",
        "mpg",
        "mpeg",
        "webm",
        "r3d",
        "braw",
        "ari",
        "crm",
        "3gp",
    ]
)
RAW_PHOTO_EXTENSIONS: Final = frozenset(
    [
        "cr2",
        "cr3",
        "crw",
        "nef",
        "nrw",
        "arw",
        "sr2",
        "srf",
        "raf",
        "orf",
        "rw2",
        "pef",
        "dng",
        "srw",
        "3fr",
        "fff",
        "iiq",
        "x3f",
        "rwl",
        "erf",
        "mrw",
        "dcr",
        "kdc",
    ]
)
RASTER_PHOTO_EXTENSIONS: Final = frozenset(
    ["jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "webp", "bmp", "gif", "avif"]
)
AUDIO_EXTENSIONS: Final = frozenset(
    ["wav", "bwf", "mp3", "flac", "aac", "m4a", "aif", "aiff", "ogg", "opus", "wma"]
)
SIDECAR_EXTENSIONS: Final = frozenset(
    ["xmp", "xml", "thm", "lrf", "lrv", "srt", "pp3", "dop", "cpf", "bim", "ind", "cpi", "mpl"]
)
"""Camera and editing sidecars that belong to a media file with the same stem."""

SEQUENCE_EXTENSIONS: Final = frozenset(["dng", "dpx", "exr", "tif", "tiff", "png", "ari", "cin"])
"""Frame formats that can form numbered image sequences (see :mod:`rushbase_core.grouping`)."""


def extension_of(name: str) -> str:
    """Return the lower-case extension of ``name`` without the dot (``""`` if none)."""
    return PurePosixPath(name).suffix.lstrip(".").lower()


def media_type_for(name: str) -> MediaType:
    """Classify a file name by its extension. Sidecars and unknown files are ``OTHER``."""
    ext = extension_of(name)
    if ext in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    if ext in RAW_PHOTO_EXTENSIONS or ext in RASTER_PHOTO_EXTENSIONS:
        return MediaType.PHOTO
    if ext in AUDIO_EXTENSIONS:
        return MediaType.AUDIO
    return MediaType.OTHER


def is_raw_photo(name: str) -> bool:
    """Return whether ``name`` is a camera RAW photo."""
    return extension_of(name) in RAW_PHOTO_EXTENSIONS


def is_sidecar(name: str) -> bool:
    """Return whether ``name`` has a sidecar extension."""
    return extension_of(name) in SIDECAR_EXTENSIONS
