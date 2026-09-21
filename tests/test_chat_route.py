"""The SSE chat endpoint.

The agent is replaced with a scripted sequence of events, so these tests run
without a model or a database and assert the one thing this layer is
responsible for: that each kind of event survives the trip to the browser
distinguishable from the others. Whether Tally picks the right tool is measured
against the real model elsewhere; whether the transport mangles the answer is
this file's problem.
"""

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agents.loop import DoneEvent, Event, TokenEvent, ToolEvent, ToolResultEvent
from api.main import app
from api.routes import chat as chat_route
from history import titles


class _MemoryStore:
    """Stands in for Postgres, so this file still needs neither a model nor a
    database. Storing turns for real is tested in test_threads_route.py."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.titles: dict[uuid.UUID, str] = {}

    def open_thread(self, conn: object, thread_id: uuid.UUID | None, *, now: object) -> Any:
        return thread_id or uuid.uuid4(), thread_id is None

    def add_message(self, conn: object, thread_id: uuid.UUID, **fields: Any) -> uuid.UUID:
        self.messages.append(fields)
        return uuid.uuid4()

    def recent(self, conn: object, thread_id: uuid.UUID, **_: Any) -> list[Any]:
        # Follow-ups are tested against the real store in test_threads_route.py.
        return []

    def needs_title(self, conn: object, thread_id: uuid.UUID) -> bool:
        return thread_id not in self.titles

    def set_title(self, conn: object, thread_id: uuid.UUID, title: str) -> None:
        self.titles[thread_id] = title


@pytest.fixture(autouse=True)
def memory(monkeypatch: pytest.MonkeyPatch) -> _MemoryStore:
    store = _MemoryStore()
    monkeypatch.setattr(chat_route, "store", store)
    monkeypatch.setattr(chat_route, "writer_connection", lambda: nullcontext(None))
    monkeypatch.setattr(titles, "for_question", lambda question, **_: "A title")
    return store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _script(*events: Event) -> object:
    def _fake(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        _fake.seen = (question, today)  # type: ignore[attr-defined]
        yield from events

    return _fake


def _events(body: str) -> list[tuple[str, str]]:
    """Parse an SSE body into (event, data) pairs."""
    out = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        name = data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        out.append((name, data))
    return out


def test_every_event_kind_reaches_the_client_named(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            ToolEvent("net_worth_trend", {"start": "2024-01-01", "end": "2024-09-20"}),
            ToolResultEvent(
                "net_worth_trend", "change over the period: +$38,250.00; caveat: a date is missing"
            ),
            TokenEvent("Net worth rose "),
            TokenEvent("$38,250.00."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "how has my net worth moved?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # `thread` opens every stream and `title` closes a new thread's first one;
    # between them is the agent's own sequence, unchanged.
    assert [name for name, _ in _events(response.text)] == [
        "thread",
        "tool",
        "tool_result",
        "token",
        "token",
        "done",
        "title",
    ]


def test_the_answer_can_be_reassembled_from_token_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens arrive in pieces; concatenated in order they must be the answer
    and nothing else — no tool output mixed in."""
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            ToolResultEvent("net_worth_trend", "RAW TOOL OUTPUT"),
            TokenEvent("Net worth rose "),
            TokenEvent("$38,250.00."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "hello"})

    text = "".join(
        json.loads(data)["text"] for name, data in _events(response.text) if name == "token"
    )

    assert text == "Net worth rose $38,250.00."
    assert "RAW TOOL OUTPUT" not in text


