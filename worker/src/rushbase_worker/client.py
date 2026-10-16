"""Async client for the worker API with retries and exponential backoff."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

import httpx

from rushbase_worker.models import Claim, Enrolled, LibraryInfo, ResultAck

RETRY_STATUS = frozenset({429, 502, 503, 504})
API = "/api/v1/worker"


class ApiError(Exception):
    """The server rejected a request."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class AuthError(ApiError):
    """The API key is missing, invalid or revoked (HTTP 401/403)."""


class LeaseLostError(ApiError):
    """The job's lease expired or was taken over (HTTP 409): stop working on it."""


class UnavailableError(Exception):
    """The server could not be reached or kept failing after all retries."""


class WorkerApi(Protocol):
    """What the runner needs from the server (implemented by :class:`WorkerClient`)."""

    async def library(self) -> LibraryInfo:
        """Return the library id."""
        ...

    async def claim(self, max_jobs: int, capabilities: dict[str, Any]) -> Claim:
        """Lease jobs."""
        ...

    async def heartbeat(self, job_id: UUID) -> None:
        """Extend a lease."""
        ...

    async def result(self, job_id: UUID, body: dict[str, Any]) -> ResultAck:
        """Report a job outcome."""
        ...

    async def release(self, job_id: UUID) -> None:
        """Give a job back."""
        ...


def backoff_delays(attempts: int, base: float = 1.0, cap: float = 30.0) -> list[float]:
    """Delays before each retry: exponential, capped, with up to 25 % jitter."""
    return [min(cap, base * 2**i) * (1 + random.random() * 0.25) for i in range(attempts)]  # noqa: S311


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    return str(body.get("detail", body)) if isinstance(body, dict) else str(body)


class WorkerClient:
    """HTTP client for ``/api/v1/worker/*`` (bearer API key)."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        retries: int = 4,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )
        self._retries = retries
        self._sleep = sleep

    async def __aenter__(self) -> "WorkerClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the connection pool."""
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, retry: bool = True
    ) -> httpx.Response:
        delays = backoff_delays(self._retries) if retry else []
        last: str = ""
        for attempt in range(len(delays) + 1):
            try:
                response = await self._http.request(method, path, json=json)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUS:
                    return self._checked(response)
                last = f"HTTP {response.status_code}: {_detail(response)}"
            if attempt < len(delays):
                await self._sleep(delays[attempt])
        raise UnavailableError(f"{method} {path} failed after retries: {last}")

    @staticmethod
    def _checked(response: httpx.Response) -> httpx.Response:
        code = response.status_code
        if code < 400:
            return response
        if code in (401, 403):
            raise AuthError(code, _detail(response))
        if code == 409:
            raise LeaseLostError(code, _detail(response))
        raise ApiError(code, _detail(response))

    @staticmethod
    async def enroll(base_url: str, payload: dict[str, Any], *, timeout: float = 30.0) -> Enrolled:
        """Exchange a one-time enrollment token for an API key."""
        async with WorkerClient(base_url, timeout=timeout) as client:
            response = await client._request("POST", f"{API}/enroll", json=payload, retry=False)
        return Enrolled.model_validate(response.json())

    async def health(self) -> dict[str, Any]:
        """Return the server's ``/health`` document."""
        response = await self._request("GET", "/health", retry=False)
        body: dict[str, Any] = response.json()
        return body

    async def library(self) -> LibraryInfo:
        """Return the library id."""
        return LibraryInfo.model_validate((await self._request("GET", f"{API}/library")).json())

    async def claim(self, max_jobs: int, capabilities: dict[str, Any]) -> Claim:
        """Lease up to ``max_jobs`` jobs matching ``capabilities``."""
        body = {"max_jobs": max_jobs, "capabilities": capabilities}
        return Claim.model_validate((await self._request("POST", f"{API}/claim", json=body)).json())

    async def heartbeat(self, job_id: UUID) -> None:
        """Extend the lease of ``job_id`` (raises :class:`LeaseLostError` when lost)."""
        await self._request("POST", f"{API}/jobs/{job_id}/heartbeat")

    async def result(self, job_id: UUID, body: dict[str, Any]) -> ResultAck:
        """Report the outcome of ``job_id``."""
        response = await self._request("POST", f"{API}/jobs/{job_id}/result", json=body)
        return ResultAck.model_validate(response.json())

    async def release(self, job_id: UUID) -> None:
        """Give ``job_id`` back without an attempt penalty."""
        await self._request("POST", f"{API}/jobs/{job_id}/release")

    async def status(self, token: str | None = None) -> dict[str, Any]:
        """Fetch the server backlog.

        With a personal API ``token`` the user endpoint ``/api/v1/status`` is used; otherwise the
        client's own worker key reads ``/api/v1/worker/status``.
        """
        if token:
            response = await self._http.get(
                "/api/v1/status", headers={"Authorization": f"Bearer {token}"}
            )
        else:
            response = await self._http.get(f"{API}/status")
        checked = self._checked(response)
        body: dict[str, Any] = checked.json()
        return body
