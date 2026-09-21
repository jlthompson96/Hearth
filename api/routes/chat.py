"""Chat over Server-Sent Events.

POST rather than GET, so the browser's `EventSource` is not used — it cannot
send a body, and a question does not belong in a URL that ends up in logs and
history. The client reads the response stream with `fetch`, which is a few more
lines in React and one less thing to work around here.

The Steward picks the specialist. `agent` may still name one directly, which
bypasses routing — that exists for the evals, which need to measure a
specialist without a routing decision in front of it, and for the UI's override
when you already know who you are asking.

Every turn is stored (Phase 8). The question is written before the model runs,
so a turn that fails still leaves the question it failed on; the answer is
written when the stream finishes; and a new thread gets its title last, from one
short model call. No connection is held open across a model call — each write
is its own short transaction.

The model still sees one question at a time. A thread is a record you can read
and search, not context the model is given: earlier turns are not sent. That is
a decision with a cost in the 8,192-token window and in routing, and it is not
this phase's to make.

Every event is named, because the client has to distinguish the answer from the
tool calls that produced it. A single unnamed stream of text would render the
tool chatter into the reply.
"""

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents import forge, tally
from agents.grounding import ungrounded
from agents.loop import (
    DoneEvent,
    Event,
    RefusedEvent,
    RoutedEvent,
    TokenEvent,
    ToolEvent,
    ToolResultEvent,
)
from db.writer import writer_connection
from history import store, titles
from ingest.errors import NotFound
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
    #: The thread to continue. None starts a new one; its id comes back in the
    #: first event, `thread`.
    thread_id: uuid.UUID | None = None


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _events(agent: Agent | None, message: str, today: dt.date) -> Iterator[Event]:
    if agent is None:
        return steward.answer(message, today=today)
    answer = tally.answer if agent is Agent.tally else forge.answer
    return answer(message, today=today)


def _stream(request: ChatRequest, today: dt.date) -> Iterator[str]:
    try:
        with writer_connection() as conn:
            thread_id, created = store.open_thread(
                conn, request.thread_id, now=dt.datetime.now(dt.UTC)
            )
            store.add_message(conn, thread_id, role="user", content=request.message)
    except NotFound:
        yield _sse("error", {"detail": "That thread no longer exists. Start a new one."})
        return
    except Exception as error:  # noqa: BLE001 - the stream is the only channel back
        yield _sse("error", {"detail": f"history unavailable: {type(error).__name__}: {error}"})
        return
    yield _sse("thread", {"id": str(thread_id), "created": created})

    answer: list[str] = []
    tools: list[dict[str, object]] = []
    agent = request.agent.value if request.agent else None
    confidence: Decimal | None = None
    refusal: str | None = None
    results: list[str] = []
    try:
        for item in _events(request.agent, request.message, today):
            match item:
                case TokenEvent(text=text, provisional=provisional):
                    if not provisional:
                        answer.append(text)
                    yield _sse("token", {"text": text, "provisional": provisional})
                case ToolEvent(name=name, args=args):
                    tools.append({"name": name, "args": args})
                    yield _sse("tool", {"name": name, "args": args})
                case ToolResultEvent(name=name, result=result):
                    results.append(result)
                    yield _sse("tool_result", {"name": name, "result": result})
                case RoutedEvent(destination=destination, confidence=sure, router=name):
                    agent, confidence = destination, Decimal(str(sure)).quantize(Decimal("0.001"))
                    # Sent before the specialist runs, so the UI can attribute
                    # an answer while it is still streaming.
                    yield _sse(
                        "routed",
                        {"destination": destination, "confidence": sure, "router": name},
                    )
                case RefusedEvent(signal=signal, message=text):
                    refusal = text
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
        # Nothing is stored for the answer: the question stays, unanswered,
        # which is what happened.
        yield _sse("error", {"detail": f"{type(error).__name__}: {error}"})
        return

    content = refusal or "".join(answer)
    # Rule 1 on the real answer: a figure no tool returned, and the question
    # did not contain, was made by the model. Flagged, since it has streamed.
    flags = [] if refusal else ungrounded(content, [*results, request.message])
    if flags:
        yield _sse("ungrounded", {"figures": flags})
    try:
        with writer_connection() as conn:
            if content:
                store.add_message(
                    conn,
                    thread_id,
                    role="assistant",
                    content=content,
                    agent=agent,
                    tool_calls=tools or None,
                    refused=refusal is not None,
                    confidence=confidence,
                    ungrounded=flags or None,
                )
            untitled = store.needs_title(conn, thread_id)
        if untitled:
            # A refused turn is never sent to the model to be titled: the
            # pre-flight check exists so the specialist is not asked about it,
            # and a title call would ask anyway.
            title = titles.REFUSED if refusal else titles.for_question(request.message)
            with writer_connection() as conn:
                store.set_title(conn, thread_id, title)
            yield _sse("title", {"id": str(thread_id), "title": title})
    except Exception as error:  # noqa: BLE001 - the answer was delivered; say what was not kept
        yield _sse("error", {"detail": f"the answer was not saved: {type(error).__name__}"})


@router.post("/chat", summary="Ask a specialist a question; streams the answer as SSE")
def post_chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream(request, request.today or dt.date.today()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Vite's dev proxy and any future reverse proxy both buffer by
            # default, which turns a stream into one late blob.
            "X-Accel-Buffering": "no",
        },
    )
