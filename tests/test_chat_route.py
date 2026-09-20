"""The SSE chat endpoint.

The agent is replaced with a scripted sequence of events, so these tests run
without a model or a database and assert the one thing this layer is
responsible for: that each kind of event survives the trip to the browser
distinguishable from the others. Whether Tally picks the right tool is measured
against the real model elsewhere; whether the transport mangles the answer is
this file's problem.
"""

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agents.tally import DoneEvent, Event, TokenEvent, ToolEvent, ToolResultEvent
from api.main import app
from api.routes import chat as chat_route


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _script(*events: Event) -> object:
    def _fake(question: str, *, today: dt.date) -> Iterator[Event]:
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
        "answer",
        _script(
            ToolEvent("net_worth_trend", {"start": "2024-01-01", "end": "2024-09-20"}),
            ToolResultEvent("net_worth_trend", "caveat: a date is missing"),
            TokenEvent("Net worth rose "),
            TokenEvent("$38,250.00."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "how has my net worth moved?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in _events(response.text)] == [
        "tool",
        "tool_result",
        "token",
        "token",
        "done",
    ]


def test_the_answer_can_be_reassembled_from_token_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens arrive in pieces; concatenated in order they must be the answer
    and nothing else — no tool output mixed in."""
    monkeypatch.setattr(
        chat_route,
        "answer",
        _script(
            ToolResultEvent("net_worth_trend", "RAW TOOL OUTPUT"),
            TokenEvent("Net worth rose "),
            TokenEvent("$38,250.00."),
            DoneEvent(),
        ),
    )

    response = client.post("/api/chat", json={"message": "hello"})
    import json

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
        "answer",
        _script(TokenEvent("thinking out loud", provisional=True), DoneEvent()),
    )
    import json

    response = client.post("/api/chat", json={"message": "hello"})
    payloads = [json.loads(d) for n, d in _events(response.text) if n == "token"]

    assert payloads[0]["provisional"] is True


def test_an_exception_mid_stream_becomes_an_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status line is long gone by the time the agent fails, so a 500 is not
    available. A truncated stream is indistinguishable from a finished one, and
    silently looking finished is the worst of the options."""

    def _explodes(question: str, *, today: dt.date) -> Iterator[Event]:
        yield TokenEvent("starting")
        raise RuntimeError("LM Studio went away")

    monkeypatch.setattr(chat_route, "answer", _explodes)

    response = client.post("/api/chat", json={"message": "hello"})
    names = [name for name, _ in _events(response.text)]

    assert response.status_code == 200
    assert names == ["token", "error"]
    assert "LM Studio went away" in response.text


def test_today_defaults_to_the_real_today_but_can_be_pinned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An eval has to be able to fix the day, because "this year" is a
    different question depending on when it is asked."""
    fake = _script(DoneEvent())
    monkeypatch.setattr(chat_route, "answer", fake)

    client.post("/api/chat", json={"message": "hello"})
    assert fake.seen[1] == dt.date.today()  # type: ignore[attr-defined]

    client.post("/api/chat", json={"message": "hello", "today": "2024-09-20"})
    assert fake.seen[1] == dt.date(2024, 9, 20)  # type: ignore[attr-defined]


def test_an_empty_message_is_rejected(client: TestClient) -> None:
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
