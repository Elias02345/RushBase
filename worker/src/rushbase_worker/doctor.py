"""Environment checks behind ``rushbase doctor``."""

import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx

from rushbase_core import LibraryError, open_library

Status = Literal["ok", "error", "skipped"]

REQUIRED_BINARIES: dict[str, tuple[str, ...]] = {
    "ffmpeg": ("-version",),
    "ffprobe": ("-version",),
    "exiftool": ("-ver",),
}


@dataclass(frozen=True)
class Check:
    """Outcome of one doctor check."""

    name: str
    status: Status
    detail: str

    def to_dict(self) -> dict[str, str]:
        """Return the JSON representation."""
        return asdict(self)


def check_binary(name: str, version_args: tuple[str, ...]) -> Check:
    """Check that ``name`` is on PATH and runs."""
    path = shutil.which(name)
    if path is None:
        return Check(name, "error", f"{name} not found on PATH")
    try:
        result = subprocess.run(
            [path, *version_args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(name, "error", f"{path} could not be executed: {exc}")
    if result.returncode != 0:
        return Check(name, "error", f"{path} exited with status {result.returncode}")
    first_line = (result.stdout or result.stderr).strip().splitlines()[:1]
    return Check(name, "ok", first_line[0] if first_line else path)


def check_library(root: Path | None, expected_id: UUID | None) -> Check:
    """Check that the library mount is readable and (optionally) the expected library."""
    if root is None:
        return Check("library", "skipped", "no library root configured (--library)")
    try:
        manifest = open_library(root, expected_id)
    except LibraryError as exc:
        return Check("library", "error", str(exc))
    return Check("library", "ok", f"library {manifest.library_id} at {root}")


def check_server(url: str | None, timeout: float = 5.0) -> Check:
    """Check that the server's ``/health`` endpoint answers with HTTP 200."""
    if url is None:
        return Check("server", "skipped", "no server URL configured (--server-url)")
    target = url.rstrip("/") + "/health"
    try:
        response = httpx.get(target, timeout=timeout)
    except httpx.HTTPError as exc:
        return Check("server", "error", f"{target} unreachable: {exc}")
    if response.status_code != httpx.codes.OK:
        return Check("server", "error", f"{target} returned HTTP {response.status_code}")
    return Check("server", "ok", f"{target} is healthy")


def run_checks(
    library: Path | None, library_id: UUID | None, server_url: str | None
) -> list[Check]:
    """Run all checks in a stable order."""
    checks = [check_binary(name, args) for name, args in REQUIRED_BINARIES.items()]
    checks.append(check_library(library, library_id))
    checks.append(check_server(server_url))
    return checks
