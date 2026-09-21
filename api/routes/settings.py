"""The Settings screen: preferences you can change, and configuration you can see.

Preferences (`preferences.py`) are stored in Postgres and read where they are
used, so a saved change is in effect on the next question. Configuration comes
from `.env` at startup and from constants in code, and is shown read-only:
editing `.env` from a page would mean restarting the server or showing values
that were not in effect, and the guardrails among it — hop cap, step cap,
follow-up window, the read-only role — are not the page's to loosen. Each is
marked locked where code enforces it.

The chat model is the exception that proves the rule: it can be changed here,
because LM Studio can swap models without Hearth restarting. It is chosen from
the models LM Studio lists and this card can run (`model_choice`), and `.env`'s
`CHAT_MODEL` stays the default and the one the evals measure.

No password is ever sent. A database is shown as its host and name, never its
URL.
"""

from pathlib import Path

import httpx
from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy.engine import make_url

import llm
import model_choice
import preferences
from agents import conversation
from agents.loop import MAX_STEPS
from api.refusals import REFUSALS
from config import get_model_settings, get_settings
from db.session import readonly_connection
from db.writer import writer_connection
from steward.graph import MAX_HOPS
from steward.router import ATTEMPTS, REASONING_EFFORT, ConstrainedJSONRouter

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: The window every figure here is measured against: CLAUDE.md's hardware
#: constraint, and the length a chosen model is loaded at.
CONTEXT_TOKENS = model_choice.CONTEXT_TOKENS

#: Where `make eval` records its runs; each names the model it measured.
EVAL_RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"


class PreferenceOut(BaseModel):
    key: str
    label: str
    help: str
    value: int | str
    default: int | str
    allowed: list[int | str]


class ConfigItem(BaseModel):
    label: str
    value: str
    #: Enforced in code; shown, not changeable from here.
    locked: bool
    note: str | None = None


class ConfigSection(BaseModel):
    title: str
    items: list[ConfigItem]


class ModelOption(BaseModel):
    key: str
    name: str
    params: str | None
    size_bytes: int
    #: The context length of each loaded instance; empty when not loaded.
    loaded_contexts: list[int]
    #: Why this card will not run it; None when it is offered.
    refused: str | None
    #: A recorded `make eval` run has measured it.
    measured: bool


class ModelsOut(BaseModel):
    #: The model the next question will use.
    active: str
    #: `.env`'s CHAT_MODEL: the default, and what the evals measure.
    default: str
    options: list[ModelOption]
    #: Set when LM Studio could not be asked for its list.
    unavailable: str | None


class SettingsOut(BaseModel):
    models: ModelsOut
    preferences: list[PreferenceOut]
    configuration: list[ConfigSection]


class ModelChoiceIn(BaseModel):
    #: A model LM Studio lists, or null for `.env`'s.
    model: str | None


class PreferenceIn(BaseModel):
    value: int | str


def _host(url: str) -> str:
    parsed = make_url(url)
    return f"{parsed.host}:{parsed.port or 5432}/{parsed.database}"


