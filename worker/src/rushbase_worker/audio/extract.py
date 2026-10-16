"""Audio extraction (16 kHz mono) and EBU R128 loudness through ffmpeg."""

import re
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushbase_core.results_ai import Loudness
from rushbase_worker.tools.process import ToolError, run_tool
from rushbase_worker.tools.toolbox import Toolbox

SAMPLE_RATE = 16_000

_INTEGRATED = re.compile(r"^\s*I:\s*(-?[0-9.]+|-inf)\s*LUFS", re.MULTILINE)
_LRA = re.compile(r"^\s*LRA:\s*(-?[0-9.]+)\s*LU", re.MULTILINE)
_TRUE_PEAK = re.compile(r"^\s*Peak:\s*(-?[0-9.]+|-inf)\s*dBFS", re.MULTILINE)


async def extract_pcm(toolbox: Toolbox, source: Path) -> NDArray[np.float32]:
    """Decode the first audio stream to mono float32 at 16 kHz (empty when there is none)."""
    try:
        result = await toolbox.ffmpeg(
            ["-i", str(source), "-map", "0:a:0?", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "f32le", "-"],
            timeout=toolbox.settings.long_tool_timeout_seconds,
        )  # fmt: skip
    except ToolError as exc:
        if "does not contain any stream" in str(exc) or "Output file #0 does not contain" in str(
            exc
        ):
            return np.zeros(0, dtype=np.float32)
        raise
    return np.frombuffer(result.stdout, dtype="<f4").astype(np.float32, copy=True)


def write_wav(path: Path, samples: NDArray[np.float32]) -> None:
    """Write mono 16-bit PCM at 16 kHz (the input format of whisper.cpp)."""
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def parse_ebur128(stderr: str) -> Loudness:
    """Integrated loudness, loudness range and true peak from ffmpeg's ``ebur128`` summary."""
    summary = stderr[stderr.rfind("Summary:") :] if "Summary:" in stderr else stderr

    def number(pattern: re.Pattern[str]) -> float | None:
        match = pattern.search(summary)
        if match is None or match.group(1) == "-inf":
            return None
        return float(match.group(1))

    return Loudness(
        integrated_lufs=number(_INTEGRATED),
        true_peak_dbtp=number(_TRUE_PEAK),
        lra_lu=number(_LRA),
    )


async def measure_loudness(toolbox: Toolbox, source: Path) -> Loudness | None:
    """EBU R128 loudness of the first audio stream via ``ebur128=peak=true``."""
    exe = toolbox.ffmpeg_path
    if exe is None:
        return None
    try:
        result = await run_tool(
            "ffmpeg",
            [exe, "-hide_banner", "-nostats", "-nostdin", "-i", str(source), "-map", "0:a:0?",
             "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
            timeout=toolbox.settings.long_tool_timeout_seconds,
        )  # fmt: skip
    except ToolError:
        return None
    return parse_ebur128(result.stderr)
