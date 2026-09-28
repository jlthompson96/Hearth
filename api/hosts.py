"""Answer only requests addressed to this machine by name.

uvicorn listens on 127.0.0.1, which keeps other machines out but not other web
pages. A page on any site can re-point its own domain at 127.0.0.1 — DNS
rebinding — and the browser then treats this API as that page's own origin:
no CORS check applies, and it can read the thread history and the Model log,
which holds every prompt with its balances. The request still carries the
page's domain in its Host header, and that is what this refuses.

Written here rather than taken from Starlette's TrustedHostMiddleware, which
reads the host as everything before the first colon: "[::1]:8000" becomes "["
and IPv6 loopback is refused — and localhost resolves to ::1 first on the GPU
host. The header is read the way a URL's host is read instead.

The Vite dev proxy forwards the browser's own Host header ("localhost:5173"),
so the UI passes through it unchanged.
"""

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: This machine, by every name a browser or a script here uses for it.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def hostname(header: str) -> str | None:
    """The host a Host header names, lowercased and without its port, or None
    when it names nothing readable."""
    try:
        return urlsplit(f"//{header}").hostname
    except ValueError:
        return None


class LoopbackHostOnly:
    """Plain ASGI rather than BaseHTTPMiddleware, so the chat's SSE stream
    passes through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and (
            hostname(Headers(scope=scope).get("host", "")) not in LOOPBACK
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await PlainTextResponse("Invalid host header", status_code=400)(
                    scope, receive, send
                )
            return
        await self.app(scope, receive, send)
