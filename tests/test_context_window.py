"""The window every budget assumes is the window a turn runs at.

The follow-up window, the tool-output cap and the token cap per step were each
measured against 8,192 tokens. LM Studio loads a model on first use at whatever
length it saved for it, and reloads an idled model the same way, so until now
the length a turn ran at was checked only when a model was switched on the
Settings screen. It is checked before a turn now, once a minute at most:

- loaded at 8,192 — ready;
- not loaded — loaded at 8,192, as a switch does;
- loaded only at another length — the turn is refused with the way out, because
  reloading would unload an instance someone may have loaded on purpose;
- LM Studio's own API not answering — ready as far as anyone can tell. Another
  server may speak only the OpenAI protocol, and a guard that cannot see must
  not stop every question.

Also here: the router's reply is capped like every other call, a step that
fills most of the window says so in the Model log, and a stored model choice
that LM Studio no longer lists does not outlive a restart.
"""

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from langchain_core.messages import AIMessageChunk

import llm
import model_choice
from agents import loop
from agents.loop import DoneEvent, Event, LogEvent, TokenEvent
from config import get_model_settings
from modellog import request_body
from steward import graph as steward
from steward import router
from tests.fake_lmstudio import FakeLMStudio, _entry

DEFAULT = get_model_settings().chat_model

#: Captured before the harness stubs them for every other test.
_real_window_problem = model_choice.window_problem
_real_usable = model_choice.usable


@pytest.fixture(autouse=True)
def _real_check(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(model_choice, "window_problem", _real_window_problem)
    monkeypatch.setattr(model_choice, "usable", _real_usable)
    model_choice.forget_window()
    llm.use_chat_model(None)
    yield
    model_choice.forget_window()
    llm.use_chat_model(None)


class _Counting(FakeLMStudio):
    """The fake, counting every request it answers."""

    def __init__(self, *entries: dict[str, Any], fail_load: str | None = None) -> None:
        super().__init__(*entries, fail_load=fail_load)
        self.requests = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return super().handle(request)


def _unreachable() -> httpx.Client:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    return httpx.Client(transport=httpx.MockTransport(refuse))


# --- the check --------------------------------------------------------------------------


def test_a_model_loaded_at_the_window_is_ready() -> None:
    studio = FakeLMStudio(_entry(DEFAULT, 2.6, loaded=[8192]))

    assert model_choice.window_problem(studio.client()) is None
    assert studio.calls == []


def test_a_model_not_loaded_is_loaded_at_the_window() -> None:
    """Otherwise LM Studio would load it on the first call, at whatever length
    it saved for that model."""
    studio = FakeLMStudio(_entry(DEFAULT, 2.6))

    assert model_choice.window_problem(studio.client()) is None
    assert studio.calls == [("load", f"{DEFAULT}@8192")]


def test_a_model_loaded_only_at_another_length_refuses_the_turn() -> None:
    studio = FakeLMStudio(_entry(DEFAULT, 2.6, loaded=[4096]))

    problem = model_choice.window_problem(studio.client())

    assert problem is not None
    assert "4,096" in problem and "8,192" in problem and "Settings" in problem
    # Nothing is unloaded or reloaded behind anyone's back.
    assert studio.calls == []


def test_a_load_that_fails_says_what_lm_studio_said() -> None:
    studio = FakeLMStudio(_entry(DEFAULT, 2.6), fail_load=DEFAULT)

    problem = model_choice.window_problem(studio.client())

    assert problem is not None and "out of memory" in problem


def test_a_model_lm_studio_does_not_have_is_named() -> None:
    studio = FakeLMStudio(_entry("another/model", 2.6, loaded=[8192]))

    problem = model_choice.window_problem(studio.client())

    assert problem is not None and DEFAULT in problem


def test_an_unreachable_api_does_not_stop_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="hearth"):
        assert model_choice.window_problem(_unreachable()) is None

    assert "context window not checked" in caplog.text


def test_the_answer_is_believed_for_a_minute() -> None:
    studio = _Counting(_entry(DEFAULT, 2.6, loaded=[8192]))

    model_choice.window_problem(studio.client())
    asked = studio.requests
    model_choice.window_problem(studio.client())

    assert asked > 0 and studio.requests == asked


def test_a_different_model_is_checked_afresh() -> None:
    studio = _Counting(
        _entry(DEFAULT, 2.6, loaded=[8192]),
        _entry("qwen/qwen3-4b-instruct", 2.4, loaded=[4096]),
    )

    assert model_choice.window_problem(studio.client()) is None
    llm.use_chat_model("qwen/qwen3-4b-instruct")

    assert model_choice.window_problem(studio.client()) is not None


