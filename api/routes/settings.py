"""The Settings screen: preferences you can change, and configuration you can see.

Preferences (`preferences.py`) are stored in Postgres and read where they are
used, so a saved change is in effect on the next question. Configuration comes
from `.env` at startup and from constants in code, and is shown read-only:
editing `.env` from a page would mean restarting the server or showing values
that were not in effect, and the guardrails among it — hop cap, step cap,
follow-up window, the read-only role — are not the page's to loosen. Each is
marked locked where code enforces it.

No password is ever sent. A database is shown as its host and name, never its
URL.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy.engine import make_url

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

#: The window every figure here is measured against. CLAUDE.md's hardware
#: constraint, loaded in LM Studio; not something Hearth sets.
CONTEXT_TOKENS = 8192


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


class SettingsOut(BaseModel):
    preferences: list[PreferenceOut]
    configuration: list[ConfigSection]


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
                item(label="Chat model", value=models.chat_model, locked=False, note="CHAT_MODEL"),
                item(
                    label="Embedding model",
                    value=models.embedding_model,
                    locked=False,
                    note="EMBEDDING_MODEL — changing it means re-embedding everything",
                ),
                item(label="Endpoint", value=models.lm_studio_base_url, locked=False),
                item(label="Context window", value=f"{CONTEXT_TOKENS:,} tokens", locked=False),
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


@router.get("", response_model=SettingsOut, summary="Preferences and the running configuration")
def read_settings() -> SettingsOut:
    with readonly_connection() as conn:
        values = preferences.read(conn)
    return SettingsOut(preferences=_out(values), configuration=_configuration())


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
