"""Initialising and opening a library on disk."""

import json
import os
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


def _write_manifest(root: Path, manifest: LibraryManifest) -> None:
    target = manifest_path(root)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


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
    _write_manifest(root, manifest)
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
