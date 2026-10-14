"""Signing in and out, and whether either is needed (api/auth.py).

Plain `def` routes on purpose: checking a password is scrypt, deliberately
slow, and FastAPI runs a plain route in its thread pool rather than on the
event loop that carries every open chat stream.
"""

import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api import auth
from api.refusals import Refusal
from config import get_settings

router = APIRouter(prefix="/api", tags=["session"])


class Session(BaseModel):
    #: Whether this Hearth asks for a password at all.
    required: bool
    signed_in: bool


class Credentials(BaseModel):
    password: str


@router.get("/session", response_model=Session, summary="Whether a sign-in is needed")
def get_session(request: Request) -> Session:
    return Session(
        required=get_settings().hearth_password_hash is not None,
        signed_in=auth.signed_in(request.headers),
    )


@router.post(
    "/login",
    status_code=204,
    responses={
        401: {"model": Refusal, "description": "Not the password"},
        429: {"model": Refusal, "description": "Too many wrong passwords; wait"},
    },
    summary="Sign in with the owner's password",
)
def login(credentials: Credentials) -> Response:
    settings = get_settings()
    stored = settings.hearth_password_hash
    if stored is None:
        return Response(status_code=204)

    now = time.time()
    wait = auth.throttle.locked_for(now)
    if wait:
        minutes = max(1, round(wait / 60))
        return JSONResponse(
            status_code=429,
            content={
                "kind": "throttled",
                "message": f"Too many wrong passwords. Try again in {minutes} min.",
            },
        )
    if not auth.verify_password(credentials.password, stored):
        auth.throttle.failed(now)
        return JSONResponse(
            status_code=401,
            content={"kind": "wrong_password", "message": "That is not the password."},
        )

    auth.throttle.reset()
    response = Response(status_code=204)
    response.set_cookie(
        auth.COOKIE,
        auth.issue(stored, now),
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.hearth_cookie_secure,
        path="/",
    )
    return response


@router.post("/logout", status_code=204, summary="Sign this device out")
def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(
        auth.COOKIE,
        httponly=True,
        samesite="strict",
        secure=get_settings().hearth_cookie_secure,
        path="/",
    )
    return response
