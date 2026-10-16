"""Typed wrappers around ffprobe, ffmpeg and ExifTool."""

import json
import re
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path
from typing import Any

from rushbase_worker.config import WorkerSettings
from rushbase_worker.tools.locate import locate
from rushbase_worker.tools.process import ToolError, ToolResult, run_tool

EXIFTOOL_FLAGS = ("-j", "-G1", "-n", "-a", "-u", "-ee3", "-api", "largefilesupport=1")
"""JSON, group names (family 1), numeric values, duplicates, unknown tags, embedded telemetry."""
MAX_STRING_CHARS = 2000
MAX_LIST_ITEMS = 500
_DECODER_LINE = re.compile(r"^\s*[VAS][A-Z.]{5}\s+(\S+)\s")


def shrink(value: Any) -> Any:
    """Bound the size of tool output: cut giant strings (binary blobs) and long lists."""
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return f"{value[:MAX_STRING_CHARS]}... (truncated, {len(value)} characters)"
        return value
    if isinstance(value, list):
        items = [shrink(v) for v in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"... ({len(value) - MAX_LIST_ITEMS} more items omitted)")
        return items
    if isinstance(value, dict):
        return {k: shrink(v) for k, v in value.items()}
    return value


def parse_decoders(text: str) -> frozenset[str]:
    """Parse the output of ``ffmpeg -decoders`` into decoder names."""
    return frozenset(m.group(1) for line in text.splitlines() if (m := _DECODER_LINE.match(line)))


class Toolbox:
    """Locates the external tools once and runs them with timeouts."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self._decoders: frozenset[str] | None = None

    @cached_property
    def ffmpeg_path(self) -> str | None:
        """Resolved ffmpeg executable."""
        return locate("ffmpeg", self.settings.ffmpeg, self.settings.toolchain_dir)

    @cached_property
    def ffprobe_path(self) -> str | None:
        """Resolved ffprobe executable."""
        return locate("ffprobe", self.settings.ffprobe, self.settings.toolchain_dir)

    @cached_property
    def exiftool_path(self) -> str | None:
        """Resolved exiftool executable."""
        return locate("exiftool", self.settings.exiftool, self.settings.toolchain_dir)

    @staticmethod
    def _need(tool: str, path: str | None) -> str:
        if path is None:
            raise ToolError(tool, f"{tool} not found (set RUSHBASE_{tool.upper()} or install it)")
        return path

    async def ffprobe(self, path: Path) -> dict[str, Any]:
        """Return ``ffprobe -show_format -show_streams -show_chapters`` as a dict."""
        exe = self._need("ffprobe", self.ffprobe_path)
        result = await run_tool(
            "ffprobe",
            [
                exe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-show_chapters",
                str(path),
            ],
            timeout=self.settings.tool_timeout_seconds,
        )
        return _json_object("ffprobe", result.stdout)

    async def exiftool(self, path: Path) -> dict[str, Any]:
        """Return ExifTool's tags for ``path`` (``group:tag`` keys), blobs shortened."""
        exe = self._need("exiftool", self.exiftool_path)
        result = await run_tool(
            "exiftool",
            [exe, *EXIFTOOL_FLAGS, str(path)],
            timeout=self.settings.tool_timeout_seconds,
            ok_codes=(0, 1),
        )
        parsed = json.loads(result.stdout.decode("utf-8", errors="replace") or "[]")
        if not parsed:
            raise ToolError("exiftool", "no metadata returned")
        entry: dict[str, Any] = parsed[0]
        for noisy in ("SourceFile", "System:Directory"):
            entry.pop(noisy, None)
        shrunk: dict[str, Any] = shrink(entry)
        return shrunk

    async def ffmpeg(
        self, args: Sequence[str], *, timeout: float | None = None, hwaccel: bool = False
    ) -> ToolResult:
        """Run ffmpeg quietly with ``args`` (inputs, filters, output)."""
        exe = self._need("ffmpeg", self.ffmpeg_path)
        pre = ["-hwaccel", "auto"] if hwaccel and self.settings.ffmpeg_hwaccel else []
        return await run_tool(
            "ffmpeg",
            [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *pre, *args],
            timeout=timeout or self.settings.tool_timeout_seconds,
        )

    async def decoders(self) -> frozenset[str]:
        """Names of the decoders this ffmpeg build offers (``prores_raw`` included if present)."""
        if self._decoders is not None:
            return self._decoders
        exe = self._need("ffmpeg", self.ffmpeg_path)
        result = await run_tool("ffmpeg", [exe, "-hide_banner", "-decoders"], timeout=30.0)
        names = parse_decoders(result.stdout.decode("utf-8", errors="replace"))
        self._decoders = names
        return names


def _json_object(tool: str, raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ToolError(tool, f"invalid JSON output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolError(tool, "unexpected JSON output")
    return parsed