def test_a_switch_forgets_what_was_checked(conn: sa.Connection) -> None:
    """Reloading on the Settings screen is the way out of a refused turn, so
    the next question must not be refused from a minute-old answer."""
    studio = FakeLMStudio(
        _entry(DEFAULT, 2.6, reasoning=["off", "on"], loaded=[4096]),
    )
    assert model_choice.window_problem(studio.client()) is not None

    model_choice.switch(conn, None, client=studio.client())

    assert model_choice.window_problem(studio.client()) is None


# --- where it is checked ----------------------------------------------------------------


def _no_model(**_: object) -> object:
    raise AssertionError("a model was built for a turn that should have stopped")


def test_the_steward_stops_before_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_choice, "window_problem", lambda client=None: "not ready")
    monkeypatch.setattr(router, "structured_reply", _no_model)

    import datetime as dt

    events: list[Event] = list(steward.answer("how is my net worth", today=dt.date(2026, 9, 21)))

    assert events[0] == TokenEvent("not ready")
    assert isinstance(events[-1], DoneEvent) and "not ready" in events[-1].reason


def test_a_specialist_named_directly_stops_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The evals and the API's `agent=` name a specialist without the Steward."""
    monkeypatch.setattr(model_choice, "window_problem", lambda client=None: "not ready")
    monkeypatch.setattr(loop, "chat_model", _no_model)

    events = list(loop.run(caller="tally", system="s", question="q", tools=[]))

    assert events[0] == TokenEvent("not ready")
    assert isinstance(events[-1], DoneEvent)


# --- what each call may spend ------------------------------------------------------------


def test_the_router_s_reply_is_capped() -> None:
    model, asked = router.routing_request("q", None, with_errand=False)

    body = request_body(model, asked)
    # langchain-openai sends max_tokens under its newer name.
    assert body.get("max_completion_tokens", body.get("max_tokens")) == router.ROUTER_MAX_TOKENS


class _Answers:
    def __init__(self, usage: dict[str, int]) -> None:
        self.usage = usage

    def bind_tools(self, tools: object) -> "_Answers":
        return self

    def stream(self, conversation: object) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content="An answer.", usage_metadata=self.usage)


def _step_error(monkeypatch: pytest.MonkeyPatch, spent_in: int, spent_out: int) -> str | None:
    usage = {"input_tokens": spent_in, "output_tokens": spent_out}
    usage["total_tokens"] = spent_in + spent_out
    # The window is ready here; what is measured is what the step reports.
    monkeypatch.setattr(model_choice, "window_problem", lambda client=None: None)
    monkeypatch.setattr(loop, "chat_model", lambda **_: _Answers(usage))
    events = list(loop.run(caller="tally", system="s", question="q", tools=[]))
    (entry,) = [e.entry for e in events if isinstance(e, LogEvent) and e.entry.kind == "step"]
    return entry.error


def test_a_step_that_fills_most_of_the_window_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _step_error(monkeypatch, spent_in=7000, spent_out=600)

    assert error is not None and "7,600 of the 8,192-token window" in error


def test_an_ordinary_step_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A turn peaks near 3,300 tokens (agents/conversation.py)."""
    assert _step_error(monkeypatch, spent_in=3000, spent_out=600) is None


# --- the stored choice, at startup -------------------------------------------------------


def test_a_stored_choice_lm_studio_lists_is_kept() -> None:
    studio = FakeLMStudio(_entry("qwen/qwen3-4b-instruct", 2.4))

    assert model_choice.usable("qwen/qwen3-4b-instruct", studio.client()) == (
        "qwen/qwen3-4b-instruct"
    )


def test_a_stored_choice_lm_studio_no_longer_lists_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Otherwise every question names a model that is not there, until someone
    finds the Settings screen."""
    studio = FakeLMStudio(_entry(DEFAULT, 2.6))

    with caplog.at_level(logging.WARNING, logger="hearth"):
        assert model_choice.usable("gone/model", studio.client()) is None

    assert "gone/model" in caplog.text


def test_a_stored_choice_is_kept_when_lm_studio_cannot_be_asked() -> None:
    """At startup LM Studio may simply not be running yet."""
    assert model_choice.usable("qwen/qwen3-4b-instruct", _unreachable()) == (
        "qwen/qwen3-4b-instruct"
    )


def test_no_stored_choice_needs_no_question() -> None:
    assert model_choice.usable(None, _unreachable()) is None
