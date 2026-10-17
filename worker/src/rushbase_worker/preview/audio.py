"""Audio waveform: PNG image plus JSON peaks, computed from a streamed mono 8 kHz decode.

Peaks JSON (``waveform.json``)::

    {"schema": 1, "duration_ms": 3000, "bins": 1000, "min": [-0.5, ...], "max": [0.5, ...]}

``min``/``max`` hold the lowest and highest sample (-1..1, three decimals) of each of ``bins``
equal time slices.
"""

import asyncio
import contextlib
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from rushbase_worker.config import WorkerSettings
from rushbase_worker.preview.derived import atomic_output
from rushbase_worker.tools.process import ToolError
from rushbase_worker.tools.safe_input import input_args
from rushbase_worker.tools.toolbox import Toolbox

SAMPLE_RATE = 8000
WINDOW = 80  # samples per fine window (10 ms)
READ_BYTES = 1 << 20
WAVE_COLOR = (138, 180, 248, 255)

Floats = NDArray[np.float32]


class PeakAccumulator:
    """Streaming min/max of consecutive ``window``-sample blocks (bounded memory)."""

    def __init__(self, window: int = WINDOW) -> None:
        self._window = window
        self._mins: list[Floats] = []
        self._maxs: list[Floats] = []
        self._carry: Floats = np.empty(0, dtype=np.float32)

    def feed(self, chunk: Floats) -> None:
        """Add the next chunk of samples."""
        data = np.concatenate([self._carry, chunk]) if self._carry.size else chunk
        usable = (data.size // self._window) * self._window
        if usable:
            blocks = data[:usable].reshape(-1, self._window)
            self._mins.append(blocks.min(axis=1))
            self._maxs.append(blocks.max(axis=1))
        self._carry = data[usable:]

    def finish(self) -> tuple[Floats, Floats]:
        """Return all window minima and maxima (a trailing partial window counts)."""
        if self._carry.size:
            self._mins.append(np.array([self._carry.min()], dtype=np.float32))
            self._maxs.append(np.array([self._carry.max()], dtype=np.float32))
            self._carry = np.empty(0, dtype=np.float32)
        if not self._mins:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
        return np.concatenate(self._mins), np.concatenate(self._maxs)


def reduce_bins(mins: Floats, maxs: Floats, bins: int) -> tuple[Floats, Floats]:
    """Reduce fine windows to at most ``bins`` bins (min of mins, max of maxs)."""
    count = mins.size
    if count == 0:
        return mins, maxs
    bins = min(bins, count)
    edges = np.linspace(0, count, bins, endpoint=False).astype(np.intp)
    return np.minimum.reduceat(mins, edges), np.maximum.reduceat(maxs, edges)


def render_waveform(mins: Floats, maxs: Floats, width: int, height: int) -> Image.Image:
    """Draw the waveform as a transparent PNG-ready RGBA image."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    middle = (height - 1) / 2
    if mins.size == 0:
        draw.line([(0, middle), (width, middle)], fill=WAVE_COLOR)
        return image
    for x in range(width):
        index = min(mins.size - 1, x * mins.size // width)
        top = middle - float(np.clip(maxs[index], -1, 1)) * middle
        bottom = middle - float(np.clip(mins[index], -1, 1)) * middle
        draw.line([(x, top), (x, max(bottom, top + 1))], fill=WAVE_COLOR)
    return image


def peaks_document(duration_ms: int | None, mins: Floats, maxs: Floats) -> dict[str, object]:
    """The JSON document written next to the waveform image."""
    return {
        "schema": 1,
        "duration_ms": duration_ms,
        "bins": int(mins.size),
        "min": [round(float(v), 3) for v in mins],
        "max": [round(float(v), 3) for v in maxs],
    }


async def _stream_peaks(toolbox: Toolbox, source: Path, timeout: float) -> tuple[Floats, Floats]:
    exe = toolbox.ffmpeg_path
    if exe is None:
        raise ToolError("ffmpeg", "ffmpeg not found")
    process = await asyncio.create_subprocess_exec(
        exe,
        "-v",
        "error",
        "-nostdin",
        *input_args(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "f32le",
        "-",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    peaks = PeakAccumulator()
    pending = b""

    async def pump() -> None:
        nonlocal pending
        assert process.stdout is not None
        while block := await process.stdout.read(READ_BYTES):
            pending += block
            usable = (len(pending) // 4) * 4
            peaks.feed(np.frombuffer(pending[:usable], dtype=np.float32))
            pending = pending[usable:]

    try:
        await asyncio.wait_for(pump(), timeout)
        stderr = await process.stderr.read() if process.stderr else b""
        code = await process.wait()
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    if code != 0:
        raise ToolError("ffmpeg", f"audio decode failed: {stderr.decode(errors='replace')[-400:]}")
    return peaks.finish()


async def make_waveform(
    toolbox: Toolbox,
    settings: WorkerSettings,
    source: Path,
    duration_ms: int | None,
    png: Path,
    peaks_json: Path,
) -> None:
    """Write ``png`` and ``peaks_json`` for the audio of ``source``."""
    mins, maxs = await _stream_peaks(toolbox, source, settings.long_tool_timeout_seconds)
    mins, maxs = reduce_bins(mins, maxs, settings.waveform_peaks)
    image = await asyncio.to_thread(
        render_waveform, mins, maxs, settings.waveform_width, settings.waveform_height
    )
    with atomic_output(png) as out:
        image.save(out, "PNG")
    with atomic_output(peaks_json) as out:
        out.write_text(json.dumps(peaks_document(duration_ms, mins, maxs)), encoding="utf-8")
