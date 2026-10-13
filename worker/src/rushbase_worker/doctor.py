"""Environment checks behind ``rushbase doctor``."""

import asyncio
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx

from rushbase_core import LibraryError, open_library
from rushbase_worker.client import ApiError, UnavailableError, WorkerClient
from rushbase_worker.config import WorkerSettings
from rushbase_worker.mount import MountError, verify_mount
from rushbase_worker.state import load_api_key, load_identity
from rushbase_worker.tools.locate import locate
from rushbase_worker.tools.toolbox import Toolbox

Status = Literal["ok", "warn", "error", "skipped"]

REQUIRED_BINARIES: dict[str, tuple[str, ...]] = {
    "ffmpeg": ("-version",),
    "ffprobe": ("-version",),
    "exiftool": ("-ver",),
}
REQUIRED_ENCODERS = ("libx264", "aac")
OPTIONAL_DECODERS = ("prores_raw",)


@dataclass(frozen=True)
class Check:
    """Outcome of one doctor check."""

    name: str
    status: Status
    detail: str

    def to_dict(self) -> dict[str, str]:
        """Return the JSON representation."""
        return asdict(self)


def check_binary(
    name: str, version_args: tuple[str, ...], settings: WorkerSettings | None = None
) -> Check:
    """Check that ``name`` is configured, in the toolchain or on PATH and runs."""
    configured = getattr(settings, name, None) if settings else None
    path = locate(name, configured, settings.toolchain_dir if settings else None)
    if path is None:
        return Check(name, "error", f"{name} not found on PATH")
    try:
        result = subprocess.run(  # noqa: S603
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


def check_perl() -> Check:
    """ExifTool is a Perl program: perl must exist."""
    path = locate("perl")
    if path is None:
        return Check("perl", "error", "perl not found; ExifTool cannot run without it")
    try:
        result = subprocess.run(  # noqa: S603
            [path, "-e", "print $^V"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("perl", "error", f"{path} could not be executed: {exc}")
    return Check("perl", "ok", f"perl {result.stdout.strip() or '(version unknown)'} at {path}")


def check_ffmpeg_features(settings: WorkerSettings) -> list[Check]:
    """Encoders needed for proxies and the optional ProRes RAW decoder."""
    toolbox = Toolbox(settings)
    if toolbox.ffmpeg_path is None:
        return [Check("ffmpeg_features", "skipped", "ffmpeg not available")]
    try:
        out = subprocess.run(  # noqa: S603
            [toolbox.ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
        decoders = asyncio.run(toolbox.decoders())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [Check("ffmpeg_features", "error", f"cannot query ffmpeg: {exc}")]
    encoders = set(re.findall(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", out.stdout, flags=re.MULTILINE))
    missing = [e for e in REQUIRED_ENCODERS if e not in encoders]
    checks = [
        Check("ffmpeg_encoders", "error", f"missing encoders: {', '.join(missing)}")
        if missing and settings.proxy_enabled
        else Check("ffmpeg_encoders", "ok" if not missing else "warn",
                   "libx264 and aac available" if not missing else f"missing: {', '.join(missing)}")
    ]  # fmt: skip
    for decoder in OPTIONAL_DECODERS:
        present = decoder in decoders
        checks.append(
            Check(
                f"decoder_{decoder}",
                "ok" if present else "warn",
                "available"
                if present
                else "not in this ffmpeg build (ProRes RAW gets metadata only)",
            )
        )
    return checks


def check_data_dir(settings: WorkerSettings) -> Check:
    """The data directory (API key, state) must be writable."""
    directory = settings.data_dir
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check("data_dir", "error", f"{directory}: {exc.strerror or exc}")
    if not os.access(directory, os.W_OK):
        return Check("data_dir", "error", f"{directory} is not writable")
    return Check("data_dir", "ok", str(directory))


def check_enrollment(settings: WorkerSettings) -> Check:
    """Whether this machine has an API key."""
    identity = load_identity(settings)
    if load_api_key(settings) is None:
        return Check("enrollment", "warn", "not enrolled (run `rushbase enroll`)")
    name = f"{identity.name} ({identity.worker_id})" if identity else "worker"
    return Check("enrollment", "ok", f"enrolled as {name}")


def check_mount(settings: WorkerSettings, root: Path | None, server_url: str | None) -> Check:
    """Compare the mounted ``library.json`` id with the id the server reports."""
    key = load_api_key(settings)
    if server_url is None or key is None or root is None:
        return Check("mount", "skipped", "needs library root, server URL and enrollment")

    async def fetch() -> UUID | None:
        async with WorkerClient(server_url, key, retries=0, timeout=5.0) as client:
            return (await client.library()).library_id

    try:
        server_id = asyncio.run(fetch())
    except (ApiError, UnavailableError, httpx.HTTPError) as exc:
        return Check("mount", "error", f"cannot ask the server for the library id: {exc}")
    try:
        verified = verify_mount(root, server_id, settings.library_id)
    except MountError as exc:
        return Check("mount", "error", str(exc))
    return Check("mount", "ok", f"mount matches server library {verified}")


def run_checks(
    library: Path | None,
    library_id: UUID | None,
    server_url: str | None,
    settings: WorkerSettings | None = None,
) -> list[Check]:
    """Run all checks in a stable order."""
    cfg = settings or WorkerSettings(_env_file=None)
    checks = [check_binary(name, args, cfg) for name, args in REQUIRED_BINARIES.items()]
    checks.append(check_library(library, library_id))
    checks.append(check_server(server_url))
    checks.append(check_perl())
    checks.extend(check_ffmpeg_features(cfg))
    checks.append(check_data_dir(cfg))
    checks.append(check_enrollment(cfg))
    checks.append(check_mount(cfg, library, server_url))
    return checks
