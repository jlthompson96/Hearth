"""Thread history through the API, against the real database.

A chat turn goes in through /api/chat exactly as the UI sends it — the agent is
scripted, so no model runs — and comes back out through /api/threads: listed,
titled, found by a word, read back with its tools, pinned, deleted.
"""

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from agents.loop import DoneEvent, Event, RoutedEvent, TokenEvent, ToolEvent, ToolResultEvent
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
    monkeypatch.setattr(titles, "for_question", lambda question, **_: "Back squat progress")

    def _answer(
        agent: object,
        question: str,
        today: dt.date,
        detail: str = "normal",
        history: object = (),
        thread_id: object = None,
    ) -> Iterator[Event]:
        yield RoutedEvent(destination="forge", confidence=0.87, router="constrained-json")
        yield ToolEvent("lift_progression", {"exercise": "back squat"})
        yield ToolResultEvent("lift_progression", "back squat  +17.500 kg since 2026-01-06")
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
    # The result is read back so a clicked figure can be traced to its source
    # line after a reload. The UI shows that one line on request, never the whole
    # result beside the answer, which would invite reading it instead.
    assert answer["tool_calls"] == [
        {
            "name": "lift_progression",
            "args": {"exercise": "back squat"},
            "result": "back squat  +17.500 kg since 2026-01-06",
        }
    ]


def test_a_call_stored_without_a_result_reads_back_as_none() -> None:
    # Turns from before results were kept have only a name and arguments.
    old = threads_route.ToolCall.model_validate({"name": "net_worth_trend", "args": {}})

    assert old.result is None


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


# --- follow-ups ---------------------------------------------------------------------


def _capturing(monkeypatch: pytest.MonkeyPatch, *events: Event) -> dict[str, Any]:
    """Replace the agent with one that records the history it is handed."""
    seen: dict[str, Any] = {}

    def _answer(
        agent: object,
        question: str,
        today: dt.date,
        detail: str = "normal",
        history: Any = (),
        thread_id: object = None,
    ) -> Iterator[Event]:
        seen["history"] = list(history)
        yield from events or (TokenEvent("ok"), DoneEvent())

    monkeypatch.setattr(chat_route, "_events", _answer)
    return seen


def _frames(response_text: str) -> list[tuple[str, Any]]:
    out = []
    for block in response_text.split("\n\n"):
        if block.strip():
            name = block.split("event: ", 1)[1].split("\n", 1)[0]
            out.append((name, json.loads(block.split("data: ", 1)[1])))
    return out


def test_a_follow_up_is_handed_the_thread_so_far_with_its_tool_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = _ask(client, "How have my squats progressed?")
    seen = _capturing(monkeypatch)

    _ask(client, "and my bench?", thread_id)

    (earlier,) = seen["history"]
    assert earlier.question == "How have my squats progressed?"
    assert (earlier.agent, earlier.answer) == ("forge", "Your back squat went up 17.5kg.")
    assert earlier.results == ("back squat  +17.500 kg since 2026-01-06",)


def test_a_new_thread_has_no_history(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capturing(monkeypatch)

    _ask(client, "How have my squats progressed?")

    assert seen["history"] == []


def test_the_thread_event_names_the_stored_question(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "How have my squats progressed?"})

    name, opened = _frames(response.text)[0]
    detail = client.get(f"/api/threads/{opened['id']}").json()
    assert name == "thread"
    assert opened["question_id"] == detail["messages"][0]["id"]


def test_asking_again_uses_the_context_the_question_first_had(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Less / Normal / More control on an earlier answer. What came after
    that question was not there to be referred to when it was asked."""
    thread_id = _ask(client, "first question")
    _ask(client, "second question", thread_id)
    _ask(client, "third question", thread_id)
    messages = client.get(f"/api/threads/{thread_id}").json()["messages"]
    second = next(m["id"] for m in messages if m["content"] == "second question")
    seen = _capturing(monkeypatch)

    client.post(
        "/api/chat",
        json={
            "message": "second question",
            "thread_id": thread_id,
            "agent": "forge",
            "detail": "detailed",
            "rerun_of": second,
        },
    )

    assert [e.question for e in seen["history"]] == ["first question"]


def test_asking_again_needs_a_question_from_this_thread(client: TestClient) -> None:
    thread_id = _ask(client, "first question")
    other = _ask(client, "a different thread")
    elsewhere = client.get(f"/api/threads/{other}").json()["messages"][0]["id"]

    response = client.post(
        "/api/chat", json={"message": "q", "thread_id": thread_id, "rerun_of": elsewhere}
    )
    alone = client.post("/api/chat", json={"message": "q", "rerun_of": elsewhere})

    assert "no longer in this thread" in response.text
    assert alone.status_code == 422


def test_a_repeated_figure_is_checked_against_the_earlier_tool_result_not_the_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """17.5kg came from a tool on the first turn and may be repeated. 140kg was
    the first answer's own invention — flagged then, and flagged again when the
    follow-up repeats it, rather than laundered by having been said before."""
    routed = RoutedEvent(destination="forge", confidence=0.9, router="constrained-json")
    _capturing(
        monkeypatch,
        routed,
        ToolEvent("lift_progression", {"exercise": "back squat"}),
        ToolResultEvent("lift_progression", "back squat  +17.500 kg since 2026-01-06"),
        TokenEvent("Your back squat went up 17.5kg, to 140kg."),
        DoneEvent(),
    )
    first = client.post("/api/chat", json={"message": "How have my squats progressed?"})
    thread_id = _frames(first.text)[0][1]["id"]
    _capturing(monkeypatch, routed, TokenEvent("As before: 17.5kg, to 140kg."), DoneEvent())

    again = client.post("/api/chat", json={"message": "say that again", "thread_id": thread_id})

    def flagged(text: str) -> list[Any]:
        return [data["figures"] for name, data in _frames(text) if name == "ungrounded"]

    assert flagged(first.text) == [["140kg"]]
    assert flagged(again.text) == [["140kg"]]
