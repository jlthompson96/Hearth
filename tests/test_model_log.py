"""The Model log: every exchange with the model and every tool run, verbatim.

No model runs. The loop is driven by a fake that records nothing and answers
scripted chunks; the request body is built by langchain-openai's own payload
code against a model that is never called; and the path from a chat turn to
the screen is driven through the API with scripted events.
"""

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from langchain_core.tools import tool

from agents import loop
from agents.loop import DoneEvent, Event, LogEvent, RoutedEvent, TokenEvent, ToolEvent
from api.main import app
from api.routes import chat as chat_route
from api.routes import model_log as model_log_route
from api.routes import threads as threads_route
from history import model_log, store, titles
from llm import chat_model, structured_reply
from modellog import LogEntry, request_body
from steward.router import Decision

NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)


def _entry(kind: str, caller: str, ms: int, **fields: Any) -> LogEntry:
    return LogEntry(
        kind=kind,  # type: ignore[arg-type]
        caller=caller,
        request=fields.pop("request", {"messages": []}),
        response=fields.pop("response", {"content": "ok"}),
        started_at=fields.pop("started_at", NOW),
        duration_ms=ms,
        **fields,
    )


# --- what the loop records ------------------------------------------------------------


@tool
def allocation(as_of: str) -> str:
    """Holdings on a date."""
    return f"VTI $27,600.00 on {as_of}"


class _ToolThenAnswer:
    """Calls the tool once, then answers."""

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools: object) -> "_ToolThenAnswer":
        return self

    def stream(self, conversation: object) -> Iterator[AIMessageChunk]:
        self.calls += 1
        if self.calls == 1:
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "allocation",
                        "args": '{"as_of": "2026-09-20"}',
                        "id": "c1",
                        "index": 0,
                    }
                ],
            )
        else:
            yield AIMessageChunk(content="You hold VTI worth $27,600.00.")


class _Breaks:
    def bind_tools(self, tools: object) -> "_Breaks":
        return self

    def stream(self, conversation: object) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content="half")
        raise ConnectionError("LM Studio went away")


def test_every_model_call_and_tool_run_is_logged_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "chat_model", lambda: _ToolThenAnswer())

    events = list(loop.run(caller="tally", system="s", question="q", tools=[allocation]))
    entries = [e.entry for e in events if isinstance(e, LogEvent)]

    assert [(e.kind, e.caller) for e in entries] == [
        ("step", "tally"),
        ("tool", "tally"),
        ("step", "tally"),
    ]
    step, ran, answered = entries
    assert step.response is not None and step.response["tool_calls"][0]["name"] == "allocation"
    assert ran.request == {"name": "allocation", "args": {"as_of": "2026-09-20"}}
    assert ran.response == {"result": "VTI $27,600.00 on 2026-09-20"}
    assert answered.response is not None
    assert answered.response["content"] == "You hold VTI worth $27,600.00."
    # The second call was sent the tool result: the log shows what the model saw.
    assert "VTI $27,600.00" in json.dumps(answered.request)


def test_a_call_that_failed_is_logged_before_the_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "chat_model", lambda: _Breaks())
    seen: list[Event] = []

    with pytest.raises(ConnectionError):
        for event in loop.run(caller="forge", system="s", question="q", tools=[]):
            seen.append(event)

    (entry,) = [e.entry for e in seen if isinstance(e, LogEvent)]
    assert entry.error == "ConnectionError: LM Studio went away"


# --- the request is the body that is sent ----------------------------------------------


def test_a_specialist_s_request_is_the_client_s_own_payload() -> None:
    """Built by langchain-openai's payload code, against a model that is never
    called: the model name, the tools and the messages as they go out."""
    model = chat_model().bind_tools([allocation])

    body = request_body(model, [("system", "s"), ("human", "q")], stream=True)

    assert body["model"] and body["stream"] is True
    assert body["tools"][0]["function"]["name"] == "allocation"
    assert body["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
    ]


def test_a_constrained_request_shows_the_schema_not_a_class() -> None:
    body = request_body(structured_reply(Decision, reasoning_effort="none"), [("human", "q")])

    assert body["reasoning_effort"] == "none"
    assert body["response_format"]["type"] == "json_schema"
    assert "destination" in json.dumps(body["response_format"]["json_schema"]["schema"])
    json.dumps(body)  # storable as it stands


# --- stored and read back -----------------------------------------------------------------


def _turn(conn: sa.Connection, question: str, answer: str | None) -> tuple[uuid.UUID, uuid.UUID]:
    thread_id, _ = store.open_thread(conn, None, now=NOW)
    asked = store.add_message(conn, thread_id, role="user", content=question)
    if answer is not None:
        store.add_message(conn, thread_id, role="assistant", content=answer, agent="tally")
    return thread_id, asked


