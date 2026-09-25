"""The FastAPI application.

/health from Phase 0, the chat stream from Phase 5, the Data & imports and
Manual entry screens' routes from Phase 2, thread history from Phase 8, and the
Model log and Settings screens from the backlog. The egress audit arrives with
Phase 9; see docs/plan.md.

The frontend reaches this through Vite's dev proxy rather than across an
origin, so there is no CORS middleware here and no browser preflight to
configure. See web/vite.config.ts.
"""

import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import llm
import model_choice
from api import __version__, refusals
from api.routes import (
    accounts,
    chat,
    health,
    imports,
    model_log,
    search_audit,
    settings,
    threads,
    training,
)
from db.writer import writer_connection
from history import store

log = logging.getLogger("hearth")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Apply thread and model-log retention once at startup, and the chat model
    chosen on the Settings screen. Starting a thread applies retention again. A
    database that is not up yet must not stop /health from answering, so a
    failure here is logged: retention runs again at the next new thread, and
    the model stays `.env`'s until one is chosen again."""
    try:
        with writer_connection() as conn:
            removed = store.sweep(conn, now=dt.datetime.now(dt.UTC))
            llm.use_chat_model(model_choice.chosen(conn))
        if removed:
            log.info("retention: removed %d thread(s) past their retention", removed)
    except Exception as error:  # noqa: BLE001 - startup must not depend on Postgres
        log.warning("startup reads skipped: %s", type(error).__name__)
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
app.include_router(training.router)
app.include_router(model_log.router)
app.include_router(search_audit.router)
app.include_router(settings.router)
