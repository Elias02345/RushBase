"""Directory layout of a Rushbase library (see the product specification, section 3)."""

from typing import Final

INPUT_DIR: Final = "Input"
CONTENT_DIR: Final = "Content"
SYSTEM_DIR: Final = ".rushbase"
LIBRARY_JSON: Final = "library.json"

DERIVED_DIR: Final = "derived"
META_DIR: Final = "meta"
BACKUPS_DIR: Final = "backups"
QUARANTINE_DIR: Final = "quarantine"
TRASH_DIR: Final = "trash"
JOURNAL_DIR: Final = "journal"

SYSTEM_SUBDIRS: Final = (
    DERIVED_DIR,
    META_DIR,
    BACKUPS_DIR,
    QUARANTINE_DIR,
    TRASH_DIR,
    JOURNAL_DIR,
)
"""Subdirectories of ``.rushbase/`` created when a library is initialised."""
