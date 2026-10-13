"""Safe file operations for the single writer of ``Content/`` (ADR-0005).

* :func:`safe_rename` never overwrites and reports "file in use" distinctly so callers can retry.
* :func:`atomic_write_bytes` writes to ``.<name>.rbtmp`` in the same directory, fsyncs and renames.
* :func:`append_line` appends a durable line (journals).

Only the server uses these on ``Content/``; the primitives live in the core so that the
rebuild tooling and tests share them.
"""

import errno
import os
from pathlib import Path
from typing import Final

IN_USE_ERRNOS: Final = frozenset({errno.EACCES, errno.EBUSY, errno.EPERM})
"""Errors that mean "open elsewhere / not permitted right now": retry later, never overwrite."""
TEMP_SUFFIX: Final = ".rbtmp"


class FileInUseError(OSError):
    """The file or directory is held open by another client or not accessible right now."""


def lexists(path: Path) -> bool:
    """``True`` when ``path`` exists (also as a dangling link), without following links."""
    return os.path.lexists(path)


def safe_rename(source: Path, target: Path) -> None:
    """Rename ``source`` to ``target`` (file or directory), creating missing parent directories.

    Raises:
        FileExistsError: ``target`` already exists; nothing is overwritten.
        FileInUseError: ``EACCES``/``EBUSY``/``EPERM`` from the file system (retry later).
    """
    if lexists(target):
        raise FileExistsError(errno.EEXIST, "target exists", str(target))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, target)
    except OSError as exc:
        if exc.errno in IN_USE_ERRNOS:
            raise FileInUseError(exc.errno, f"{source} is in use: {exc.strerror}") from exc
        raise


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # some network file systems refuse; the rename itself is already done
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temporary file in the same directory (overwrites)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}{TEMP_SUFFIX}")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        if exc.errno in IN_USE_ERRNOS:
            raise FileInUseError(exc.errno, f"{path} is in use: {exc.strerror}") from exc
        raise
    _fsync_directory(path.parent)


def append_line(path: Path, line: str) -> None:
    """Append ``line`` (plus newline) to ``path`` and fsync; creates the file and parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
