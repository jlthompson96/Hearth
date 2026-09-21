"""Preferences: what the Settings screen can change, in effect on the next question.

Not configuration. `config.py` reads `.env` once at startup — model names,
database URLs, the LM Studio endpoint — and a screen that edited those would
have to restart the server or show values that were not in effect, which is why
the plan shelved Settings until something genuinely changeable appeared. These
are that: each is read from Postgres where it is used, every time, so a saved
change is true on the next question and nothing needs a restart.

Every preference has a fixed set of allowed values. None of them can loosen a
guardrail: how long history is kept and how long an answer is by default are
the person's choice; the hop cap, the step cap, the follow-up window and the
read-only role are not, and the Settings screen shows those locked.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from db.models import Preference
from ingest.errors import NotFound, Refused


@dataclass(frozen=True)
class Choice:
    """One preference: its default, and every value it may take."""

    key: str
    label: str
    help: str
    default: object
    allowed: tuple[object, ...]


CHOICES: tuple[Choice, ...] = (
    Choice(
        key="default_detail",
        label="Answer length",
        help=(
            "How much a new question's answer says. Less · Normal · More under an "
            "answer still asks again at another length."
        ),
        default="normal",
        allowed=("brief", "normal", "detailed"),
    ),
    Choice(
        key="thread_retention_days",
        label="Keep threads for",
        help=(
            "Days after a thread's last message before it is deleted, with its model "
            "log. Pinned threads are kept however old. Swept at startup and whenever "
            "a thread is started."
        ),
        default=365,
        allowed=(30, 90, 180, 365, 730),
    ),
    Choice(
        key="model_log_retention_days",
        label="Keep the model log for",
        help=(
            "Days to keep each exchange with the model, verbatim. Never longer than "
            "its thread: deleting a thread deletes its log."
        ),
        default=90,
        allowed=(7, 30, 90, 365),
    ),
)

BY_KEY: Mapping[str, Choice] = {c.key: c for c in CHOICES}


class UnknownPreferenceError(NotFound):
    """No preference by that name. Answered 404 by the API's refusal handler."""


class NotAllowedError(Refused):
    """A value not on the preference's list. Answered 422 with the list."""

    kind = "not_allowed"


def read(conn: sa.Connection) -> dict[str, object]:
    """Every preference, stored or default. A stored value that is no longer
    allowed — a choice removed since — reads as the default rather than as
    something the code was never written to handle."""
    stored = dict(conn.execute(sa.select(Preference.key, Preference.value)).tuples().all())
    return {c.key: stored[c.key] if stored.get(c.key) in c.allowed else c.default for c in CHOICES}


def get(conn: sa.Connection, key: str) -> object:
    if key not in BY_KEY:
        raise UnknownPreferenceError(f"no preference {key!r}")
    return read(conn)[key]


def days(conn: sa.Connection, key: str) -> dt.timedelta:
    value = get(conn, key)
    assert isinstance(value, int), key
    return dt.timedelta(days=value)


def write(conn: sa.Connection, key: str, value: object) -> None:
    choice = BY_KEY.get(key)
    if choice is None:
        raise UnknownPreferenceError(f"no preference {key!r}")
    if value not in choice.allowed:
        raise NotAllowedError(f"{key} must be one of {list(choice.allowed)}, not {value!r}")
    conn.execute(
        insert(Preference)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=[Preference.key], set_={"value": value, "updated_at": sa.func.now()}
        )
    )
