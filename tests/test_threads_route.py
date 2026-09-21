"""Thread history through the API, against the real database.

A chat turn goes in through /api/chat exactly as the UI sends it — the agent is
scripted, so no model runs — and comes back out through /api/threads: listed,
titled, found by a word, read back with its tools, pinned, deleted.
"""

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from agents.loop import DoneEvent, Event, RoutedEvent, TokenEvent, ToolEvent
from api.main import app
from api.routes import chat as chat_route
from api.routes import threads as threads_route
from history import titles


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> TestClient:
    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    monkeypatch.setattr(chat_route, "writer_connection", _same)
    monkeypatch.setattr(threads_route, "writer_connection", _same)
    monkeypatch.setattr(threads_route, "readonly_connection", _same)
    monkeypatch.setattr(titles, "for_question", lambda question: "Back squat progress")

    def _answer(
        agent: object, question: str, today: dt.date, detail: str = "normal"
    ) -> Iterator[Event]:
        yield RoutedEvent(destination="forge", confidence=0.87, router="constrained-json")
        yield ToolEvent("lift_progression", {"exercise": "back squat"})
        yield TokenEvent("Your back squat went up 17.5kg.")
        yield DoneEvent()

    monkeypatch.setattr(chat_route, "_events", _answer)
    return TestClient(app)


def _ask(client: TestClient, message: str, thread_id: str | None = None) -> str:
    body: dict[str, str] = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    response = client.post("/api/chat", json=body)
    frames = [block for block in response.text.split("\n\n") if block.strip()]
    first = json.loads(frames[0].split("data: ", 1)[1])
    thread: str = first["id"]
    return thread


def test_a_chat_turn_is_listed_with_its_title(client: TestClient) -> None:
    thread_id = _ask(client, "How have my squats progressed?")

    listing = client.get("/api/threads").json()

    assert listing["retention_days"] == 365
    [thread] = listing["threads"]
    assert (thread["id"], thread["title"], thread["pinned"]) == (
        thread_id,
        "Back squat progress",
        False,
    )


def test_a_word_you_typed_finds_the_thread(client: TestClient) -> None:
    thread_id = _ask(client, "How have my squats progressed?")

    found = client.get("/api/threads", params={"q": "squat"}).json()["threads"]
    missed = client.get("/api/threads", params={"q": "mortgage"}).json()["threads"]

    assert [t["id"] for t in found] == [thread_id]
    assert any(part["match"] for part in found[0]["snippet"])
    assert missed == []


def test_a_thread_reads_back_as_it_was_shown(client: TestClient) -> None:
    thread_id = _ask(client, "How have my squats progressed?")

    detail = client.get(f"/api/threads/{thread_id}").json()

    question, answer = detail["messages"]
    assert question["content"] == "How have my squats progressed?"
    assert answer["agent"] == "forge"
    assert answer["confidence"] == "0.870"
    assert answer["tool_calls"] == [
        {"name": "lift_progression", "args": {"exercise": "back squat"}}
    ]


def test_a_follow_up_joins_the_same_thread(client: TestClient) -> None:
    thread_id = _ask(client, "How have my squats progressed?")

    again = _ask(client, "and my bench?", thread_id)

    assert again == thread_id
    assert len(client.get(f"/api/threads/{thread_id}").json()["messages"]) == 4
    assert len(client.get("/api/threads").json()["threads"]) == 1


def test_pinning_and_deleting(client: TestClient) -> None:
    thread_id = _ask(client, "How have my squats progressed?")

    assert client.patch(f"/api/threads/{thread_id}", json={"pinned": True}).status_code == 204
    assert client.get("/api/threads").json()["threads"][0]["pinned"] is True

    assert client.delete(f"/api/threads/{thread_id}").status_code == 204
    assert client.get("/api/threads").json()["threads"] == []
    assert client.get(f"/api/threads/{thread_id}").status_code == 404


def test_continuing_a_deleted_thread_says_so(client: TestClient) -> None:
    thread_id = _ask(client, "How have my squats progressed?")
    client.delete(f"/api/threads/{thread_id}")

    response = client.post("/api/chat", json={"message": "again", "thread_id": thread_id})

    assert "no longer exists" in response.text
