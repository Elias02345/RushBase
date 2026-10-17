"""Synchronous client for the user-facing REST API (personal API token or worker key)."""

from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from rushbase_worker.config import WorkerSettings
from rushbase_worker.state import load_api_key, load_identity, write_private

TOKEN_FILE = "user_token"
TOKEN_NAME = "rushbase-companion"


class UserApiError(Exception):
    """A user API call failed (``status`` is 0 for transport and configuration errors)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}" if status else detail)
        self.status = status
        self.detail = detail


def token_file(settings: WorkerSettings) -> Path:
    """Where `rushbase login` stores the personal API token."""
    return settings.data_dir / TOKEN_FILE


def load_user_token(settings: WorkerSettings) -> str | None:
    """Personal token from ``RUSHBASE_API_TOKEN`` or the data directory."""
    if settings.api_token:
        return settings.api_token
    try:
        return token_file(settings).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def resolve_server_url(settings: WorkerSettings) -> str | None:
    """Configured server URL, falling back to the enrolled one."""
    identity = load_identity(settings)
    return settings.server_url or (identity.server_url if identity else None)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    return str(body.get("detail", body)) if isinstance(body, dict) else str(body)


class UserApi:
    """Read-only calls the companion needs (`/api/v1/search`, `/assets/{id}`, `/status` ...)."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    @classmethod
    def from_settings(
        cls, settings: WorkerSettings, *, transport: httpx.BaseTransport | None = None
    ) -> Self:
        """Build a client from settings; raises :class:`UserApiError` when unconfigured."""
        url = resolve_server_url(settings)
        token = load_user_token(settings)
        if url is None:
            raise UserApiError(0, "no server configured: run `rushbase enroll` or `rushbase login`")
        if token is None:
            raise UserApiError(0, "no personal API token: run `rushbase login --server URL` first")
        return cls(url, token, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()

    def _send(self, path: str, params: Any = None) -> httpx.Response:
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UserApiError(0, f"server unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise UserApiError(response.status_code, _detail(response))
        return response

    def get(self, path: str, params: list[tuple[str, str]] | None = None) -> Any:
        """GET ``path`` and return the decoded JSON body."""
        return self._send(path, params).json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        """POST ``body`` as JSON and return the decoded JSON response."""
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise UserApiError(0, f"server unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise UserApiError(response.status_code, _detail(response))
        return response.json()

    def get_bytes(self, path: str) -> bytes:
        """GET a binary resource (derived images)."""
        return self._send(path).content

    def search(
        self,
        query: str,
        filters: list[tuple[str, str]] | None = None,
        *,
        level: str = "asset",
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Hybrid search (``level`` is ``asset`` or ``shot``)."""
        params = [("q", query), ("level", level), ("limit", str(limit)), ("offset", str(offset))]
        return dict(self.get("/api/v1/search", params + (filters or [])))

    def asset(self, asset_id: str) -> dict[str, Any]:
        """Full asset detail including files, per-OS paths and shots."""
        return dict(self.get(f"/api/v1/assets/{asset_id}"))

    def facets(self) -> dict[str, Any]:
        """Available filter dimensions."""
        return dict(self.get("/api/v1/facets"))

    def status(self) -> dict[str, Any]:
        """Backlog and worker counters (personal token)."""
        return dict(self.get("/api/v1/status"))


def fetch_backlog(
    settings: WorkerSettings, *, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Backlog via the personal token, else via the enrolled worker key."""
    url = resolve_server_url(settings)
    if url is None:
        raise UserApiError(0, "no server configured")
    token = load_user_token(settings)
    if token:
        with UserApi(url, token, transport=transport) as api:
            return api.status()
    key = load_api_key(settings)
    if key is None:
        raise UserApiError(0, "not enrolled and no personal token")
    with UserApi(url, key, transport=transport) as api:
        return dict(api.get("/api/v1/worker/status"))


def report_usage(
    settings: WorkerSettings,
    project_name: str,
    machine: str,
    items: list[dict[str, Any]],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Send a usage report; personal token first (editor), else the enrolled worker key."""
    url = resolve_server_url(settings)
    if url is None:
        raise UserApiError(0, "no server configured: run `rushbase enroll` or `rushbase login`")
    body = {"project_name": project_name, "machine": machine, "items": items}
    token = load_user_token(settings)
    if token:
        with UserApi(url, token, transport=transport) as api:
            return dict(api.post("/api/v1/usage/report", body))
    key = load_api_key(settings)
    if key is None:
        raise UserApiError(0, "not enrolled and no personal token")
    with UserApi(url, key, transport=transport) as api:
        return dict(api.post("/api/v1/worker/usage", body))


def login_and_create_token(
    server: str,
    username: str,
    password: str,
    *,
    name: str = TOKEN_NAME,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Sign in with username/password, create a personal API token and sign out again."""
    with httpx.Client(base_url=server.rstrip("/"), timeout=20.0, transport=transport) as http:
        try:
            body = {"username": username, "password": password}
            login = http.post("/api/v1/auth/login", json=body)
            if login.status_code >= 400:
                raise UserApiError(login.status_code, _detail(login))
            headers = {"X-CSRF-Token": str(login.json().get("csrf_token", ""))}
            payload = {"name": name, "scopes": []}
            created = http.post("/api/v1/tokens", json=payload, headers=headers)
            if created.status_code >= 400:
                raise UserApiError(created.status_code, _detail(created))
            secret = str(created.json()["token"])
            http.post("/api/v1/auth/logout", headers=headers)
        except httpx.HTTPError as exc:
            raise UserApiError(0, f"server unreachable: {exc}") from exc
    return secret


def save_user_token(settings: WorkerSettings, token: str) -> Path:
    """Store the token with mode 600 and return its path."""
    path = token_file(settings)
    write_private(path, token + "\n")
    return path
