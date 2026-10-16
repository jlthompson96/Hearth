"""What the sidebar's two dots say, part by part.

Separate from /health on purpose: /health checks nothing but this process, so
it stays the thing to look at when everything else is broken. These ask what
the backend and the model depend on — Postgres and SearXNG on the Pi, LM Studio
on the desktop, which is often a different machine from the one that is up.
Behind the sign-in like every /api route.

Nothing here sends anything beyond the LAN: SearXNG is asked at /healthz, the
same probe the router uses, never with a search.
"""

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import BaseModel

import model_choice
from db.session import readonly_connection
from tools import errand

router = APIRouter(prefix="/api/status", tags=["status"])


class Check(BaseModel):
    name: str
    #: None when it was not checked, because something before it failed.
    ok: bool | None
    detail: str


class ModelStatus(BaseModel):
    state: model_choice.StatusState
    #: The model the next question will use.
    model: str
    #: A sentence to show, naming the fix when there is one.
    detail: str
    checks: list[Check]


class BackendStatus(BaseModel):
    """The parts behind the backend. The backend itself answering is what
    /health says, and the UI puts that first."""

    checks: list[Check]


@router.get("/model", response_model=ModelStatus, summary="Whether the chat model can answer")
def get_model_status() -> ModelStatus:
    found = model_choice.status()
    return ModelStatus(
        state=found.state,
        model=found.model,
        detail=found.detail,
        checks=[Check(name=c.name, ok=c.ok, detail=c.detail) for c in found.checks],
    )


@router.get("/backend", response_model=BackendStatus, summary="What the backend depends on")
def get_backend_status() -> BackendStatus:
    return BackendStatus(checks=[_database(), _search()])


def _database() -> Check:
    try:
        with readonly_connection() as conn:
            conn.execute(sa.text("SELECT 1"))
    except sa.exc.SQLAlchemyError as error:
        # The type only: a driver's message can carry the connection URL.
        return Check(
            name="Database", ok=False, detail=f"Postgres did not answer ({type(error).__name__})."
        )
    return Check(name="Database", ok=True, detail="Postgres answers.")


def _search() -> Check:
    if errand.available():
        return Check(
            name="Web search", ok=True, detail="SearXNG answers; web questions are offered."
        )
    return Check(
        name="Web search",
        ok=False,
        detail="SearXNG is not answering, so web questions are not offered. Everything else works.",
    )
