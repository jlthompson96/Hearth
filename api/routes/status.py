"""Whether the model can answer, for the sidebar.

Separate from /health on purpose: /health checks nothing but this process, so
it stays the thing to look at when everything else is broken. This one asks LM
Studio, which runs on the desktop — on the Pi, often a different machine from
the one that is up. Behind the sign-in like every /api route.
"""

from fastapi import APIRouter
from pydantic import BaseModel

import model_choice

router = APIRouter(prefix="/api/status", tags=["status"])


class ModelStatus(BaseModel):
    state: model_choice.StatusState
    #: The model the next question will use.
    model: str
    #: A sentence to show, naming the fix when there is one.
    detail: str


@router.get("/model", response_model=ModelStatus, summary="Whether the chat model can answer")
def get_model_status() -> ModelStatus:
    found = model_choice.status()
    return ModelStatus(state=found.state, model=found.model, detail=found.detail)
