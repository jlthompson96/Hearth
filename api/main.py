"""The FastAPI application.

Phase 0 serves /health and nothing else. Chat over SSE, imports, the egress
audit and the model log arrive with their phases; see docs/plan.md.

The frontend reaches this through Vite's dev proxy rather than across an
origin, so there is no CORS middleware here and no browser preflight to
configure. See web/vite.config.ts.
"""

from fastapi import FastAPI

from api import __version__
from api.routes import health

app = FastAPI(
    title="Hearth",
    version=__version__,
    summary="Local-first personal assistant. Everything runs on this machine.",
)

app.include_router(health.router)
