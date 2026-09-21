"""The FastAPI application.

/health from Phase 0, the chat stream from Phase 5, and from Phase 2 the
Data & imports and Manual entry screens' routes. The egress audit arrives with
Phase 9; see docs/plan.md.

The frontend reaches this through Vite's dev proxy rather than across an
origin, so there is no CORS middleware here and no browser preflight to
configure. See web/vite.config.ts.
"""

from fastapi import FastAPI

from api import __version__, refusals
from api.routes import accounts, chat, health, imports

app = FastAPI(
    title="Hearth",
    version=__version__,
    summary="Local-first personal assistant. Everything runs on this machine.",
)

refusals.install(app)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(imports.router)
app.include_router(accounts.router)
