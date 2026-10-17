"""Video previews: poster, hover-scrub sprite and 720p proxy (all via ffmpeg + Pillow).

Sprite format: one JPEG with ``tiles`` equally sized frames in a single row (default 10 tiles
of 320 px width). Tile ``i`` (0-based, left to right) shows the frame at
``(i + 0.5) / tiles * duration``. The file name encodes the layout:
``sprite-<tiles>x1-<tile width>.jpg``.
"""

import asyncio
import tempfile
from pathlib import Path

from PIL import Image

from rushbase_worker.config import WorkerSettings
from rushbase_worker.preview.derived import atomic_output
from rushbase_worker.tools.safe_input import input_args
from rushbase_worker.tools.toolbox import Toolbox

POSTER_QUALITY = 82
SPRITE_QUALITY = 70


def quote_filter_value(value: str) -> str:
    """Quote ``value`` for use as an ffmpeg filter option (single-quote escaping)."""
    return "'" + value.replace("'", "'\\''") + "'"


def lut_filter(lut: Path | None) -> str | None:
    """The ``lut3d`` filter for a ``.cube`` file, or ``None`` when no LUT applies."""
    return None if lut is None else f"lut3d=file={quote_filter_value(str(lut))}"


def video_filter(*parts: str | None) -> str:
    """Join non-empty filter parts into one ``-vf`` chain."""
    return ",".join(p for p in parts if p)


def poster_time(duration_ms: int | None) -> float:
    """Poster timestamp in seconds: 10 % into the clip, at most 10 s, 0 when unknown."""
    if not duration_ms or duration_ms <= 0:
        return 0.0
    return min(duration_ms / 1000 * 0.1, 10.0)


def sprite_times(duration_ms: int | None, tiles: int) -> list[float]:
    """Timestamps (seconds) of the sprite tiles: the centre of each of ``tiles`` equal slices."""
    seconds = (duration_ms or 0) / 1000
    return [(i + 0.5) / tiles * seconds for i in range(tiles)]


async def extract_frame(
    toolbox: Toolbox,
    source: Path,
    t_seconds: float,
    target: Path,
    width: int,
    lut: Path | None,
    quality: int | None = None,
) -> None:
    """Decode one frame at ``t_seconds``, scaled to at most ``width`` px, into ``target``."""
    chain = video_filter(lut_filter(lut), f"scale='min({width},iw)':-2")
    await toolbox.ffmpeg(
        [
            *["-ss", f"{t_seconds:.3f}", *input_args(source), "-an", "-sn", "-frames:v", "1"],
            *["-vf", chain, *(["-q:v", str(quality)] if quality else []), str(target)],
        ],
        hwaccel=True,
    )


async def make_poster(
    toolbox: Toolbox,
    settings: WorkerSettings,
    source: Path,
    t: float,
    final: Path,
    lut: Path | None,
) -> Image.Image:
    """Write the WebP poster (``settings.poster_width`` px wide) and return it as an image."""
    with tempfile.TemporaryDirectory(prefix="rb-poster-", dir=final.parent) as tmp:
        frame = Path(tmp) / "frame.png"
        await extract_frame(toolbox, source, t, frame, settings.poster_width, lut)

        def encode() -> Image.Image:
            with Image.open(frame) as opened:
                image = opened.convert("RGB")
            with atomic_output(final) as out:
                image.save(out, "WEBP", quality=POSTER_QUALITY, method=4)
            return image

        return await asyncio.to_thread(encode)


async def make_sprite(
    toolbox: Toolbox,
    settings: WorkerSettings,
    source: Path,
    duration_ms: int | None,
    final: Path,
    lut: Path | None,
) -> tuple[int, int]:
    """Write the hover-scrub sprite; returns its ``(width, height)``."""
    tiles = settings.sprite_tiles
    tile_width = settings.sprite_tile_width
    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="rb-sprite-", dir=final.parent) as tmp:
        for i, t in enumerate(sprite_times(duration_ms, tiles)):
            frame = Path(tmp) / f"{i:02d}.png"
            await extract_frame(toolbox, source, t, frame, tile_width, lut)
            with Image.open(frame) as opened:
                images.append(opened.convert("RGB"))

        def compose() -> tuple[int, int]:
            tile_w, tile_h = images[0].size
            sheet = Image.new("RGB", (tile_w * tiles, tile_h))
            for i, image in enumerate(images):
                sheet.paste(image.resize((tile_w, tile_h)), (i * tile_w, 0))
            with atomic_output(final) as out:
                sheet.save(out, "JPEG", quality=SPRITE_QUALITY)
            return sheet.size

        return await asyncio.to_thread(compose)


async def make_proxy(
    toolbox: Toolbox, settings: WorkerSettings, source: Path, final: Path, lut: Path | None
) -> None:
    """Encode the H.264/AAC proxy (``settings.proxy_height`` max, CRF, ``+faststart``)."""
    chain = video_filter(
        lut_filter(lut), f"scale=-2:'min({settings.proxy_height},ih)'", "format=yuv420p"
    )
    with atomic_output(final) as out:
        await toolbox.ffmpeg(
            [
                *[*input_args(source), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"],
                *["-vf", chain, "-c:v", "libx264", "-preset", settings.proxy_preset],
                *["-crf", str(settings.proxy_crf), "-c:a", "aac", "-b:a", "128k", "-ac", "2"],
                *["-movflags", "+faststart", str(out)],
            ],
            timeout=settings.long_tool_timeout_seconds,
            hwaccel=True,
        )
