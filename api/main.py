"""The FastAPI application.

/health from Phase 0 and Tally's chat stream from Phase 5. Imports and the
egress audit arrive with their phases; see docs/plan.md.

The frontend reaches this through Vite's dev proxy rather than across an
origin, so there is no CORS middleware here and no browser preflight to
configure. See web/vite.config.ts.
"""

from fastapi import FastAPI

from api import __version__
from api.routes import chat, health

app = FastAPI(
    title="Hearth",
    version=__version__,
    summary="Local-first personal assistant. Everything runs on this machine.",
)

app.include_router(health.router)
app.include_router(chat.router)