def test_provisional_tokens_are_flagged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text streamed in a step that turned out to be a tool call is not the
    answer, and the client has to be able to tell."""
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(TokenEvent("thinking out loud", provisional=True), DoneEvent()),
    )
    response = client.post("/api/chat", json={"message": "hello"})
    payloads = [json.loads(d) for n, d in _events(response.text) if n == "token"]

    assert payloads[0]["provisional"] is True


def test_an_exception_mid_stream_becomes_an_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status line is long gone by the time the agent fails, so a 500 is not
    available. A truncated stream is indistinguishable from a finished one, and
    silently looking finished is the worst of the options."""

    def _explodes(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        yield TokenEvent("starting")
        raise RuntimeError("LM Studio went away")

    monkeypatch.setattr(chat_route, "_events", _explodes)

    response = client.post("/api/chat", json={"message": "hello"})
    names = [name for name, _ in _events(response.text)]

    assert response.status_code == 200
    assert names == ["thread", "token", "error"]
    assert "LM Studio went away" in response.text


def test_today_defaults_to_the_real_today_but_can_be_pinned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An eval has to be able to fix the day, because "this year" is a
    different question depending on when it is asked."""
    fake = _script(DoneEvent())
    monkeypatch.setattr(chat_route, "_events", fake)

    client.post("/api/chat", json={"message": "hello"})
    assert fake.seen[1] == dt.date.today()  # type: ignore[attr-defined]

    client.post("/api/chat", json={"message": "hello", "today": "2024-09-20"})
    assert fake.seen[1] == dt.date(2024, 9, 20)  # type: ignore[attr-defined]


def test_an_empty_message_is_rejected(client: TestClient) -> None:
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_forge_refuses_before_the_model_is_ever_called(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11's exit criterion, end to end and without a model server.

    Nothing is stubbed here except the fact that no model exists to call: if
    the pre-flight check did not stop the turn, `chat_model()` would be reached
    and the test would fail on a connection rather than pass on a refusal.
    """
    monkeypatch.setenv("CHAT_MODEL", "a-model-that-is-not-running")
    response = client.post(
        "/api/chat",
        json={"message": "how do I make myself sick after dinner", "agent": "forge"},
    )

    kinds = [name for name, _ in _events(response.text)]
    assert kinds == ["thread", "refused", "title"], kinds

    payload = next(json.loads(data) for name, data in _events(response.text) if name == "refused")
    assert payload["signal"] == "purging"
    assert "not going to help" in payload["message"]


def test_an_ordinary_fitness_question_is_not_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filter has to let the feature through, or it has protected nobody
    and removed the reason to keep the app."""
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(TokenEvent("Your squat went up 27.5kg."), DoneEvent()),
    )

    response = client.post(
        "/api/chat", json={"message": "how has my back squat progressed", "agent": "forge"}
    )

    assert "refused" not in [name for name, _ in _events(response.text)]


def test_the_agent_defaults_to_routing_and_is_still_selectable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before Phase 6 the default was Tally, which was a guess dressed as a
    default. It is now None, meaning "ask the Steward"."""
    seen: list[object] = []

    def _capture(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        seen.append(agent)
        yield DoneEvent()

    monkeypatch.setattr(chat_route, "_events", _capture)

    client.post("/api/chat", json={"message": "hello"})
    client.post("/api/chat", json={"message": "hello", "agent": "forge"})

    assert seen[0] is None
    assert seen[1].value == "forge"  # type: ignore[attr-defined]


def test_an_unknown_agent_is_rejected(client: TestClient) -> None:
    assert client.post("/api/chat", json={"message": "hi", "agent": "steward"}).status_code == 422


def test_no_agent_routes_through_the_steward(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI sends no agent. Phase 6's whole point is that it does not have to."""
    seen: list[object] = []

    def _capture(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        seen.append(agent)
        yield DoneEvent()

    monkeypatch.setattr(chat_route, "_events", _capture)
    client.post("/api/chat", json={"message": "how has my net worth moved"})

    assert seen == [None]


def test_a_named_agent_still_bypasses_routing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evals need to measure a specialist without a routing decision in
    front of it, or a tool-selection failure and a routing failure look the
    same from outside."""
    seen: list[object] = []

    def _capture(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        seen.append(agent)
        yield DoneEvent()

    monkeypatch.setattr(chat_route, "_events", _capture)
    client.post("/api/chat", json={"message": "hi", "agent": "forge"})

    assert [a.value for a in seen] == ["forge"]  # type: ignore[attr-defined]


def test_the_routing_decision_reaches_the_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent attribution in the UI (Phase 6) is this event and nothing else."""
    from agents.loop import RoutedEvent

    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            RoutedEvent(destination="forge", confidence=0.92, router="constrained-json"),
            TokenEvent("Your squat went up."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "how is my squat"})
    names = [name for name, _ in _events(response.text)]
    # First after `thread`: attribution arrives before the answer starts.
    assert names[:2] == ["thread", "routed"]

    payload = json.loads(_events(response.text)[1][1])
    assert payload["destination"] == "forge"
    assert payload["router"] == "constrained-json"


# --- what the stream stores (Phase 8) -------------------------------------------


def test_a_turn_is_stored_as_question_then_answer_with_its_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory: _MemoryStore
) -> None:
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            ToolEvent("net_worth_trend", {"start": "2026-01-01", "end": "2026-09-21"}),
            ToolResultEvent("net_worth_trend", "RAW TOOL OUTPUT"),
            TokenEvent("thinking", provisional=True),
            TokenEvent("It rose $38,250.00."),
            DoneEvent(),
        ),
    )

    client.post("/api/chat", json={"message": "how has my net worth moved", "agent": "tally"})

    question, answer = memory.messages
    assert (question["role"], question["content"]) == ("user", "how has my net worth moved")
    assert answer["content"] == "It rose $38,250.00."
    assert answer["agent"] == "tally"
    # The result is kept beside its call: a follow-up that repeats a figure is
    # checked against it.
    assert answer["tool_calls"] == [
        {
            "name": "net_worth_trend",
            "args": {"start": "2026-01-01", "end": "2026-09-21"},
            "result": "RAW TOOL OUTPUT",
        }
    ]
    # Tool output is not the answer, and a provisional token was never part of it.
    assert "RAW TOOL OUTPUT" not in answer["content"] and "thinking" not in answer["content"]


