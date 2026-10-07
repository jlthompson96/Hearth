"""Choosing the chat model: what is offered, what is refused, and the order a
switch happens in.

LM Studio is replaced with an `httpx.MockTransport` that holds a model list and
records every load and unload, so nothing here needs LM Studio running and
nothing here can load a model onto the card.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

import llm
import model_choice
from api.main import app
from api.routes import settings as settings_route
from config import get_model_settings
from tests.fake_lmstudio import FakeLMStudio, GiB, _entry

DEFAULT = get_model_settings().chat_model


def _studio(**kwargs: Any) -> FakeLMStudio:
    return FakeLMStudio(
        _entry(DEFAULT, 2.64, reasoning=["off", "on"], loaded=[8192]),
        _entry("qwen/qwen3-4b-thinking", 2.33, reasoning=["on"]),
        _entry("qwen/qwen3-4b-instruct", 2.4, reasoning=["off", "on"]),
        _entry("google/gemma-4-12b", 7.04, reasoning=["off", "on"]),
        _entry("allenai/olmo-32b-think", 18.1, tools=False),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _no_override() -> Iterator[None]:
    llm.use_chat_model(None)
    yield
    llm.use_chat_model(None)


# --- what is offered ------------------------------------------------------------------


def test_only_models_this_card_can_run_are_offered() -> None:
    offered = {m.key: m.refused for m in model_choice.catalog(_studio().client())}

    assert offered[DEFAULT] is None
    assert offered["qwen/qwen3-4b-instruct"] is None
    assert "reasoning off" in str(offered["qwen/qwen3-4b-thinking"])
    assert "7.04 GiB" in str(offered["google/gemma-4-12b"])
    assert "tool use" in str(offered["allenai/olmo-32b-think"])
    assert "nomic-embed" not in offered, "an embedding model is not a chat model"


def test_a_model_that_cannot_stop_reasoning_is_not_offered() -> None:
    """Measured on qwen3-4b-thinking: routing took up to 26 seconds, every title
    ran out of tokens reasoning, and a question did not finish in five minutes.
    A model that can switch reasoning off, or has none, is offered."""
    studio = FakeLMStudio(
        _entry("a/thinks-always", 2.3, reasoning=["on"]),
        _entry("b/can-stop", 2.3, reasoning=["off", "on"]),
        _entry("c/effort-levels", 2.3, reasoning=["low", "medium", "high"]),
        _entry("d/no-reasoning", 2.3),
    )
    refused = {m.key: m.refused for m in model_choice.catalog(studio.client())}

    assert "reasoning off" in str(refused["a/thinks-always"])
    assert "reasoning off" in str(refused["c/effort-levels"])
    assert refused["b/can-stop"] is None and refused["d/no-reasoning"] is None


def test_the_budget_is_an_8b_model_at_q4_with_room() -> None:
    """CLAUDE.md: nothing larger than ~8B at Q4 on an 8GB card. An 8B Q4_K_M
    is about 4.9GB; the budget leaves the rest for the KV cache and the rest."""
    assert 4.9e9 < model_choice.MAX_MODEL_BYTES < 8 * GiB


# --- switching --------------------------------------------------------------------------


def test_a_switch_loads_the_new_model_before_unloading_the_old(conn: sa.Connection) -> None:
    studio = _studio()

    active = model_choice.switch(conn, "qwen/qwen3-4b-instruct", client=studio.client())

    assert active == "qwen/qwen3-4b-instruct"
    assert studio.calls == [
        ("load", "qwen/qwen3-4b-instruct@8192"),
        ("unload", f"{DEFAULT}:0"),
    ]
    assert model_choice.chosen(conn) == "qwen/qwen3-4b-instruct"


def test_a_refused_model_changes_nothing(conn: sa.Connection) -> None:
    studio = _studio()

    with pytest.raises(model_choice.ModelRefusedError, match="7.04 GiB"):
        model_choice.switch(conn, "google/gemma-4-12b", client=studio.client())

    assert studio.calls == []
    assert model_choice.chosen(conn) is None


def test_a_failed_load_leaves_the_old_model_loaded_and_chosen(conn: sa.Connection) -> None:
    studio = _studio(fail_load="qwen/qwen3-4b-instruct")

    with pytest.raises(model_choice.ModelRefusedError, match="out of memory"):
        model_choice.switch(conn, "qwen/qwen3-4b-instruct", client=studio.client())

    assert ("unload", f"{DEFAULT}:0") not in studio.calls
    assert model_choice.chosen(conn) is None


def test_a_model_loaded_at_the_wrong_length_is_reloaded_at_8192(conn: sa.Connection) -> None:
    """LM Studio loads on first use at a length of its own choosing; every
    budget here assumes 8,192."""
    studio = FakeLMStudio(
        _entry(DEFAULT, 2.64, loaded=[8192]),
        _entry("qwen/qwen3-4b-instruct", 2.33, loaded=[4096]),
    )

    model_choice.switch(conn, "qwen/qwen3-4b-instruct", client=studio.client())

    assert studio.calls[:2] == [
        ("unload", "qwen/qwen3-4b-instruct:0"),
        ("load", "qwen/qwen3-4b-instruct@8192"),
    ]


def test_going_back_to_env_s_model_forgets_the_choice(conn: sa.Connection) -> None:
    studio = _studio()
    model_choice.switch(conn, "qwen/qwen3-4b-instruct", client=studio.client())

    active = model_choice.switch(conn, None, client=studio.client())

    assert active == DEFAULT
    assert model_choice.chosen(conn) is None
    assert ("unload", "qwen/qwen3-4b-instruct:new") in studio.calls


def test_the_next_model_call_names_the_chosen_model() -> None:
    llm.use_chat_model("qwen/qwen3-4b-instruct")

    assert llm.chat_model().model_name == "qwen/qwen3-4b-instruct"
    llm.use_chat_model(None)
    assert llm.chat_model().model_name == DEFAULT


def test_a_model_is_measured_when_an_eval_result_names_it(tmp_path: Path) -> None:
    (tmp_path / "abc.json").write_text(json.dumps({"model": DEFAULT}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")

    assert model_choice.measured(tmp_path) == {DEFAULT}


# --- through the API --------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> Iterator[TestClient]:
    studio = _studio()

    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    real_catalog, real_switch = model_choice.catalog, model_choice.switch
    monkeypatch.setattr(settings_route, "readonly_connection", _same)
    monkeypatch.setattr(settings_route, "writer_connection", _same)
    monkeypatch.setattr(model_choice, "catalog", lambda client=None: real_catalog(studio.client()))
    monkeypatch.setattr(
        model_choice,
        "switch",
        lambda conn, key, client=None: real_switch(conn, key, client=studio.client()),
    )
    yield TestClient(app, base_url="http://localhost")


def test_the_screen_lists_every_chat_model_with_why_not(client: TestClient) -> None:
    models = client.get("/api/settings").json()["models"]

    assert models["active"] == models["default"] == DEFAULT
    refused = {o["key"]: o["refused"] for o in models["options"]}
    assert refused[DEFAULT] is None and refused["google/gemma-4-12b"]


def test_choosing_a_model_is_in_effect_on_the_next_question(client: TestClient) -> None:
    response = client.put("/api/settings/chat-model", json={"model": "qwen/qwen3-4b-instruct"})

    assert response.status_code == 204
    assert llm.active_chat_model() == "qwen/qwen3-4b-instruct"
    assert client.get("/api/settings").json()["models"]["active"] == "qwen/qwen3-4b-instruct"


def test_a_model_the_card_cannot_run_is_refused_at_the_door(client: TestClient) -> None:
    response = client.put("/api/settings/chat-model", json={"model": "google/gemma-4-12b"})

    assert response.status_code == 422
    assert response.json()["kind"] == "model_refused"
    assert llm.active_chat_model() == DEFAULT


def test_lm_studio_down_is_said_not_crashed(
    monkeypatch: pytest.MonkeyPatch, conn: sa.Connection
) -> None:
    def _down(client: object = None) -> Any:
        raise httpx.ConnectError("refused")

    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    monkeypatch.setattr(settings_route, "readonly_connection", _same)
    monkeypatch.setattr(model_choice, "catalog", _down)

    models = TestClient(app, base_url="http://localhost").get("/api/settings").json()["models"]

    assert models["options"] == [] and "LM Studio did not answer" in models["unavailable"]
