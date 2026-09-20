"""Chat over Server-Sent Events.

POST rather than GET, so the browser's `EventSource` is not used — it cannot
send a body, and a question does not belong in a URL that ends up in logs and
history. The client reads the response stream with `fetch`, which is a few more
lines in React and one less thing to work around here.

The Steward picks the specialist. `agent` may still name one directly, which
bypasses routing — that exists for the evals, which need to measure a
specialist without a routing decision in front of it, and for the UI's override
when you already know who you are asking.

Phase 5 is one thread and no history. The `thread` and `message` tables exist
from Phase 1 and nothing writes to them yet: persistence is Phase 8's, along
with the UI that makes stored threads worth having.

Every event is named, because the client has to distinguish the answer from the
tool calls that produced it. A single unnamed stream of text would render the
tool chatter into the reply.
"""

import datetime as dt
import json
from collections.abc import Iterator
from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents import forge, tally
from agents.loop import (
    DoneEvent,
    Event,
    RefusedEvent,
    RoutedEvent,
    TokenEvent,
    ToolEvent,
    ToolResultEvent,
)
from steward import graph as steward

router = APIRouter(prefix="/api", tags=["chat"])


class Agent(StrEnum):
    """A specialist named directly, bypassing the Steward."""

    tally = "tally"
    forge = "forge"


class ChatRequest(BaseModel):
    """Declared so the TypeScript client is generated rather than hand-written."""

    message: str = Field(min_length=1, max_length=2000)
    #: None routes through the Steward, which is what the UI does. Naming a
    #: specialist skips routing entirely.
    agent: Agent | None = None
    #: Overridable only so an eval can pin the day. The UI never sends it, and
    #: there is no default in the agent — "this year" means something different
    #: on different days, and that is a caller's assumption to state.
    today: dt.date | None = None


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _events(agent: Agent | None, message: str, today: dt.date) -> Iterator[Event]:
    if agent is None:
        return steward.answer(message, today=today)
    answer = tally.answer if agent is Agent.tally else forge.answer
    return answer(message, today=today)


def _stream(agent: Agent | None, message: str, today: dt.date) -> Iterator[str]:
    try:
        for item in _events(agent, message, today):
            match item:
                case TokenEvent(text=text, provisional=provisional):
                    yield _sse("token", {"text": text, "provisional": provisional})
                case ToolEvent(name=name, args=args):
                    yield _sse("tool", {"name": name, "args": args})
                case ToolResultEvent(name=name, result=result):
                    yield _sse("tool_result", {"name": name, "result": result})
                case RoutedEvent(destination=destination, confidence=confidence, router=name):
                    # Sent before the specialist runs, so the UI can attribute
                    # an answer while it is still streaming.
                    yield _sse(
                        "routed",
                        {"destination": destination, "confidence": confidence, "router": name},
                    )
                case RefusedEvent(signal=signal, message=text):
                    # Its own event, not a token stream: a refusal is a
                    # different kind of outcome from an answer and the client,
                    # the evals and any future audit all need to tell them
                    # apart without reading the prose.
                    yield _sse("refused", {"signal": signal, "message": text})
                case DoneEvent(reason=reason):
                    yield _sse("done", {"reason": reason})
    except Exception as error:  # noqa: BLE001 - the stream is the only channel back
        # A traceback cannot reach the client through an open SSE stream, and a
        # silently truncated one looks to the UI exactly like a finished answer.
        yield _sse("error", {"detail": f"{type(error).__name__}: {error}"})


@router.post("/chat", summary="Ask a specialist a question; streams the answer as SSE")
def post_chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream(request.agent, request.message, request.today or dt.date.today()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Vite's dev proxy and any future reverse proxy both buffer by
            # default, which turns a stream into one late blob.
            "X-Accel-Buffering": "no",
        },
    )
