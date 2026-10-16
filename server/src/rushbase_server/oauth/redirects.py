"""Redirect URI policy: exact-match allowlist, loopback port-agnostic (ADR-0010)."""

from collections.abc import Iterable
from urllib.parse import urlsplit

BUILTIN_REDIRECTS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback(uri: str) -> bool:
    """``http://127.0.0.1:<any>/...`` or ``http://localhost:<any>/...`` (no fragment)."""
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        return False
    return (
        parts.scheme == "http"
        and parts.hostname in LOOPBACK_HOSTS
        and not parts.fragment
        and not parts.username
    )


def _loopback_key(uri: str) -> tuple[str, str, str, str]:
    parts = urlsplit(uri)
    return (parts.scheme, parts.hostname or "", parts.path, parts.query)


def is_allowed(uri: str, extra: Iterable[str] = ()) -> bool:
    """Whether ``uri`` may be registered: built-in, admin-configured or loopback."""
    return uri in BUILTIN_REDIRECTS or uri in set(extra) or is_loopback(uri)


def matches(requested: str, registered: Iterable[str]) -> bool:
    """Exact match against a registered URI; loopback URIs ignore the port only."""
    for candidate in registered:
        if requested == candidate:
            return True
        if is_loopback(requested) and is_loopback(candidate):
            if _loopback_key(requested) == _loopback_key(candidate):
                return True
    return False


def host_of(uri: str) -> str:
    """The host (and port) shown on the consent page."""
    return urlsplit(uri).netloc
