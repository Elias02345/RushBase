"""Resumable, verified downloads (HTTP Range, SHA-256, atomic rename)."""

import contextlib
import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

CHUNK = 1 << 20
PROGRESS_INTERVAL = 1.0
RETRIES = 4

ProgressSink = Callable[[dict[str, object]], None]


class DownloadError(Exception):
    """A download failed permanently (checksum mismatch, HTTP error after retries)."""


def sha256_file(path: Path, on_chunk: Callable[[int], None] | None = None) -> str:
    """SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            if on_chunk:
                on_chunk(len(chunk))
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    *,
    sha256: str,
    size: int,
    label: str,
    emit: ProgressSink = lambda _event: None,
    client: httpx.Client | None = None,
) -> None:
    """Download ``url`` to ``dest`` unless it is already there and correct.

    Data goes to ``<dest>.part`` and is resumed with a Range request after interruptions; the
    final file appears only after the SHA-256 matched (atomic ``os.replace``).
    """
    if dest.is_file() and dest.stat().st_size == size:
        if sha256_file(dest) == sha256:
            emit({"event": "download_skipped", "file": label, "reason": "present"})
            return
        emit({"event": "download_replaced", "file": label, "reason": "checksum mismatch"})
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    owned = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0))
    try:
        _fetch_with_retries(http, url, part, size, label, emit)
    finally:
        if owned:
            http.close()
    emit({"event": "verify_started", "file": label})
    actual = sha256_file(part)
    if actual != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{label}: SHA-256 mismatch (expected {sha256}, got {actual})")
    os.replace(part, dest)
    emit({"event": "download_done", "file": label, "bytes": size})


def _fetch_with_retries(
    http: httpx.Client, url: str, part: Path, size: int, label: str, emit: ProgressSink
) -> None:
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            _fetch(http, url, part, size, label, emit)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc
            emit({"event": "download_retry", "file": label, "attempt": attempt, "error": str(exc)})
            time.sleep(min(2.0**attempt, 20.0))
        else:
            return
    raise DownloadError(f"{label}: download failed after {RETRIES} attempts: {last}")


def _fetch(
    http: httpx.Client, url: str, part: Path, size: int, label: str, emit: ProgressSink
) -> None:
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have == size:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    with http.stream("GET", url, headers=headers) as response:
        if response.status_code == httpx.codes.REQUESTED_RANGE_NOT_SATISFIABLE:
            part.unlink(missing_ok=True)
            raise httpx.TransportError("server rejected the range; restarting")
        response.raise_for_status()
        resumed = response.status_code == httpx.codes.PARTIAL_CONTENT
        mode = "ab" if resumed and have else "wb"
        done = have if mode == "ab" else 0
        last_emit = 0.0
        emit({"event": "download_started", "file": label, "total": size, "resume_from": done})
        with part.open(mode) as handle:
            for chunk in response.iter_bytes(CHUNK):
                handle.write(chunk)
                done += len(chunk)
                if done > size:
                    raise DownloadError(f"{label}: server sent more than {size} bytes")
                now = time.monotonic()
                if now - last_emit >= PROGRESS_INTERVAL:
                    last_emit = now
                    emit(
                        {"event": "download_progress", "file": label, "bytes": done, "total": size}
                    )
    if part.stat().st_size != size:
        raise httpx.TransportError(f"{label}: incomplete ({part.stat().st_size} of {size} bytes)")


def remove_quietly(path: Path) -> None:
    """Delete a file, ignoring races."""
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