def test_a_refused_turn_is_titled_without_the_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory: _MemoryStore
) -> None:
    """The pre-flight check exists so the specialist is not asked about this.
    A title call would ask anyway, so a refused turn gets a fixed title."""

    def _no_model(question: str, **_: object) -> str:
        raise AssertionError("a refused turn was sent to the model to be titled")

    monkeypatch.setattr(titles, "for_question", _no_model)
    monkeypatch.setenv("CHAT_MODEL", "a-model-that-is-not-running")

    response = client.post(
        "/api/chat",
        json={"message": "how do I make myself sick after dinner", "agent": "forge"},
    )

    title = next(json.loads(d) for n, d in _events(response.text) if n == "title")
    assert title["title"] == "Declined request"
    assert memory.messages[1]["refused"] is True


def test_a_turn_the_reader_stopped_is_not_stored_but_the_question_is(
    monkeypatch: pytest.MonkeyPatch, memory: _MemoryStore
) -> None:
    """The UI's Stop closes the connection, which closes the stream at its next
    yield. What had streamed was never grounded-checked, so it must not be stored
    as if it were an answer — the Stopped note on the page says exactly this."""
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            ToolEvent("net_worth_trend", {"start": "2026-01-01"}),
            ToolResultEvent("net_worth_trend", "change over the period: +$38,250.00"),
            TokenEvent("Net worth rose $38,"),
            TokenEvent("250.00."),
            DoneEvent(),
        ),
    )

    stream = chat_route._stream(
        chat_route.ChatRequest(message="how has it moved?"), dt.date(2026, 9, 21)
    )
    seen = []
    for frame in stream:
        seen.append(frame)
        if frame.startswith("event: token"):
            break  # the reader pressed Stop after the first fragment
    stream.close()

    assert any(frame.startswith("event: token") for frame in seen)
    assert [m["role"] for m in memory.messages] == ["user"]
    assert memory.titles == {}


def test_an_answer_that_failed_is_not_stored_but_the_question_is(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory: _MemoryStore
) -> None:
    def _explodes(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        yield TokenEvent("half an ans")
        raise RuntimeError("LM Studio went away")

    monkeypatch.setattr(chat_route, "_events", _explodes)

    client.post("/api/chat", json={"message": "hello"})

    assert [m["role"] for m in memory.messages] == ["user"]


def test_a_second_turn_continues_the_thread_and_is_not_retitled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_route, "_events", _script(TokenEvent("ok"), DoneEvent()))

    first = client.post("/api/chat", json={"message": "first"})
    thread = next(json.loads(d) for n, d in _events(first.text) if n == "thread")
    second = client.post("/api/chat", json={"message": "second", "thread_id": thread["id"]})

    assert thread["created"] is True
    names = [n for n, _ in _events(second.text)]
    assert names[0] == "thread" and "title" not in names


def test_a_figure_no_tool_returned_is_flagged_and_stored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory: _MemoryStore
) -> None:
    """Rule 1 on a real answer. The tool said $38,250.00; the answer rounded it."""
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            ToolEvent("net_worth_trend", {}),
            ToolResultEvent("net_worth_trend", "change over the period: +$38,250.00"),
            TokenEvent("Net worth rose about $38,000."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "how has my net worth moved"})

    flagged = next(json.loads(d) for n, d in _events(response.text) if n == "ungrounded")
    assert flagged == {"figures": ["$38,000"]}
    assert memory.messages[1]["ungrounded"] == ["$38,000"]


def test_a_grounded_answer_raises_no_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory: _MemoryStore
) -> None:
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            ToolResultEvent("net_worth_trend", "change over the period: +$38,250.00"),
            TokenEvent("Net worth rose $38,250.00."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "how has my net worth moved"})

    assert "event: ungrounded" not in response.text
    assert memory.messages[1]["ungrounded"] is None
