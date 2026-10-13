"""Writing derived files: always temp file in the same directory, then atomic rename."""

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

from rushbase_core.results import DerivedFile

MIME_TYPES = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".json": "application/json",
}


def temp_name(final: Path) -> Path:
    """Sibling temp path that keeps the extension (``.proxy.rbtmp.mp4``) so tools infer formats."""
    return final.with_name(f".{final.stem}.rbtmp{final.suffix}")


@contextlib.contextmanager
def atomic_output(final: Path) -> Iterator[Path]:
    """Yield a temp path; on success it is renamed to ``final``, on error it is removed."""
    tmp = temp_name(final)
    tmp.unlink(missing_ok=True)
    try:
        yield tmp
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def describe(
    kind: str, final: Path, rel_path: str, width: int | None = None, height: int | None = None
) -> DerivedFile:
    """Build the :class:`DerivedFile` entry for a written file."""
    return DerivedFile.model_validate(
        {
            "kind": kind,
            "rel_path": rel_path,
            "width": width,
            "height": height,
            "size": final.stat().st_size,
            "mime": MIME_TYPES.get(final.suffix.lower()),
        }
    )
