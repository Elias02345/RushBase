"""Derived files (``.rushbase/derived/<asset-id>/``): path validation, URLs and safe resolution.

Workers write previews only below ``.rushbase/derived/<asset-id>/``; the server never serves
anything outside that directory (originals are unreachable by construction).
"""

from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from rushbase_core import SYSTEM_DIR
from rushbase_core.layout import DERIVED_DIR

URL_PREFIX = "/api/v1/derived"
#: Only these file types are ever served; anything else (``.html``, ``.svg``, ...) is a 404.
SERVED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".mp4", ".m4a", ".webm", ".json"})


class DerivedPathError(ValueError):
    """A derived path is malformed or points outside the asset's derived directory."""


def derived_prefix(asset_id: UUID) -> str:
    """Library-relative directory (with trailing slash) that holds the asset's derived files."""
    return f"{SYSTEM_DIR}/{DERIVED_DIR}/{asset_id}/"


def _check_segments(path: str) -> list[str]:
    if not path or "\x00" in path or "\\" in path or path.startswith("/"):
        raise DerivedPathError(f"invalid derived path {path!r}")
    segments = path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise DerivedPathError(f"invalid derived path {path!r}")
    return segments


def check_derived_rel_path(asset_id: UUID, rel_path: str) -> str:
    """Validate a library-relative derived path of ``asset_id``; returns the part below its dir.

    Raises:
        DerivedPathError: the path is not strictly below ``.rushbase/derived/<asset-id>/``.
    """
    prefix = derived_prefix(asset_id)
    if not rel_path.startswith(prefix):
        raise DerivedPathError(f"{rel_path!r} is outside {prefix}")
    _check_segments(rel_path)
    return rel_path[len(prefix) :]


def derived_url(asset_id: UUID, rel_path: str) -> str | None:
    """URL (served by ``GET /api/v1/derived/...``) of a derived file, or ``None`` if invalid."""
    try:
        sub = check_derived_rel_path(asset_id, rel_path)
    except DerivedPathError:
        return None
    return f"{URL_PREFIX}/{asset_id}/{quote(sub)}"


def resolve_derived(root: Path, asset_id: UUID, sub_path: str) -> Path:
    """Map a request path to an existing regular file inside the asset's derived directory.

    Raises:
        DerivedPathError: traversal attempt, symlink escape, or not a regular file.
    """
    segments = _check_segments(sub_path)
    if Path(segments[-1]).suffix.lower() not in SERVED_EXTENSIONS:
        raise DerivedPathError("not found")
    base = (root / SYSTEM_DIR / DERIVED_DIR / str(asset_id)).resolve()
    candidate = (base / sub_path).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        raise DerivedPathError("not found")
    return candidate
