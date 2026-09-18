"""Configuration guards.

These exist because two of CLAUDE.md's rules are one careless default away from
being broken silently: model names must come from the environment, and tools
must connect as a role that cannot write.
"""

import pytest
from pydantic import ValidationError

from config import ModelSettings, Settings


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
