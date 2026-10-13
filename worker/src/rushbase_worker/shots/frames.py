"""Reduced-resolution frame stream from ffmpeg (raw BGR over a pipe, one frame in memory)."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushbase_worker.tools.process import ToolError
from rushbase_worker.tools.toolbox import Toolbox


def analysis_size(display_width: int, display_height: int, target_width: int) -> tuple[int, int]:
    """Even-sized ``(width, height)`` with the display aspect ratio, at most ``target_width``."""
    width = min(target_width, display_width)
    width -= width % 2
    width = max(width, 2)
    height = max(2, round(display_height * width / display_width))
    return width, height - height % 2


def display_size(width: int, height: int, rotation: int | None) -> tuple[int, int]:
    """Displayed frame size after the container's rotation is applied."""
    return (height, width) if rotation in (90, 270) else (width, height)


async def stream_frames(
    toolbox: Toolbox,
    source: Path,
    size: tuple[int, int],
    fps: float,
    *,
    hwaccel: bool = False,
) -> AsyncIterator[NDArray[np.uint8]]:
    """Yield BGR frames of ``size`` at ``fps`` (ffmpeg applies rotation and resampling)."""
    exe = toolbox.ffmpeg_path
    if exe is None:
        raise ToolError("ffmpeg", "ffmpeg not found")
    width, height = size
    pre = ["-hwaccel", "auto"] if hwaccel and toolbox.settings.ffmpeg_hwaccel else []
    process = await asyncio.create_subprocess_exec(
        exe,
        "-v",
        "error",
        "-nostdin",
        *pre,
        "-i",
        str(source),
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"fps={fps:.4f},scale={width}:{height}",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "-",  # fmt: skip
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    frame_bytes = width * height * 3
    try:
        while True:
            try:
                data = await process.stdout.readexactly(frame_bytes)
            except asyncio.IncompleteReadError:
                break
            yield np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
        stderr = await process.stderr.read() if process.stderr else b""
        code = await process.wait()
        if code != 0:
            raise ToolError(
                "ffmpeg", f"frame decode failed: {stderr.decode(errors='replace')[-400:]}"
            )
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
