"""Library mount verification: ``library.json`` must carry the server's library id."""

from pathlib import Path
from uuid import UUID

from rushbase_core import LibraryError, open_library


class MountError(Exception):
    """The local library mount is missing or belongs to a different library."""


def verify_mount(root: Path | None, server_library_id: UUID | None, expected: UUID | None) -> UUID:
    """Return the verified library id or raise :class:`MountError`.

    The id in ``<root>/.rushbase/library.json`` must equal the id the server reports (and
    ``expected`` when configured). A worker never processes anything from a mismatching mount.
    """
    if root is None:
        raise MountError("no library root configured (RUSHBASE_LIBRARY_ROOT or worker.toml)")
    if server_library_id is None:
        raise MountError("the server has no library bound yet")
    if expected is not None and expected != server_library_id:
        raise MountError(
            f"configured library id {expected} differs from the server's {server_library_id}"
        )
    try:
        manifest = open_library(root, server_library_id)
    except LibraryError as exc:
        raise MountError(str(exc)) from exc
    return manifest.library_id
