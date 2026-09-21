"""Preferences: what the Settings screen can change, in effect on the next question.

Against the real database, because "in effect" is the claim: a saved value has
to be what the next sweep or the next listing reads, with no restart between.
"""

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

import model_choice
import preferences
from api.main import app
from api.routes import settings as settings_route
from api.routes import threads as threads_route
from config import get_settings
from db.models import ModelLog
from history import model_log, store
from modellog import LogEntry

NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)


def test_every_preference_starts_at_its_default(conn: sa.Connection) -> None:
    assert preferences.read(conn) == {c.key: c.default for c in preferences.CHOICES}


def test_the_retention_constant_is_the_preference_s_default() -> None:
    """Two statements of "a year" must not drift apart."""
    default = preferences.BY_KEY["thread_retention_days"].default
    assert dt.timedelta(days=default) == store.RETENTION  # type: ignore[arg-type]


def test_a_saved_preference_is_read_back(conn: sa.Connection) -> None:
    preferences.write(conn, "default_detail", "brief")
    preferences.write(conn, "default_detail", "detailed")

    assert preferences.get(conn, "default_detail") == "detailed"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("default_detail", "verbose"),
        ("thread_retention_days", 3650),
        ("thread_retention_days", "365"),
        ("model_log_retention_days", 0),
    ],
)
def test_only_the_listed_values_are_allowed(conn: sa.Connection, key: str, value: object) -> None:
    """ "Forever" is not on the list, and neither is anything a typo makes."""
    with pytest.raises(preferences.NotAllowedError):
        preferences.write(conn, key, value)


def test_an_unknown_preference_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(preferences.UnknownPreferenceError):
        preferences.write(conn, "max_hops", 99)


def test_a_stored_value_no_longer_allowed_reads_as_the_default(conn: sa.Connection) -> None:
    conn.execute(sa.text("insert into preference (key, value) values ('default_detail', '\"x\"')"))

    assert preferences.get(conn, "default_detail") == "normal"


# --- retention, as the preference says -----------------------------------------------


def _aged_thread(conn: sa.Connection, days: int) -> uuid.UUID:
    thread_id, _ = store.open_thread(conn, None, now=NOW)
    store.add_message(conn, thread_id, role="user", content="a question")
    when = NOW - dt.timedelta(days=days)
    conn.execute(
        sa.text("update thread set updated_at = :w where id = :id"), {"w": when, "id": thread_id}
    )
    return thread_id


def _exists(conn: sa.Connection, thread_id: uuid.UUID) -> bool:
    found = conn.execute(sa.text("select 1 from thread where id = :id"), {"id": thread_id})
    return found.first() is not None


def test_a_shorter_thread_retention_is_in_effect_at_the_next_sweep(conn: sa.Connection) -> None:
    old, recent = _aged_thread(conn, 45), _aged_thread(conn, 20)
    store.sweep(conn, now=NOW)
    assert _exists(conn, old) and _exists(conn, recent), "a year, by default"

    preferences.write(conn, "thread_retention_days", 30)
    store.sweep(conn, now=NOW)

    assert not _exists(conn, old) and _exists(conn, recent)


def _entry(started: dt.datetime) -> LogEntry:
    return LogEntry(
        kind="tool",
        caller="tally",
        request={"name": "allocation", "args": {}},
        response={"result": "ok"},
        started_at=started,
        duration_ms=5,
    )


def test_the_model_log_expires_on_its_own_retention_even_on_a_pinned_thread(
    conn: sa.Connection,
) -> None:
    """Pinning keeps a conversation, not the record of every call behind it."""
    thread_id, _ = store.open_thread(conn, None, now=NOW)
    question = store.add_message(conn, thread_id, role="user", content="q")
    store.set_pinned(conn, thread_id, True, now=NOW)
    model_log.add(
        conn,
        thread_id,
        question,
        [_entry(NOW - dt.timedelta(days=100)), _entry(NOW - dt.timedelta(days=10))],
    )

    store.sweep(conn, now=NOW)

    kept = conn.execute(sa.select(ModelLog.started_at)).scalars().all()
    assert kept == [NOW - dt.timedelta(days=10)]
    assert _exists(conn, thread_id)


# --- through the API ----------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> TestClient:
    @contextmanager
    def _same() -> Iterator[sa.Connection]:
        yield conn

    monkeypatch.setattr(settings_route, "readonly_connection", _same)
    monkeypatch.setattr(settings_route, "writer_connection", _same)
    monkeypatch.setattr(threads_route, "readonly_connection", _same)
    # The model list is LM Studio's; these tests are about preferences and must
    # not need it running. tests/test_model_choice.py fakes it in full.
    monkeypatch.setattr(model_choice, "catalog", lambda client=None: [])
    return TestClient(app)


def test_a_change_saved_on_the_screen_is_what_the_next_read_sees(client: TestClient) -> None:
    assert client.put("/api/settings/thread_retention_days", json={"value": 90}).status_code == 204

    shown = {p["key"]: p["value"] for p in client.get("/api/settings").json()["preferences"]}
    listing = client.get("/api/threads").json()

    assert shown["thread_retention_days"] == 90
    assert listing["retention_days"] == 90


def test_a_value_not_on_the_list_is_refused_at_the_door(client: TestClient) -> None:
    assert client.put("/api/settings/default_detail", json={"value": "verbose"}).status_code == 422
    assert client.put("/api/settings/max_hops", json={"value": 99}).status_code == 404


def test_the_configuration_is_shown_without_a_password(client: TestClient) -> None:
    """A database is shown as host and name. Its URL carries the password."""
    body = client.get("/api/settings").text
    passwords = [
        make_url(u).password for u in (get_settings().database_url, get_settings().database_url_ro)
    ]

    for password in passwords:
        assert password and password not in body


def test_the_guardrails_are_shown_locked(client: TestClient) -> None:
    items = {
        item["label"]: item
        for section in client.get("/api/settings").json()["configuration"]
        for item in section["items"]
    }

    for guardrail in ("Routing hops per turn", "Model calls per answer", "Follow-up window"):
        assert items[guardrail]["locked"] is True
    assert items["Routing hops per turn"]["value"] == "6"
