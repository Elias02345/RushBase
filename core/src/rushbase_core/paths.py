"""Library-relative path handling and per-OS root mapping.

Paths are always stored relative to the library root using ``/`` separators. Every
machine configures where the root is mounted locally.
"""

import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict


class PathOutsideLibraryError(ValueError):
    """Raised when a path escapes (or does not belong to) the library root."""


def _normalize(parts: tuple[str, ...]) -> str:
    stack: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise PathOutsideLibraryError("relative path escapes the library root")
            stack.pop()
        else:
            stack.append(part)
    return "/".join(stack)


def relative_to_root(root: Path, path: Path) -> str:
    """Return ``path`` as a ``/``-separated string relative to ``root``.

    Raises :class:`PathOutsideLibraryError` when ``path`` is not below ``root``.
    """
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PathOutsideLibraryError(f"{path} is not inside {root}") from exc
    return _normalize(relative.parts)


def resolve_in_root(root: Path, relative: str) -> Path:
    """Join a stored relative path onto the local ``root``.

    Absolute paths, drive letters and ``..`` segments that would leave the root are rejected.
    Stored paths are ``/``-separated: a backslash (or NUL) is part of a POSIX file name and is
    rejected rather than interpreted as a separator, so two names can never alias one path.
    """
    if "\\" in relative or "\x00" in relative:
        raise PathOutsideLibraryError(f"invalid library path {relative!r}")
    if relative.startswith("/") or PureWindowsPath(relative).drive:
        raise PathOutsideLibraryError(f"expected a relative path, got {relative!r}")
    normalized = _normalize(PurePosixPath(relative).parts)
    return root.joinpath(*normalized.split("/")) if normalized else root


class PlatformRoots(BaseModel):
    """Where the library root is mounted on each operating system."""

    model_config = ConfigDict(frozen=True)

    windows: str | None = None
    linux: str | None = None
    macos: str | None = None

    def for_platform(self, platform: str | None = None) -> Path | None:
        """Return the configured root for ``platform`` (``sys.platform`` by default)."""
        value = getattr(self, _platform_key(platform or sys.platform))
        return Path(value) if value else None


def _platform_key(platform: str) -> str:
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    return "linux"
