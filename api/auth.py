"""One owner, one password, and nothing under /api without it.

Hearth was built to answer this machine alone, and api/hosts.py held that line.
Served from the Pi it answers every device at home, so what stands between a
phone on the Wi-Fi and the Model log — every prompt, balances included — is
this. Off until `HEARTH_PASSWORD_HASH` is set, and config.py will not let
`HEARTH_ALLOWED_HOSTS` be set without it.

- **The password is stored as scrypt**, from the standard library, as
  `make password` writes it. No new dependency, and no plain text in `.env`.
- **A session is a signed cookie** holding only its expiry: `HttpOnly`, so no
  script reads it; `SameSite=Strict`, so no other site's page can send it; and
  `Secure` unless `HEARTH_COOKIE_SECURE` is off. The signing key is derived from
  the stored hash, so changing the password signs every device out.
- **Guessing is throttled.** After `MAX_FAILURES` wrong passwords inside
  `WINDOW`, every attempt is refused until the window passes — including the
  right one, or the throttle would tell a guesser when they had it.

What is not behind the gate: `/health`, signing in and out, and the session
probe the UI asks before it knows whether to show the sign-in screen. The UI's
own files are not either; they hold no data.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import deque
from threading import Lock

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from config import get_settings

COOKIE = "hearth_session"
#: How long a device stays signed in.
SESSION_SECONDS = 30 * 24 * 3600

#: Wrong passwords allowed inside WINDOW before every attempt is refused.
MAX_FAILURES = 5
WINDOW = 15 * 60

#: Paths answered without a session. Everything else under these prefixes needs one.
OPEN = frozenset({"/health", "/api/login", "/api/logout", "/api/session"})
GUARDED_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json")

# scrypt at 2**15: 32 MiB and a noticeable fraction of a second per guess,
# which is the point. maxmem must clear 128 * r * n, which is exactly 32 MiB.
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """`scrypt:n:r:p:salt:hash`, every part needed to check it again. Colons,
    not the usual `$`: the Makefile includes `.env` and make would expand `$3`,
    and compose interpolates `$` in its `.env` too."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM)
    return f"scrypt:{_N}:{_R}:{_P}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split(":")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def _key(stored: str) -> bytes:
    return hashlib.sha256(b"hearth-session\0" + stored.encode()).digest()


def issue(stored: str, now: float) -> str:
    """A session token: its expiry, and a signature over it."""
    body = f"v1.{int(now) + SESSION_SECONDS}"
    return f"{body}.{_b64(hmac.new(_key(stored), body.encode(), hashlib.sha256).digest())}"


def valid(token: str | None, stored: str, now: float) -> bool:
    if not token:
        return False
    body, _, signature = token.rpartition(".")
    version, _, expiry = body.partition(".")
    if version != "v1" or not expiry.isdigit():
        return False
    expected = _b64(hmac.new(_key(stored), body.encode(), hashlib.sha256).digest())
    return hmac.compare_digest(signature, expected) and int(expiry) > now


class Throttle:
    """Wrong passwords, counted across every device: there is one owner, and
    behind the Pi's reverse proxy every request arrives from the same address
    anyway. Kept in memory, so a restart forgets it — which a guesser cannot
    cause from outside."""

    def __init__(self) -> None:
        self._failures: deque[float] = deque()
        self._lock = Lock()

    def _expire(self, now: float) -> None:
        while self._failures and self._failures[0] <= now - WINDOW:
            self._failures.popleft()

    def locked_for(self, now: float) -> int:
        """Seconds until an attempt is considered again; 0 when it is now."""
        with self._lock:
            self._expire(now)
            if len(self._failures) < MAX_FAILURES:
                return 0
            return int(self._failures[0] + WINDOW - now) + 1

    def failed(self, now: float) -> None:
        with self._lock:
            self._expire(now)
            self._failures.append(now)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


throttle = Throttle()


def cookie_from(headers: Headers) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def signed_in(headers: Headers, now: float | None = None) -> bool:
    """Whether a request carries a live session, or none is needed."""
    stored = get_settings().hearth_password_hash
    if stored is None:
        return True
    return valid(cookie_from(headers), stored, time.time() if now is None else now)


class RequireSession:
    """Plain ASGI, like LoopbackHostOnly, so the chat's SSE stream passes
    through untouched. The settings are read per request, not at startup, so a
    test can switch the gate on and off."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path: str = scope["path"]
            guarded = path not in OPEN and (
                path.startswith(GUARDED_PREFIXES) or path in ("/docs", "/redoc")
            )
            if guarded and not signed_in(Headers(scope=scope)):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse(
                        status_code=401,
                        content={"kind": "signed_out", "message": "Sign in to continue."},
                    )(scope, receive, send)
                return
        await self.app(scope, receive, send)
