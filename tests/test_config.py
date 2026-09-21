"""Configuration guards.

These exist because CLAUDE.md's rules are one careless default away from being
broken silently: model names must come from the environment, tools must connect
as a role that cannot write, and nothing Hearth is configured to call may be
beyond this machine and the LAN (rule 5).
"""

import pytest
from pydantic import ValidationError

from config import ModelSettings, Settings, local_only


def test_model_names_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A default model name in code is a hardcoded model name. Assume they change."""
    monkeypatch.delenv("CHAT_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)

    with pytest.raises(ValidationError) as error:
        ModelSettings(_env_file=None)

    missing = {e["loc"][0] for e in error.value.errors()}
    assert {"chat_model", "embedding_model"} <= missing


def test_model_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_MODEL", "a-model-that-will-be-replaced")
    monkeypatch.setenv("EMBEDDING_MODEL", "an-embedding-model")

    settings = ModelSettings(_env_file=None)

    assert settings.chat_model == "a-model-that-will-be-replaced"
    assert settings.lm_studio_base_url.startswith("http://localhost")


def test_database_urls_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_tools_get_a_separate_read_only_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 2: query tools connect with a read-only role. Same database, second
    URL. Phase 1 proves the role cannot write; this only proves it is not the
    read-write URL by accident."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://hearth:x@localhost:5432/hearth")
    monkeypatch.setenv("DATABASE_URL_RO", "postgresql+psycopg://hearth_ro:x@localhost:5432/hearth")

    settings = Settings(_env_file=None)

    assert settings.database_url != settings.database_url_ro


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:8080",
        "http://[::1]:1234/v1",
        "http://192.168.1.20:1234/v1",
        "http://10.0.0.5:8080",
        "http://172.16.4.2:1234/v1",
        "http://gpu-box:1234/v1",
        "http://gpu-box.local:1234/v1",
        "postgresql+psycopg://hearth:secret@localhost:5432/hearth",
    ],
)
def test_this_machine_and_the_lan_are_allowed(url: str) -> None:
    assert local_only(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://8.8.8.8:1234/v1",
        "https://searx.example.com",
        "postgresql+psycopg://hearth:secret@db.example.com:5432/hearth",
    ],
)
def test_anywhere_else_is_refused(url: str) -> None:
    """Rule 5 was a comment beside these settings. Now it is a startup failure."""
    with pytest.raises(ValueError, match="rule 5"):
        local_only(url)


def test_a_remote_model_endpoint_stops_the_inference_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_MODEL", "m")
    monkeypatch.setenv("EMBEDDING_MODEL", "e")
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "https://api.openai.com/v1")

    with pytest.raises(ValidationError, match="rule 5"):
        ModelSettings(_env_file=None)


def test_a_refused_database_url_does_not_print_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://hearth:hunter2@db.example.com/h")
    monkeypatch.setenv("DATABASE_URL_RO", "postgresql+psycopg://ro:hunter3@localhost/h")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    assert "db.example.com" in str(caught.value)
    assert "hunter2" not in str(caught.value)


def test_an_empty_reasoning_effort_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The line a copied .env.example carries must not stop the model layer."""
    monkeypatch.setenv("CHAT_MODEL", "m")
    monkeypatch.setenv("EMBEDDING_MODEL", "e")
    monkeypatch.setenv("REASONING_EFFORT", "")

    assert ModelSettings(_env_file=None).reasoning_effort is None
