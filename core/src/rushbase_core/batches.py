"""Batch naming: derive label, hint date and the owning batch from an ``Input/`` path."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Final

from rushbase_core.layout import INPUT_DIR

AUTO_BATCH_SEGMENT: Final = "@auto"
"""Virtual path segment of automatic per-day batches for files lying directly in ``Input/``."""

_DATE_PREFIX: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?![0-9])")


@dataclass(frozen=True)
class BatchHint:
    """Which batch a path belongs to and what the folder name reveals about it."""

    input_path: str
    """Library-relative batch folder (a virtual ``Input/@auto/<day>`` for loose files)."""
    label: str
    hint_date: date | None
    auto: bool


def parse_hint_date(name: str) -> date | None:
    """Return the ``YYYY-MM-DD`` prefix of ``name`` as a date, or ``None`` if absent/invalid."""
    match = _DATE_PREFIX.match(name)
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def label_from_folder(name: str) -> str:
    """Folder name without its ``YYYY-MM-DD`` prefix (the date is kept as the hint date).

    ``2026-10-12_Drehtag_Standort-A`` becomes ``Drehtag_Standort-A``; a name that is only a
    date stays unchanged.
    """
    if parse_hint_date(name) is None:
        return name
    rest = name[10:].lstrip(" _-.")
    return rest or name


def batch_for(rel_path: str, today: date) -> BatchHint | None:
    """Return the batch of a library-relative file path below ``Input/``.

    The first sub folder is the batch; files directly in ``Input/`` go to the automatic batch
    of ``today``. Returns ``None`` for paths outside ``Input/``.
    """
    parts = rel_path.split("/")
    if len(parts) < 2 or parts[0] != INPUT_DIR:
        return None
    if len(parts) == 2:
        day = today.isoformat()
        return BatchHint(f"{INPUT_DIR}/{AUTO_BATCH_SEGMENT}/{day}", f"Auto {day}", today, auto=True)
    folder = parts[1]
    return BatchHint(
        f"{INPUT_DIR}/{folder}", label_from_folder(folder), parse_hint_date(folder), auto=False
    )


def batch_root_of(input_path: str) -> str:
    """Return the real directory that grouping uses for a batch (``Input`` for auto batches)."""
    return INPUT_DIR if f"/{AUTO_BATCH_SEGMENT}/" in input_path else input_path
