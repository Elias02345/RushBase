"""Pure-ASGI middleware: security response headers.

Implemented without ``BaseHTTPMiddleware`` so streaming responses (file ranges, MCP) are
passed through untouched.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Policy for API, OAuth and error responses. The web UI is a separate static bundle, which
#: needs the same sources (images/media from itself, ``data:``/``blob:`` for thumbnails).
DEFAULT_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "font-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'; object-src 'none'"
)
#: Interactive API docs load their assets from a CDN; they carry no CSP of their own.
_NO_CSP_PATHS = ("/docs", "/redoc")

_STATIC_HEADERS = {
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "x-content-type-options": "nosniff",
}


class SecurityHeadersMiddleware:
    """Adds CSP, framing, referrer and sniffing headers unless a route already set them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _STATIC_HEADERS.items():
                    headers.setdefault(name, value)
                if not path.startswith(_NO_CSP_PATHS):
                    headers.setdefault("content-security-policy", DEFAULT_CSP)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLargeError(Exception):
    """Raised inside the receive wrapper once the declared or streamed body exceeds the limit."""


class BodyLimitMiddleware:
    """Rejects request bodies above ``max_bytes`` with 413 (declared and streamed size)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isnumeric() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return
        received = 0
        exceeded = False
        started = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if exceeded:
                # The framework may have turned our exception into a 400: answer 413 instead.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    headers = [(b"content-type", b"application/json")]
                    await send({"type": "http.response.start", "status": 413, "headers": headers})
                elif message["type"] == "http.response.body":
                    body = b'{"detail":"request body too large"}'
                    await send({"type": "http.response.body", "body": body})
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLargeError:
            if started:
                raise
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "request body too large"}, status_code=413)
        await response(scope, receive, send)
