"""Safe file operations for the single writer of ``Content/`` (ADR-0005).

* :func:`safe_rename` never overwrites and reports "file in use" distinctly so callers can retry.
* :func:`atomic_write_bytes` writes to ``.<name>.<random>.rbtmp`` (created exclusively, links not
  followed) in the same directory, fsyncs and renames.
* :func:`append_line` appends a durable line (journals).

Only the server uses these on ``Content/``; the primitives live in the core so that the
rebuild tooling and tests share them.
"""

import ctypes
import errno
import os
import secrets
import sys
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


_AT_FDCWD: Final = -100
_RENAME_NOREPLACE: Final = 1
_UNSUPPORTED_ERRNOS: Final = frozenset({errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EXDEV})


def _renameat2_noreplace(source: Path, target: Path) -> bool:
    """Atomic no-overwrite rename via ``renameat2``; ``False`` when the platform lacks it."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        return False
    result = function(
        _AT_FDCWD, os.fsencode(source), _AT_FDCWD, os.fsencode(target), _RENAME_NOREPLACE
    )
    if result == 0:
        return True
    code = ctypes.get_errno()
    if code in _UNSUPPORTED_ERRNOS:
        return False
    raise OSError(code, os.strerror(code), str(source))


def _link_noreplace(source: Path, target: Path) -> bool:
    """No-overwrite move of a regular file: ``link`` fails if ``target`` exists, then unlink."""
    if source.is_symlink() or not source.is_file():
        return False
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_ERRNOS | {errno.EPERM, errno.EMLINK}:
            return False  # file system without hard links (e.g. some SMB mounts)
        raise
    os.unlink(source)
    return True


def safe_rename(source: Path, target: Path) -> None:
    """Rename ``source`` to ``target`` (file or directory) without ever overwriting.

    Creates missing parent directories. The no-overwrite guarantee is atomic where the platform
    allows it (``renameat2(RENAME_NOREPLACE)``, else ``link`` + ``unlink`` for files); the last
    resort is an existence check followed by ``rename`` (a race remains only there).

    Raises:
        FileExistsError: ``target`` already exists; nothing is overwritten.
        FileInUseError: ``EACCES``/``EBUSY``/``EPERM`` from the file system (retry later).
    """
    if lexists(target):
        raise FileExistsError(errno.EEXIST, "target exists", str(target))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if _renameat2_noreplace(source, target) or _link_noreplace(source, target):
            return
        if lexists(target):
            raise FileExistsError(errno.EEXIST, "target exists", str(target))
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
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}{TEMP_SUFFIX}")
    try:
        # O_EXCL | O_NOFOLLOW: never write through a planted file or symlink.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(temp, flags, 0o644), "wb") as handle:
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
