import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from rushbase_core import (
    CONTENT_DIR,
    INPUT_DIR,
    SCHEMA_VERSION,
    SYSTEM_DIR,
    SYSTEM_SUBDIRS,
    LibraryManifest,
    LibraryMismatchError,
    LibraryNotFoundError,
    ServerLease,
    UnsupportedSchemaError,
    init_library,
    open_library,
)


def test_init_creates_layout(tmp_path: Path) -> None:
    manifest = init_library(tmp_path / "lib")
    root = tmp_path / "lib"
    assert (root / INPUT_DIR).is_dir()
    assert (root / CONTENT_DIR).is_dir()
    for name in SYSTEM_SUBDIRS:
        assert (root / SYSTEM_DIR / name).is_dir()
    assert manifest.schema_version == SCHEMA_VERSION
    assert manifest.server_lease is None


def test_init_is_idempotent_and_keeps_id(tmp_path: Path) -> None:
    first = init_library(tmp_path)
    (tmp_path / CONTENT_DIR / "keep.txt").write_text("x")
    second = init_library(tmp_path)
    assert first.library_id == second.library_id
    assert (tmp_path / CONTENT_DIR / "keep.txt").read_text() == "x"


def test_open_checks_expected_id(tmp_path: Path) -> None:
    manifest = init_library(tmp_path)
    assert open_library(tmp_path, manifest.library_id) == manifest
    with pytest.raises(LibraryMismatchError):
        open_library(tmp_path, uuid4())


def test_open_missing_library(tmp_path: Path) -> None:
    with pytest.raises(LibraryNotFoundError):
        open_library(tmp_path)


def test_open_malformed_json(tmp_path: Path) -> None:
    init_library(tmp_path)
    (tmp_path / SYSTEM_DIR / "library.json").write_text("{nope")
    with pytest.raises(LibraryNotFoundError, match="not valid JSON"):
        open_library(tmp_path)


def test_open_invalid_manifest(tmp_path: Path) -> None:
    init_library(tmp_path)
    (tmp_path / SYSTEM_DIR / "library.json").write_text(json.dumps({"library_id": "nope"}))
    with pytest.raises(LibraryNotFoundError, match="not a valid library manifest"):
        open_library(tmp_path)


def test_open_rejects_newer_schema(tmp_path: Path) -> None:
    init_library(tmp_path)
    data = {"library_id": str(uuid4()), "schema_version": SCHEMA_VERSION + 1}
    (tmp_path / SYSTEM_DIR / "library.json").write_text(json.dumps(data))
    with pytest.raises(UnsupportedSchemaError):
        open_library(tmp_path)


def test_manifest_round_trips_lease() -> None:
    expires = datetime(2030, 1, 1, tzinfo=UTC)
    manifest = LibraryManifest(server_lease=ServerLease(holder="srv-1", expires_at=expires))
    again = LibraryManifest.model_validate_json(manifest.model_dump_json())
    assert again == manifest


def test_lease_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        ServerLease(holder="srv", expires_at=datetime(2030, 1, 1))


def test_lease_expiry() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    lease = ServerLease(holder="srv", expires_at=now + timedelta(minutes=1))
    assert not lease.is_expired(now)
    assert lease.is_expired(now + timedelta(minutes=1))
