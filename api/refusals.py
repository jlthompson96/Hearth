"""How a refused import or entry reaches the browser.

`ingest` raises `Refused` subclasses whose messages are written to be shown —
they carry row numbers, column names and the shapes of cells, never a figure.
This turns them into a response with a machine-readable `kind` and that
message, so no route needs its own try/except and none can forget one.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ingest.errors import Conflict, DataDirProblem, FixtureLoaded, NotFound, Refused


class Refusal(BaseModel):
    kind: str
    message: str


#: For a route's `responses=`, so the refusal shape is in the OpenAPI schema and
#: the generated TypeScript types.
REFUSALS: dict[int | str, dict[str, Any]] = {
    404: {"model": Refusal, "description": "Nothing by that id"},
    409: {"model": Refusal, "description": "Refused: conflicts with what is recorded"},
    422: {"model": Refusal, "description": "Refused: the input cannot be read as it stands"},
}

#: Refusals about the state of things, rather than about the input.
_CONFLICTS = (Conflict, FixtureLoaded, DataDirProblem)


def install(app: FastAPI) -> None:
    @app.exception_handler(Refused)
    async def _refused(_: Request, error: Refused) -> JSONResponse:
        status = 409 if isinstance(error, _CONFLICTS) else 422
        return JSONResponse(status_code=status, content={"kind": error.kind, "message": str(error)})

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, error: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"kind": "not_found", "message": str(error)})
