"""Server lease stored in ``library.json``: at most one coordinator per library.

The lease is a holder id plus an expiry. The holder renews it periodically (default every
30 s, expiry 120 s). A second server refuses to start while the lease is held by a live
holder; ``force=True`` (``--force-takeover``) overrides it explicitly.

SMB offers atomic rename but no compare-and-swap, so acquisition is best effort: after
writing, the manifest is read back and the claim is dropped if somebody else won the race.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from rushbase_core.library import LibraryError, open_library, write_manifest
from rushbase_core.models import ServerLease

DEFAULT_LEASE_SECONDS: Final = 120
DEFAULT_HEARTBEAT_SECONDS: Final = 30


class ServerLeaseHeldError(LibraryError):
    """Another live server holds the library lease."""

    def __init__(self, holder: str, expires_at: datetime) -> None:
        super().__init__(
            f"library is managed by server {holder!r} (lease valid until {expires_at.isoformat()});"
            " stop it, wait for the lease to expire or use --force-takeover"
        )
        self.holder = holder
        self.expires_at = expires_at


class ServerLeaseLostError(LibraryError):
    """The lease this process held was taken over by another holder."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def acquire_server_lease(
    root: Path,
    holder: str,
    *,
    ttl_seconds: float = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
    force: bool = False,
) -> ServerLease:
    """Take (or renew) the lease for ``holder``.

    Raises:
        ServerLeaseHeldError: another holder's lease is still valid and ``force`` is false.
    """
    moment = _now(now)
    manifest = open_library(root)
    current = manifest.server_lease
    if (
        current is not None
        and current.holder != holder
        and not current.is_expired(moment)
        and not force
    ):
        raise ServerLeaseHeldError(current.holder, current.expires_at)
    lease = ServerLease(holder=holder, expires_at=moment + timedelta(seconds=ttl_seconds))
    write_manifest(root, manifest.model_copy(update={"server_lease": lease}))
    confirmed = open_library(root).server_lease
    if confirmed is None or confirmed.holder != holder:
        raise ServerLeaseLostError("lost the race for the library lease")
    return lease


def renew_server_lease(
    root: Path,
    holder: str,
    *,
    ttl_seconds: float = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> ServerLease:
    """Extend the lease of ``holder``; raises :class:`ServerLeaseLostError` if it was taken."""
    moment = _now(now)
    current = open_library(root).server_lease
    if current is not None and current.holder != holder and not current.is_expired(moment):
        raise ServerLeaseLostError(f"library lease now belongs to {current.holder!r}")
    return acquire_server_lease(root, holder, ttl_seconds=ttl_seconds, now=moment)


def release_server_lease(root: Path, holder: str) -> None:
    """Drop the lease if ``holder`` still owns it (graceful shutdown). Never raises for others."""
    manifest = open_library(root)
    lease = manifest.server_lease
    if lease is not None and lease.holder == holder:
        write_manifest(root, manifest.model_copy(update={"server_lease": None}))
