"""Liveness.

Deliberately dependency-free: /health answers before Postgres exists, which is
what makes it useful as the Phase 0 exit check. A readiness endpoint that does
check the database belongs with the database, in Phase 1.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from api import __version__

router = APIRouter(tags=["system"])


class Health(BaseModel):
    """Response models are declared because the TypeScript types are generated
    from this app's OpenAPI schema — an untyped response is an `any` in the UI."""

    status: Literal["ok"]
    version: str


@router.get("/health", response_model=Health, summary="Liveness probe")
def get_health() -> Health:
    return Health(status="ok", version=__version__)
