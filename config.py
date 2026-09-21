"""Configuration, read from the environment.

Split in two on purpose. `Settings` is what the running app needs to boot;
`ModelSettings` is what the inference layer needs and nothing else does. The API
must come up on a machine with no LM Studio behind it, so a missing CHAT_MODEL
is not allowed to take /health down with it.

No field that names a model carries a default. Model names change; a default
here would be a hardcoded model name by another route (CLAUDE.md, Stack).
"""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# hide_input_in_errors: a rejected DATABASE_URL would otherwise be printed in
# full by the validation error — password included.
_ENV = SettingsConfigDict(
    env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
)

#: Names that only resolve on a local network.
_LAN_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localdomain")


def local_only(url: str) -> str:
    """Rule 5, in code: every address Hearth is configured to call must be this
    machine or the LAN — loopback, a private or link-local address, `localhost`,
    a bare LAN hostname, or a LAN-only suffix. Anything else fails at startup,
    before a single request is made.

    Until this existed the rule was a comment beside the setting, which is the
    kind of guardrail rule 7 says is not one. The error names the host and
    never the URL: a database URL carries a password.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        raise ValueError("the URL has no host")
    if host == "localhost" or host.endswith(_LAN_SUFFIXES):
        return url
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:  # a bare LAN name, e.g. "gpu-box"
            return url
        raise ValueError(
            f"it points at {host!r}, which is neither this machine nor the LAN. Hearth "
            "makes no outbound calls beyond those (CLAUDE.md, rule 5)."
        ) from None
    if address.is_loopback or address.is_private or address.is_link_local:
        return url
    raise ValueError(
        f"it points at {host}, a public address. Hearth makes no outbound calls "
        "beyond this machine and the LAN (CLAUDE.md, rule 5)."
    )


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

    @field_validator("database_url", "database_url_ro", "searxng_url")
    @classmethod
    def _local(cls, url: str) -> str:
        return local_only(url)


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

    # How much the model reasons before answering, for every call that does not
    # set its own. Unset leaves it to the model — which, for the current one,
    # means a few hundred hidden tokens before every routing decision and every
    # answer. "none" turns it off. A setting rather than a constant so the evals
    # can measure both; every recorded result names the value it ran with.
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _unset_when_empty(cls, value: object) -> object:
        # `REASONING_EFFORT=` with nothing after it means unset, not an invalid
        # value — the line a copied .env.example would carry.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("lm_studio_base_url")
    @classmethod
    def _local(cls, url: str) -> str:
        return local_only(url)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_model_settings() -> ModelSettings:
    return ModelSettings()
