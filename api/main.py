"""The FastAPI application.

/health from Phase 0, the chat stream from Phase 5, the Data & imports and
Manual entry screens' routes from Phase 2, and thread history from Phase 8. The
egress audit arrives with Phase 9; see docs/plan.md.

The frontend reaches this through Vite's dev proxy rather than across an
origin, so there is no CORS middleware here and no browser preflight to
configure. See web/vite.config.ts.
"""

import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import __version__, refusals
from api.routes import accounts, chat, health, imports, threads
from db.writer import writer_connection
from history import store

log = logging.getLogger("hearth")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Apply thread retention once at startup; starting a thread applies it
    again. A database that is not up yet must not stop /health from answering,
    so a failure here is logged and the next new thread tries again."""
    try:
        with writer_connection() as conn:
            removed = store.sweep(conn, now=dt.datetime.now(dt.UTC))
        if removed:
            log.info("retention: removed %d thread(s) untouched for a year", removed)
    except Exception as error:  # noqa: BLE001 - startup must not depend on Postgres
        log.warning("retention sweep skipped: %s", type(error).__name__)
    yield


app = FastAPI(
    title="Hearth",
    version=__version__,
    summary="Local-first personal assistant. Everything runs on this machine.",
    lifespan=lifespan,
)

refusals.install(app)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(threads.router)
app.include_router(imports.router)
app.include_router(accounts.router)
