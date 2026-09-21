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

A follow-up carries the thread's last few turns. They are read here, before the
new question is stored, and handed on whole; which of them any model sees is
decided in `agents.conversation` — the router the last exchange, each
specialist its own recent answers, and a refused turn nobody. Each tool result
is stored beside its call, because a figure repeated from an earlier answer is
checked against the tool result it first came from.

Every event is named, because the client has to distinguish the answer from the
tool calls that produced it. A single unnamed stream of text would render the
tool chatter into the reply.
"""

import datetime as dt
import json
import logging
import uuid
from collections.abc import Generator, Iterator, Sequence
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from agents import forge, tally
from agents.conversation import MAX_EXCHANGES, Exchange, window
from agents.grounding import ungrounded
from agents.loop import (
    Detail,
    DoneEvent,
    Event,
    LogEvent,
    RefusedEvent,
    RoutedEvent,
    TokenEvent,
    ToolEvent,
    ToolResultEvent,
)
from db.writer import writer_connection
from history import model_log, store, titles
from ingest.errors import NotFound
from modellog import LogEntry
from steward import graph as steward

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger("hearth")


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
    #: How much to say: the "Less / Normal / More" control under an answer
    #: re-asks the same question with this changed, naming the same specialist.
    detail: Detail = "normal"
    #: The stored question this one asks again, from the same control. Its
    #: context is the thread as it was when that question was first asked.
    rerun_of: uuid.UUID | None = None

    @model_validator(mode="after")
    def _rerun_needs_its_thread(self) -> "ChatRequest":
        if self.rerun_of is not None and self.thread_id is None:
            raise ValueError("rerun_of names a question in a thread; thread_id is required")
        return self


#: How many earlier questions are read from the thread. Twice what a specialist
#: is shown, so a conversation that moves between Tally and Forge still leaves
#: each its own recent answers to choose from.
LOADED = 2 * MAX_EXCHANGES


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _exchanges(messages: list[store.StoredMessage]) -> list[Exchange]:
    """Stored messages, paired back into the questions and answers they were."""
    exchanges: list[Exchange] = []
    for message in messages:
        if message.role == "user":
            exchanges.append(Exchange(question=message.content, answer="", agent=None))
        elif message.role == "assistant" and exchanges:
            exchanges[-1] = Exchange(
                question=exchanges[-1].question,
                answer=message.content,
                agent=message.agent,
                refused=message.refused,
                results=tuple(
                    str(call["result"]) for call in message.tool_calls or [] if "result" in call
                ),
            )
    return exchanges


def _events(
    agent: Agent | None,
    message: str,
    today: dt.date,
    detail: Detail = "normal",
    history: Sequence[Exchange] = (),
) -> Iterator[Event]:
    if agent is None:
        return steward.answer(message, today=today, detail=detail, history=history)
    answer = tally.answer if agent is Agent.tally else forge.answer
    return answer(message, today=today, detail=detail, history=history)


def _stream(request: ChatRequest, today: dt.date) -> Generator[str, None, None]:
    try:
        with writer_connection() as conn:
            thread_id, created = store.open_thread(
                conn, request.thread_id, now=dt.datetime.now(dt.UTC)
            )
            # Read before the new question is stored, so it is not its own context.
            history = (
                []
                if created
                else _exchanges(
                    store.recent(conn, thread_id, questions=LOADED, before=request.rerun_of)
                )
            )
            question_id = store.add_message(conn, thread_id, role="user", content=request.message)
    except NotFound:
        gone = "That question is no longer in this thread." if request.rerun_of else ""
        yield _sse("error", {"detail": gone or "That thread no longer exists. Start a new one."})
        return
    except Exception as error:  # noqa: BLE001 - the stream is the only channel back
        yield _sse("error", {"detail": f"history unavailable: {type(error).__name__}: {error}"})
        return
    # The question's id lets the client ask it again with its own context.
    yield _sse(
        "thread", {"id": str(thread_id), "created": created, "question_id": str(question_id)}
    )

    #: Every model call and tool run, for the Model log.
    log: list[LogEntry] = []
    try:
        yield from _turn(request, today, history, thread_id, log)
    finally:
        # However the turn ended: answered, failed, or abandoned by a client
        # that stopped listening — the stream is closed and this still runs.
        # The turn nobody waited for is the one most worth reading.
        _keep(thread_id, question_id, log)


def _turn(
    request: ChatRequest,
    today: dt.date,
    history: Sequence[Exchange],
    thread_id: uuid.UUID,
    log: list[LogEntry],
) -> Iterator[str]:
    """The turn itself, as SSE frames. Appends every Model log entry to `log`,
    which the caller keeps."""
    answer: list[str] = []
    tools: list[dict[str, object]] = []
    agent = request.agent.value if request.agent else None
    confidence: Decimal | None = None
    refusal: str | None = None
    results: list[str] = []
    try:
        for item in _events(request.agent, request.message, today, request.detail, history):
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
                    # The loop yields each result straight after its call.
                    if tools:
                        tools[-1]["result"] = result
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
                case LogEvent(entry=entry):
                    log.append(entry)
    except Exception as error:  # noqa: BLE001 - the stream is the only channel back
        # A traceback cannot reach the client through an open SSE stream, and a
        # silently truncated one looks to the UI exactly like a finished answer.
        # Nothing is stored for the answer: the question stays, unanswered,
        # which is what happened. The log of how it failed is kept.
        yield _sse("error", {"detail": f"{type(error).__name__}: {error}"})
        return

    content = refusal or "".join(answer)
    # Rule 1 on the real answer: a figure no tool returned, and the question
    # did not contain, was made by the model. Flagged, since it has streamed.
    # The earlier turns this specialist was shown count through their tool
    # results and questions, never their answers: an invented figure repeated
    # is still invented.
    shown = window(history, agent) if agent else []
    earlier = [text for e in shown for text in (e.question, *e.results)]
    flags = [] if refusal else ungrounded(content, [*results, request.message, *earlier])
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
                    detail=request.detail,
                )
            untitled = store.needs_title(conn, thread_id)
        if untitled:
            # A refused turn is never sent to the model to be titled: the
            # pre-flight check exists so the specialist is not asked about it,
            # and a title call would ask anyway.
            title = (
                titles.REFUSED if refusal else titles.for_question(request.message, log=log.append)
            )
            with writer_connection() as conn:
                store.set_title(conn, thread_id, title)
            yield _sse("title", {"id": str(thread_id), "title": title})
    except Exception as error:  # noqa: BLE001 - the answer was delivered; say what was not kept
        yield _sse("error", {"detail": f"the answer was not saved: {type(error).__name__}"})


def _keep(thread_id: uuid.UUID, question_id: uuid.UUID, log: list[LogEntry]) -> None:
    """Store a turn's Model log. A log that cannot be written is lost, not a
    failed turn: the answer has already been delivered, and the log is how one
    is explained, never a reason to withhold it."""
    if not log:
        return
    try:
        with writer_connection() as conn:
            model_log.add(conn, thread_id, question_id, log)
    except Exception as error:  # noqa: BLE001 - see above
        logger.warning("model log not stored: %s", type(error).__name__)


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