def test_a_run_reads_back_as_it_happened(conn: sa.Connection) -> None:
    thread_id, asked = _turn(conn, "what do I own?", "You hold VTI.")
    model_log.add(
        conn,
        thread_id,
        asked,
        [
            _entry("route", "steward", 400, input_tokens=300, output_tokens=12),
            _entry("step", "tally", 2000, started_at=NOW + dt.timedelta(milliseconds=400)),
            _entry(
                "tool",
                "tally",
                30,
                request={"name": "allocation", "args": {}},
                started_at=NOW + dt.timedelta(milliseconds=2400),
            ),
            _entry(
                "step",
                "tally",
                3000,
                input_tokens=1700,
                output_tokens=900,
                started_at=NOW + dt.timedelta(milliseconds=2430),
            ),
        ],
    )

    (summary,) = model_log.runs(conn)
    detail = model_log.run(conn, asked)

    assert summary.question == "what do I own?"
    assert [(s.kind, s.caller, s.tool) for s in summary.chain] == [
        ("route", "steward", None),
        ("step", "tally", None),
        ("tool", "tally", "allocation"),
        ("step", "tally", None),
    ]
    assert (summary.input_tokens, summary.output_tokens) == (2000, 912)
    assert summary.duration_ms == 5430
    assert not summary.failed
    assert detail.answer == "You hold VTI." and detail.answered_by == "tally"
    assert [e.seq for e in detail.entries] == [0, 1, 2, 3]


def test_a_failed_turn_has_no_answer_even_when_the_next_one_does(conn: sa.Connection) -> None:
    thread_id, asked = _turn(conn, "first", None)
    store.add_message(conn, thread_id, role="user", content="second")
    store.add_message(conn, thread_id, role="assistant", content="second's answer")
    model_log.add(conn, thread_id, asked, [_entry("step", "tally", 10, error="ConnectionError")])

    detail = model_log.run(conn, asked)

    assert detail.answer is None
    assert detail.summary.failed


def test_a_log_goes_with_its_thread(conn: sa.Connection) -> None:
    thread_id, asked = _turn(conn, "q", "a")
    model_log.add(conn, thread_id, asked, [_entry("step", "tally", 10)])

    store.delete_thread(conn, thread_id)

    assert model_log.runs(conn) == []


# --- from a chat turn to the screen ----------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> TestClient:
    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    monkeypatch.setattr(chat_route, "writer_connection", _same)
    monkeypatch.setattr(threads_route, "readonly_connection", _same)
    monkeypatch.setattr(model_log_route, "readonly_connection", _same)

    def _title(question: str, **kwargs: Any) -> str:
        kwargs["log"](_entry("title", "titles", 200))
        return "Holdings"

    monkeypatch.setattr(titles, "for_question", _title)
    return TestClient(app)


def _script(*events: Event) -> Any:
    def _answer(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: Any = ()
    ) -> Iterator[Event]:
        yield from events

    return _answer


def test_a_chat_turn_is_logged_and_found_by_its_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        chat_route,
        "_events",
        _script(
            LogEvent(_entry("route", "steward", 400)),
            RoutedEvent(destination="tally", confidence=0.9, router="constrained-json"),
            LogEvent(_entry("step", "tally", 900)),
            ToolEvent("allocation", {"as_of": "2026-09-20"}),
            LogEvent(_entry("tool", "tally", 20, request={"name": "allocation", "args": {}})),
            TokenEvent("You hold VTI."),
            LogEvent(_entry("step", "tally", 1500)),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "what do I own?"})
    opened = json.loads(response.text.split("data: ", 1)[1].split("\n", 1)[0])
    # The log is stored, not streamed: it is the bulk of a turn.
    assert "event: log" not in response.text

    listing = client.get("/api/model-log").json()
    run = client.get(f"/api/model-log/{opened['question_id']}").json()

    assert listing["retention_days"] == 90
    assert [r["question"] for r in listing["runs"]] == ["what do I own?"]
    assert [(e["kind"], e["caller"]) for e in run["entries"]] == [
        ("route", "steward"),
        ("step", "tally"),
        ("tool", "tally"),
        ("step", "tally"),
        ("title", "titles"),
    ]
    assert run["answer"] == "You hold VTI."


def test_a_turn_that_failed_keeps_its_log(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fails(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: Any = ()
    ) -> Iterator[Event]:
        yield LogEvent(_entry("step", "tally", 50, error="ConnectionError: gone"))
        raise ConnectionError("gone")

    monkeypatch.setattr(chat_route, "_events", _fails)

    client.post("/api/chat", json={"message": "what do I own?"})

    (run,) = client.get("/api/model-log").json()["runs"]
    assert run["failed"] is True


def test_an_unknown_run_is_not_found(client: TestClient) -> None:
    assert client.get(f"/api/model-log/{uuid.uuid4()}").status_code == 404
