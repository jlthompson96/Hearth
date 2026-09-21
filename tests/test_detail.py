"""How much a specialist says: brief, normal or detailed.

The level only changes how much to say. The rules — copy figures, never compute
one, state the caveat, give no advice — live in each specialist's own prompt and
must be in the system prompt at every level, or "More" would become a way to
ask for arithmetic.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agents import forge, tally
from agents.loop import DETAILS, DoneEvent, Event, TokenEvent, detail_prompt
from api.main import app
from api.routes import chat as chat_route
from history import titles

TODAY = dt.date(2026, 9, 21)


@pytest.mark.parametrize("detail", DETAILS)
def test_every_level_keeps_every_rule(detail: str) -> None:
    for prompt in (tally.system_prompt(TODAY, detail), forge.system_prompt(TODAY, detail)):  # type: ignore[arg-type]
        assert "Do not calculate anything" in prompt
        assert "copied from a tool result" in prompt
        assert detail_prompt(detail) in prompt  # type: ignore[arg-type]
        assert "{detail}" not in prompt and "{today}" not in prompt
    assert "caveat:" in tally.system_prompt(TODAY, detail)  # type: ignore[arg-type]
    assert "estimate" in forge.system_prompt(TODAY, detail)  # type: ignore[arg-type]


def test_the_levels_are_different_instructions() -> None:
    brief, normal, detailed = (detail_prompt(d) for d in DETAILS)

    assert len({brief, normal, detailed}) == 3
    assert "one or two sentences" in brief
    assert "list" in detailed


def test_an_unknown_level_is_refused_not_guessed() -> None:
    with pytest.raises(ValueError, match="unknown detail level"):
        detail_prompt("verbose")  # type: ignore[arg-type]


# --- through the chat route -------------------------------------------------------


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"stored": []}

    class _Store:
        def open_thread(self, conn: object, thread_id: object, *, now: object) -> Any:
            return "t", False

        def add_message(self, conn: object, thread_id: object, **fields: Any) -> str:
            captured["stored"].append(fields)
            return "q"

        def recent(self, conn: object, thread_id: object, **_: Any) -> list[Any]:
            return []

        def needs_title(self, conn: object, thread_id: object) -> bool:
            return False

        def set_title(self, conn: object, thread_id: object, title: str) -> None:
            pass

    def _events(
        agent: object, question: str, today: dt.date, detail: str = "normal", history: object = ()
    ) -> Iterator[Event]:
        captured["agent"], captured["detail"] = agent, detail
        yield TokenEvent("ok")
        yield DoneEvent()

    monkeypatch.setattr(chat_route, "store", _Store())
    monkeypatch.setattr(chat_route, "writer_connection", lambda: nullcontext(None))
    monkeypatch.setattr(chat_route, "_events", _events)
    monkeypatch.setattr(titles, "for_question", lambda q: "t")
    return captured


def test_the_default_is_normal(seen: dict[str, Any]) -> None:
    TestClient(app).post("/api/chat", json={"message": "how has my net worth moved"})

    assert seen["detail"] == "normal"


def test_a_rerun_names_the_specialist_and_the_level_and_is_stored_with_it(
    seen: dict[str, Any],
) -> None:
    """What the Less / Normal / More control sends: the same question, the
    specialist that answered it, and the level asked for."""
    TestClient(app).post(
        "/api/chat",
        json={"message": "how has my net worth moved", "agent": "tally", "detail": "detailed"},
    )

    assert seen["agent"].value == "tally"
    assert seen["detail"] == "detailed"
    assert seen["stored"][-1]["detail"] == "detailed"


def test_a_level_that_does_not_exist_is_rejected_at_the_door() -> None:
    response = TestClient(app).post("/api/chat", json={"message": "hi", "detail": "verbose"})

    assert response.status_code == 422
