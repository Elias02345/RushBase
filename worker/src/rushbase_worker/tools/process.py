"""Run an external tool with a timeout, without blocking the event loop."""

import asyncio
import contextlib
from dataclasses import dataclass


class ToolError(Exception):
    """An external tool failed, timed out or is missing."""

    def __init__(self, tool: str, message: str, *, returncode: int | None = None) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.returncode = returncode


@dataclass(frozen=True)
class ToolResult:
    """Captured output of a finished tool."""

    returncode: int
    stdout: bytes
    stderr: str


async def run_tool(
    tool: str, args: list[str], *, timeout: float, ok_codes: tuple[int, ...] = (0,)
) -> ToolResult:
    """Run ``args`` (``args[0]`` is the executable) and return its output.

    Raises :class:`ToolError` when the executable is missing, the timeout expires (the process
    is killed) or the exit status is not in ``ok_codes``.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise ToolError(tool, f"cannot execute {args[0]}: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        await _kill(process)
        raise ToolError(tool, f"timed out after {timeout:.0f}s") from exc
    except asyncio.CancelledError:
        await _kill(process)
        raise
    code = process.returncode if process.returncode is not None else -1
    text = stderr.decode("utf-8", errors="replace").strip()
    if code not in ok_codes:
        tail = text[-800:] if text else "no error output"
        raise ToolError(tool, f"exit status {code}: {tail}", returncode=code)
    return ToolResult(code, stdout, text)


async def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    await process.wait()
