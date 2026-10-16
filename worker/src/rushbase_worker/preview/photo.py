"""Photo previews with Pillow (+ pillow-heif for HEIC) and rawpy for camera RAW/DNG."""

import io
from pathlib import Path

import numpy as np
import pillow_heif
import rawpy
from PIL import Image, ImageOps

from rushbase_core.media import is_raw_photo

pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 600_000_000
EMBEDDED_MIN_EDGE = 1000
"""Embedded RAW thumbnails smaller than this (long edge) are not used as the preview."""


class PhotoDecodeError(Exception):
    """The photo could not be decoded by any available decoder."""


def _from_raw(path: Path) -> Image.Image:
    """Embedded JPEG/bitmap thumbnail when large enough (fast path), else a half-size develop."""
    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            thumb = None
        if thumb is not None:
            image: Image.Image | None = None
            if thumb.format == rawpy.ThumbFormat.JPEG:
                image = Image.open(io.BytesIO(thumb.data))
            elif thumb.format == rawpy.ThumbFormat.BITMAP:
                image = Image.fromarray(np.asarray(thumb.data))
            if image is not None and max(image.size) >= EMBEDDED_MIN_EDGE:
                return image
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
        return Image.fromarray(rgb)


def load_photo(path: Path) -> Image.Image:
    """Open ``path`` as an upright RGB image (EXIF orientation applied)."""
    errors: list[str] = []
    image: Image.Image | None = None
    if is_raw_photo(path.name):
        try:
            image = _from_raw(path)
        except (rawpy.LibRawError, ValueError, OSError) as exc:
            errors.append(f"rawpy: {exc}")
    if image is None:
        try:
            image = Image.open(path)
            image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            errors.append(f"Pillow: {exc}")
            image = None
    if image is None:
        raise PhotoDecodeError(f"cannot decode {path.name}: {'; '.join(errors)}")
    return ImageOps.exif_transpose(image).convert("RGB")


def resized(image: Image.Image, width: int) -> Image.Image:
    """Scale down to at most ``width`` px wide (never upscale)."""
    if image.width <= width:
        return image.copy()
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS)
