"""Pure-ASGI middleware: security response headers.

Implemented without ``BaseHTTPMiddleware`` so streaming responses (file ranges, MCP) are
passed through untouched.
"""

from starlette.datastructures import MutableHeaders
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