def _configuration() -> list[ConfigSection]:
    env = get_settings()
    models = get_model_settings()
    role = make_url(env.database_url_ro).username or "(unnamed)"
    item = ConfigItem
    return [
        ConfigSection(
            title="Model",
            items=[
                item(
                    label="Chat model",
                    value=llm.active_chat_model(),
                    locked=False,
                    note=f"Chosen above; .env's CHAT_MODEL is {models.chat_model}",
                ),
                item(
                    label="Embedding model",
                    value=models.embedding_model,
                    locked=False,
                    note="EMBEDDING_MODEL — changing it means re-embedding everything",
                ),
                item(label="Endpoint", value=models.lm_studio_base_url, locked=False),
                item(
                    label="Context window",
                    value=f"{CONTEXT_TOKENS:,} tokens",
                    locked=True,
                    note="A chosen model is loaded at this length",
                ),
                item(
                    label="Specialists reason",
                    value=models.reasoning_effort or "model default",
                    locked=False,
                    note="REASONING_EFFORT — off, they stop calling tools",
                ),
                item(
                    label="Router and titles reason",
                    value=REASONING_EFFORT,
                    locked=True,
                    note="Measured: reasoning left on returned empty replies",
                ),
            ],
        ),
        ConfigSection(
            title="Steward and agents",
            items=[
                item(label="Routing", value=ConstrainedJSONRouter.name, locked=True),
                item(label="Routing hops per turn", value=str(MAX_HOPS), locked=True),
                item(label="Router attempts", value=str(ATTEMPTS), locked=True),
                item(label="Model calls per answer", value=str(MAX_STEPS), locked=True),
                item(
                    label="Follow-up window",
                    value=(
                        f"{conversation.MAX_EXCHANGES} exchanges, "
                        f"{conversation.MAX_CHARS:,} characters"
                    ),
                    locked=True,
                    note="Each specialist sees only its own answers",
                ),
                item(
                    label="Pre-flight check",
                    value="on",
                    locked=True,
                    note="Before routing, and again in each specialist",
                ),
            ],
        ),
        ConfigSection(
            title="Data and privacy",
            items=[
                item(label="Database", value=_host(env.database_url), locked=False),
                item(label="Tool role", value=f"{role} · read only", locked=True),
                item(
                    label="Data folder",
                    value=str(env.hearth_data_dir) if env.hearth_data_dir else "not set",
                    locked=False,
                    note="HEARTH_DATA_DIR — outside the repository",
                ),
                item(
                    label="Leaves the machine",
                    value="nothing",
                    locked=True,
                    note="Every configured address must be local, or startup fails",
                ),
                item(
                    label="Telemetry",
                    value="off",
                    locked=True,
                    note="The model connection refuses to start with tracing on",
                ),
            ],
        ),
    ]


def _out(values: dict[str, object]) -> list[PreferenceOut]:
    return [
        PreferenceOut(
            key=c.key,
            label=c.label,
            help=c.help,
            value=values[c.key],
            default=c.default,
            allowed=list(c.allowed),
        )
        for c in preferences.CHOICES
    ]


def _models() -> ModelsOut:
    default = get_model_settings().chat_model
    try:
        found = model_choice.catalog()
    except httpx.HTTPError as error:
        return ModelsOut(
            active=llm.active_chat_model(),
            default=default,
            options=[],
            unavailable=f"LM Studio did not answer ({type(error).__name__}). Is it running?",
        )
    measured = model_choice.measured(EVAL_RESULTS)
    return ModelsOut(
        active=llm.active_chat_model(),
        default=default,
        options=[
            ModelOption(
                key=m.key,
                name=m.name,
                params=m.params,
                size_bytes=m.size_bytes,
                loaded_contexts=list(m.loaded_contexts),
                refused=m.refused,
                measured=m.key in measured,
            )
            for m in found
        ],
        unavailable=None,
    )


@router.get("", response_model=SettingsOut, summary="Preferences and the running configuration")
def read_settings() -> SettingsOut:
    with readonly_connection() as conn:
        values = preferences.read(conn)
    return SettingsOut(models=_models(), preferences=_out(values), configuration=_configuration())


# Declared before `/{key}`, which would otherwise take this path as a key.
@router.put(
    "/chat-model",
    status_code=204,
    response_class=Response,
    summary="Choose the chat model; loaded now, in effect on the next question",
    responses={422: REFUSALS[422]},
)
def choose_model(choice: ModelChoiceIn) -> Response:
    """Refused unless this card can run it; loaded at 8,192 tokens before the
    model it replaces is unloaded, so a refusal or a failed load changes
    nothing."""
    try:
        with writer_connection() as conn:
            model_choice.switch(conn, choice.model)
    except httpx.HTTPError as error:
        raise model_choice.ModelRefusedError(
            f"LM Studio did not answer ({type(error).__name__}). Is it running?"
        ) from error
    # Only once the choice is stored: a failed write must not leave the app
    # answering with a model the next restart would forget.
    llm.use_chat_model(choice.model)
    return Response(status_code=204)


@router.put(
    "/{key}",
    status_code=204,
    response_class=Response,
    summary="Change a preference; in effect on the next question",
    responses={404: REFUSALS[404], 422: REFUSALS[422]},
)
def write_preference(key: str, change: PreferenceIn) -> Response:
    # An unknown key or a value not on the list is refused by the API's refusal
    # handler, in the shape every screen already shows.
    with writer_connection() as conn:
        preferences.write(conn, key, change.value)
    return Response(status_code=204)
