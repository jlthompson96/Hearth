"""Whether the model is there, as the sidebar says it.

"Backend connected" says Hearth answers and nothing about the model, which runs
on another machine. Served from the Pi, the two come apart: the backend can be
up while the desktop sleeps. So the model gets its own line, read from LM
Studio's own model list — the same list the Settings screen reads — and every
way it can be missing is told apart, because each needs a different fix.

LM Studio is the `FakeLMStudio` of tests/test_model_choice.py; nothing here
needs it running.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import llm
import model_choice
from api.main import app
from config import get_model_settings
from tests.fake_lmstudio import FakeLMStudio, _entry

DEFAULT = get_model_settings().chat_model


@pytest.fixture(autouse=True)
def _no_override() -> Iterator[None]:
    llm.use_chat_model(None)
    yield
    llm.use_chat_model(None)


def _status(*entries: dict[str, Any]) -> model_choice.Status:
    return model_choice.status(FakeLMStudio(*entries).client())


def test_loaded_at_8192_is_ready() -> None:
    status = _status(_entry(DEFAULT, 2.64, loaded=[8192]))

    assert status.state == "ready"
    assert status.model == DEFAULT


def test_listed_but_not_loaded_is_idle_and_loads_when_asked() -> None:
    """Not a fault: the first question loads it at 8,192 (window_problem)."""
    status = _status(_entry(DEFAULT, 2.64))

    assert status.state == "idle"
    assert "first question" in status.detail


def test_loaded_only_at_another_length_is_a_fault_that_names_the_fix() -> None:
    """A turn refuses this model rather than unload it behind anyone's back, so
    the sidebar must not call it ready."""
    status = _status(_entry(DEFAULT, 2.64, loaded=[4096]))

    assert status.state == "wrong_length"
    assert "4096" in status.detail and "Settings" in status.detail


def test_a_model_lm_studio_does_not_have_is_missing() -> None:
    status = _status(_entry("someone/else", 2.4, loaded=[8192]))

    assert status.state == "missing"
    assert DEFAULT in status.detail


def test_the_model_chosen_in_settings_is_the_one_checked() -> None:
    llm.use_chat_model("qwen/qwen3-4b-instruct")
    status = _status(_entry(DEFAULT, 2.64, loaded=[8192]), _entry("qwen/qwen3-4b-instruct", 2.4))

    assert status.model == "qwen/qwen3-4b-instruct"
    assert status.state == "idle"


def test_lm_studio_not_answering_is_unreachable_not_a_crash() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    status = model_choice.status(httpx.Client(transport=httpx.MockTransport(refuse)))

    assert status.state == "unreachable"
    assert "LM Studio did not answer" in status.detail


def test_the_route_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    studio = FakeLMStudio(_entry(DEFAULT, 2.64, loaded=[8192]))
    real = model_choice.status
    monkeypatch.setattr(model_choice, "status", lambda client=None: real(studio.client()))

    with TestClient(app, base_url="http://localhost") as client:
        body = client.get("/api/status/model").json()

    assert body == {"state": "ready", "model": DEFAULT, "detail": body["detail"]}
