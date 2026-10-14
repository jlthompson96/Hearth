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

Served from the NAS, the API answers other machines too, by the names listed in
`HEARTH_ALLOWED_HOSTS`; config.py refuses that list without a password, and
api/auth.py asks for it.

The Vite dev proxy forwards the browser's own Host header ("localhost:5173"),
so the UI passes through it unchanged.
"""

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from config import get_settings

#: This machine, by every name a browser or a script here uses for it.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def allowed(name: str | None) -> bool:
    """This machine, or a name `HEARTH_ALLOWED_HOSTS` adds — the NAS's, when
    Hearth is served from there. Any other name is still a rebinding page's.
    Read per request, so a test can add a name."""
    return name in LOOPBACK or name in get_settings().hearth_allowed_hosts


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
            not allowed(hostname(Headers(scope=scope).get("host", "")))
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await PlainTextResponse("Invalid host header", status_code=400)(
                    scope, receive, send
                )
            return
        await self.app(scope, receive, send)
