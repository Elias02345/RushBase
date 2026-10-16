"""Client ID Metadata Documents (CIMD): an https ``client_id`` URL is fetched SSRF-safely.

Rules: https only (default port), no credentials, the hostname must resolve exclusively to
public addresses (private, loopback, link-local, multicast, reserved, CGNAT and IPv4-mapped IPv6
forms are refused), the connection goes to the resolved address (no second lookup, so DNS
rebinding cannot swap it), redirects are never followed, 5 s total timeout, 64 KiB body cap.
"""

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_BODY_BYTES = 64 * 1024
TIMEOUT_SECONDS = 5.0
MIN_CACHE_SECONDS = 300
MAX_CACHE_SECONDS = 86_400
DEFAULT_CACHE_SECONDS = 3600

Resolver = Callable[[str], Awaitable[list[str]]]
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class CimdError(Exception):
    """The metadata document could not be fetched or is invalid."""


@dataclass(frozen=True)
class ClientMetadata:
    """The validated parts of a metadata document."""

    client_id: str
    name: str
    redirect_uris: list[str]
    scopes: list[str]
    cache_seconds: int
    etag: str | None
    not_modified: bool = False


def is_cimd_client_id(client_id: str) -> bool:
    """A client_id that is an https URL selects the CIMD flow."""
    return client_id.startswith("https://")


def address_allowed(value: str) -> bool:
    """Whether a resolved address is a public unicast address."""
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped or address.sixtofour
        if mapped is not None:
            return address_allowed(str(mapped))
        embedded = ipaddress.ip_address(int(address) & 0xFFFFFFFF)
        if address in ipaddress.ip_network("64:ff9b::/96"):  # NAT64 embeds an IPv4 address
            return address_allowed(str(embedded))
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return False
    return address.is_global and not address.is_multicast


async def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` with the system resolver (A and AAAA)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CimdError(f"cannot resolve {host}") from exc
    return sorted({str(info[4][0]) for info in infos})


def _validate_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise CimdError("client_id URL must be https")
    if parts.username or parts.password or parts.fragment:
        raise CimdError("client_id URL must not carry credentials or a fragment")
    if parts.port not in (None, 443):
        raise CimdError("client_id URL must use the default https port")
    if not parts.path or parts.path == "/":
        raise CimdError("client_id URL needs a path")
    if parts.path.split("/")[-1] in {".", ".."} or "/../" in parts.path:
        raise CimdError("client_id URL must not contain dot segments")
    return parts.hostname, parts.path + (f"?{parts.query}" if parts.query else "")


def _cache_seconds(headers: httpx.Headers) -> int:
    control = headers.get("cache-control", "")
    for directive in control.split(","):
        name, _, value = directive.strip().partition("=")
        if name.lower() in {"no-store", "no-cache"}:
            return MIN_CACHE_SECONDS
        if name.lower() == "max-age" and value.isdigit():
            return min(max(int(value), MIN_CACHE_SECONDS), MAX_CACHE_SECONDS)
    return DEFAULT_CACHE_SECONDS


def _parse(url: str, body: bytes, headers: httpx.Headers) -> ClientMetadata:
    try:
        data: Any = json.loads(body)
    except ValueError as exc:
        raise CimdError("metadata document is not JSON") from exc
    if not isinstance(data, dict) or data.get("client_id") != url:
        raise CimdError("client_id in the document does not equal its URL")
    uris = data.get("redirect_uris")
    if not (isinstance(uris, list) and uris and all(isinstance(u, str) for u in uris)):
        raise CimdError("redirect_uris missing")
    if data.get("token_endpoint_auth_method", "none") != "none":
        raise CimdError("only public clients (token_endpoint_auth_method none) are supported")
    if any(key in data for key in ("client_secret", "client_secret_expires_at")):
        raise CimdError("metadata documents must not contain secrets")
    name = data.get("client_name")
    scope = data.get("scope", "")
    return ClientMetadata(
        client_id=url,
        name=(name if isinstance(name, str) and name.strip() else urlsplit(url).hostname or url)[
            :120
        ],
        redirect_uris=[str(u) for u in uris][:20],
        scopes=scope.split() if isinstance(scope, str) else [],
        cache_seconds=_cache_seconds(headers),
        etag=headers.get("etag"),
    )


async def fetch_client_metadata(
    url: str,
    *,
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
    etag: str | None = None,
) -> ClientMetadata:
    """Fetch and validate the metadata document at ``url`` (raises :class:`CimdError`)."""
    host, target = _validate_url(url)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CimdError("client_id URL must use a host name, not an address")
    addresses = await resolver(host)
    if not addresses or not all(address_allowed(a) for a in addresses):
        raise CimdError("client_id host resolves to a non-public address")
    address = addresses[0]
    netloc = f"[{address}]" if ":" in address else address
    headers = {"Host": host, "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=transport or httpx.AsyncHTTPTransport(retries=0),
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                request = client.build_request(
                    "GET",
                    f"https://{netloc}{target}",
                    headers=headers,
                    extensions={"sni_hostname": host},
                )
                response = await client.send(request, stream=True)
                try:
                    return await _read(url, response, etag)
                finally:
                    await response.aclose()
    except (TimeoutError, httpx.HTTPError) as exc:
        raise CimdError(f"metadata fetch failed: {type(exc).__name__}") from exc


async def _read(url: str, response: httpx.Response, etag: str | None) -> ClientMetadata:
    if response.status_code == 304 and etag:
        return ClientMetadata(
            url, "", [], [], _cache_seconds(response.headers), etag, not_modified=True
        )
    if response.status_code != 200:
        raise CimdError(f"metadata fetch returned HTTP {response.status_code}")
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise CimdError("metadata document too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise CimdError("metadata document too large")
        chunks.append(chunk)
    return _parse(url, b"".join(chunks), response.headers)
