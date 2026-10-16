"""Metadata CSV for DaVinci Resolve (Media Pool > Import Metadata from File), ADR-0002."""

import csv
import io
from collections.abc import Callable
from pathlib import Path

from rushbase_worker.companion.clips import Clip

DEFAULT_COLUMNS = (
    "File Name",
    "Clip Directory",
    "Description",
    "Keywords",
    "Comments",
    "Scene",
    "Shot",
    "Take",
    "Good Take",
)
"""Header names as Resolve writes them with `ExportMetadata`; override with ``--columns``."""

PathFn = Callable[[Clip], Path | None]


def _comments(clip: Clip) -> str:
    notes = [f"rushbase:{clip.asset_id}"]
    notes += [s.description for s in clip.selected_shots() if s.description and clip.shots]
    return " | ".join(notes)


def _shot_size(clip: Clip) -> str:
    sizes = {s.shot_size for s in clip.selected_shots() if s.shot_size}
    return next(iter(sizes)) if len(sizes) == 1 else ""


def column_value(column: str, clip: Clip, path: Path | None) -> str:
    """Cell content for one Resolve metadata column (unknown columns stay empty)."""
    if column == "File Name":
        return clip.filename
    if column == "Clip Directory":
        return str(path.parent) if path else ""
    if column == "Description":
        return clip.description or clip.title or ""
    if column == "Keywords":
        return ", ".join(clip.keywords)
    if column == "Comments":
        return _comments(clip)
    if column == "Shot":
        return _shot_size(clip)
    return ""


def export_csv(
    clips: list[Clip], resolve_path: PathFn, columns: tuple[str, ...] = DEFAULT_COLUMNS
) -> str:
    """One row per asset with the requested ``columns`` (UTF-8 text, CRLF line ends)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for clip in clips:
        path = resolve_path(clip)
        writer.writerow([column_value(c, clip, path) for c in columns])
    return buffer.getvalue()
