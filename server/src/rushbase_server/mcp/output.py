"""Tool results: compact JSON text capped at 30 000 characters, cursors and image content."""

import asyncio
import base64
import binascii
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError

from rushbase_core.mcp_tools import MAX_IMAGE_EDGE, MAX_IMAGES, MAX_RESULT_CHARS
from rushbase_server.services import derived

THUMB_EDGE = 256
JPEG_QUALITY = 72


class ToolFailure(Exception):
    """A tool call cannot be completed; the message is shown to the model."""


@dataclass
class ToolOutput:
    """JSON payload plus images (as raw JPEG bytes with a caption)."""

    payload: dict[str, Any]
    images: list[tuple[str, bytes]] = field(default_factory=list)
    list_key: str | None = None
    """Entry list that may be trimmed (with a cursor) when the result exceeds the cap."""
    offset: int = 0
    droppable: tuple[str, ...] = ()
    """Optional payload keys removed first when the result is too large."""


def encode_cursor(offset: int) -> str:
    """Opaque cursor for an offset."""
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """Offset of a cursor (0 when absent); invalid cursors raise :class:`ToolFailure`."""
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        prefix, _, number = raw.partition(":")
        if prefix != "o" or not number.isdigit():
            raise ValueError
        return int(number)
    except (ValueError, binascii.Error) as exc:
        raise ToolFailure("invalid cursor; use `next_cursor` from a previous result") from exc


def dumps(payload: dict[str, Any]) -> str:
    """Compact JSON."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def fit(
    payload: dict[str, Any],
    list_key: str | None = None,
    offset: int = 0,
    droppable: tuple[str, ...] = (),
    cap: int = MAX_RESULT_CHARS - 500,
) -> str:
    """Serialize ``payload`` within the cap.

    First removes ``droppable`` keys (largest detail first), then trims trailing entries of
    ``payload[list_key]`` and points ``next_cursor`` at the first dropped entry.
    """
    text = dumps(payload)
    for key in droppable:
        if len(text) <= cap:
            break
        if key in payload:
            payload = {k: v for k, v in payload.items() if k != key}
            payload["omitted"] = [*payload.get("omitted", []), key]
            text = dumps(payload)
    if len(text) > cap and list_key and isinstance(payload.get(list_key), list):
        items = list(payload[list_key])
        kept = len(items)
        while kept > 1 and len(text) > cap:
            kept = max(1, kept - max(1, kept // 5))
            payload = {
                **payload,
                list_key: items[:kept],
                "next_cursor": encode_cursor(offset + kept),
            }
            payload["truncated_for_size"] = True
            text = dumps(payload)
    if len(text) > cap:
        raise ToolFailure("result too large; narrow the query or lower `limit`")
    return text


def sub_path_from_url(url: str | None, asset_id: UUID) -> str | None:
    """Path below the asset's derived directory from a ``/api/v1/derived/...`` URL."""
    marker = f"{derived.URL_PREFIX}/{asset_id}/"
    if not url or not url.startswith(marker):
        return None
    return unquote(url[len(marker) :])


def _jpeg(path: Path, edge: int) -> bytes:
    with Image.open(path) as image:
        upright = ImageOps.exif_transpose(image) or image
        upright.thumbnail((edge, edge))
        buffer = io.BytesIO()
        upright.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return buffer.getvalue()


async def load_image(
    root: Path | None, asset_id: UUID, sub_path: str | None, edge: int
) -> bytes | None:
    """Downscaled JPEG of a derived file, ``None`` when unavailable (never raises)."""
    if root is None or sub_path is None:
        return None
    try:
        path = derived.resolve_derived(root, asset_id, sub_path)
        return await asyncio.to_thread(_jpeg, path, min(edge, MAX_IMAGE_EDGE))
    except (derived.DerivedPathError, OSError, ValueError, UnidentifiedImageError):
        return None


def clamp_images(images: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """At most :data:`MAX_IMAGES` images."""
    return images[:MAX_IMAGES]
