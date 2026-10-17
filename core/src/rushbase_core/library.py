"""Initialising and opening a library on disk."""

import json
import os
import secrets
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from rushbase_core.layout import (
    CONTENT_DIR,
    INPUT_DIR,
    LIBRARY_JSON,
    SYSTEM_DIR,
    SYSTEM_SUBDIRS,
)
from rushbase_core.models import SCHEMA_VERSION, LibraryManifest


class LibraryError(Exception):
    """Base class for library problems."""


class LibraryNotFoundError(LibraryError):
    """``library.json`` is missing, unreadable or malformed (wrong mount?)."""


class LibraryMismatchError(LibraryError):
    """The mounted library is not the one that was expected."""


class UnsupportedSchemaError(LibraryError):
    """``library.json`` was written by a newer, incompatible release."""


def manifest_path(root: Path) -> Path:
    """Return the location of ``library.json`` below ``root``."""
    return root / SYSTEM_DIR / LIBRARY_JSON


def atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` via ``.<name>.rbtmp`` in the same directory, fsync, rename.

    This is the write pattern for every file Rushbase creates on the share (ADR-0005): other
    clients never observe a partially written file under its final name.
    """
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.rbtmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(tmp, flags, 0o644), "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY)
    except OSError:  # directory fsync is not supported everywhere (e.g. some network mounts)
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def write_manifest(root: Path, manifest: LibraryManifest) -> None:
    """Atomically persist ``manifest`` as ``.rushbase/library.json``."""
    atomic_write_text(manifest_path(root), manifest.model_dump_json(indent=2) + "\n")


def init_library(root: Path) -> LibraryManifest:
    """Create the library layout below ``root`` and return its manifest.

    Idempotent: an already initialised library is left untouched and its existing manifest
    is returned, so the library id never changes.
    """
    root.mkdir(parents=True, exist_ok=True)
    for name in (INPUT_DIR, CONTENT_DIR):
        (root / name).mkdir(exist_ok=True)
    system = root / SYSTEM_DIR
    system.mkdir(exist_ok=True)
    for name in SYSTEM_SUBDIRS:
        (system / name).mkdir(exist_ok=True)
    if manifest_path(root).exists():
        return open_library(root)
    manifest = LibraryManifest()
    write_manifest(root, manifest)
    return manifest


def open_library(root: Path, expected_id: UUID | None = None) -> LibraryManifest:
    """Read and validate ``library.json``.

    Raises:
        LibraryNotFoundError: the manifest is missing, unreadable or malformed.
        UnsupportedSchemaError: the schema version is newer than this release supports.
        LibraryMismatchError: ``expected_id`` is given and differs from the library id.
    """
    path = manifest_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LibraryNotFoundError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise LibraryNotFoundError(f"{path} is not valid JSON: {exc}") from exc
    try:
        manifest = LibraryManifest.model_validate(raw)
    except ValidationError as exc:
        raise LibraryNotFoundError(f"{path} is not a valid library manifest: {exc}") from exc
    if manifest.schema_version > SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"library schema {manifest.schema_version} is newer than supported {SCHEMA_VERSION}"
        )
    if expected_id is not None and manifest.library_id != expected_id:
        raise LibraryMismatchError(
            f"expected library {expected_id}, found {manifest.library_id} at {root}"
        )
    return manifest
