"""Ignore rules for files and directories that must never be ingested."""

from fnmatch import fnmatchcase
from pathlib import PurePath
from typing import Final

IGNORED_FILE_PATTERNS: Final = (
    "._*",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.part",
    "*.tmp",
    "~$*",
)
IGNORED_DIR_NAMES: Final = frozenset({"@eaDir", "#recycle", ".snapshot"})

_LOWER_FILE_PATTERNS: Final = tuple(pattern.lower() for pattern in IGNORED_FILE_PATTERNS)
_LOWER_DIR_NAMES: Final = frozenset(name.lower() for name in IGNORED_DIR_NAMES)


def is_ignored(path: str | PurePath) -> bool:
    """Return whether ``path`` (relative, ``/`` or ``\\`` separated) must be ignored.

    A path is ignored when its final component matches a junk-file pattern, or when
    any component is an ignored directory name (so everything below it is ignored too).
    Matching is case-insensitive because SMB shares and Windows/macOS clients are.
    """
    parts = [p.lower() for p in str(path).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return False
    if any(part in _LOWER_DIR_NAMES for part in parts):
        return True
    return any(fnmatchcase(parts[-1], pattern) for pattern in _LOWER_FILE_PATTERNS)
