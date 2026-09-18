"""Configuration, read from the environment.

Split in two on purpose. `Settings` is what the running app needs to boot;
`ModelSettings` is what the inference layer needs and nothing else does. The API
must come up on a machine with no LM Studio behind it, so a missing CHAT_MODEL
is not allowed to take /health down with it.

No field that names a model carries a default. Model names change; a default
here would be a hardcoded model name by another route (CLAUDE.md, Stack).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class Settings(BaseSettings):
    """Application configuration. Required fields fail loudly at startup."""

    model_config = _ENV

    # Two roles against one database. Tools get the read-only one; only
    # migrations and ingestion use the read-write one (CLAUDE.md, rule 2).
    database_url: str = Field(min_length=1)
    database_url_ro: str = Field(min_length=1)

    # Errand's door. Localhost only — anything else violates rule 5.
    searxng_url: str = "http://localhost:8080"

    # Real CSVs live outside the repo. Unset until Phase 2 needs it.
    hearth_data_dir: Path | None = None


class ModelSettings(BaseSettings):
    """Inference configuration. Arrives in use at Phase 4."""

    model_config = _ENV

    # An endpoint, not a model name — a localhost default is fine here.
    lm_studio_base_url: str = "http://localhost:1234/v1"
    # LM Studio ignores this; ChatOpenAI insists on something.
    lm_studio_api_key: str = "not-needed"

    # No defaults. Set these to the ids LM Studio reports.
    chat_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_model_settings() -> ModelSettings:
    return ModelSettings()
