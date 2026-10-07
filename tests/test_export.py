"""The open copy of the database: every table out as JSON lines, and back in.

A `pg_dump` can only be read by Postgres. The export is for the day Hearth moves
somewhere a dump cannot follow, so what is tested is that it loses nothing:
every row of every table goes out and comes back equal — money to the cent,
timestamps to the instant, JSON whole — and that a load refuses anything it
cannot put back exactly.

All of it runs inside the test's transaction and is rolled back.
"""

import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

import preferences
from db.models import SearchAudit, Workout, WorkoutSet
from history import model_log, store
from ingest.datadir import REPO
from modellog import LogEntry
from scripts import export

NOW = dt.datetime(2026, 10, 7, 9, 30, tzinfo=dt.UTC)


def _everything(conn: sa.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table.name: [
            tuple(row)
            for row in conn.execute(sa.select(table).order_by(*table.primary_key.columns)).all()
        ]
        for table in export.tables()
    }


@pytest.fixture
def filled(seeded: sa.Connection) -> sa.Connection:
    """The golden fixture, plus a row in each table it leaves empty."""
    conn = seeded
    thread_id, _ = store.open_thread(conn, None, now=NOW)
    question = store.add_message(conn, thread_id, role="user", content="how's my net worth?")
    store.add_message(
        conn,
        thread_id,
        role="assistant",
        content="Your net worth rose $47,650.00 — naïve café text, kept as written.",
        agent="tally",
        tool_calls=[{"name": "net_worth_trend", "args": {"start": "2026-01-01"}, "result": "x"}],
        confidence=Decimal("0.875"),
        ungrounded=["$1.00"],
        detail="normal",
    )
    model_log.add(
        conn,
        thread_id,
        question,
        [
            LogEntry(
                kind="route",
                caller="steward",
                request={"messages": [{"role": "user", "content": "q"}], "temperature": 0.0},
                response={"content": '{"destination": "tally"}', "usage": None},
                started_at=NOW,
                duration_ms=410,
                model="a-model",
                input_tokens=900,
                output_tokens=20,
            )
        ],
    )
    preferences.write(conn, "default_detail", "brief")
    conn.execute(sa.insert(SearchAudit).values(query="weather", allowed=True, result_count=3))
    workout = conn.execute(
        sa.insert(Workout)
        .values(performed_on=dt.date(2026, 10, 6), kind="strength", notes="felt good")
        .returning(Workout.id)
    ).scalar_one()
    conn.execute(
        sa.insert(WorkoutSet).values(
            workout_id=workout,
            exercise="back squat",
            set_number=1,
            reps=5,
            weight=Decimal("225.500"),
            weight_unit="lb",
        )
    )
    return conn


def test_every_row_goes_out_and_comes_back_equal(filled: sa.Connection, tmp_path: Path) -> None:
    before = _everything(filled)
    assert all(before[name] for name in ("account", "message", "model_log", "workout_set"))

    manifest = export.export(filled, tmp_path / "out", now=NOW)

    assert manifest["tables"]["balance_snapshot"]["rows"] == len(before["balance_snapshot"])
    for table in reversed(export.tables()):
        filled.execute(table.delete())

    loaded = export.load(filled, tmp_path / "out")

    assert loaded == {name: len(rows) for name, rows in before.items()}
    assert _everything(filled) == before


def test_money_is_written_as_the_exact_figure(filled: sa.Connection, tmp_path: Path) -> None:
    """A JSON number is a float in most readers; a balance must not become one."""
    export.export(filled, tmp_path / "out", now=NOW)

    first = json.loads((tmp_path / "out" / "balance_snapshot.jsonl").read_text().splitlines()[0])
    sets = (tmp_path / "out" / "workout_set.jsonl").read_text(encoding="utf-8")

    assert isinstance(first["balance"], str) and first["balance"].endswith(".00")
    assert '"weight": "225.500"' in sets


def test_the_manifest_names_what_it_holds(filled: sa.Connection, tmp_path: Path) -> None:
    manifest = export.export(filled, tmp_path / "out", now=NOW)
    on_disk = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))

    assert on_disk == manifest
    assert manifest["format"] == export.FORMAT
    assert manifest["alembic_revision"]
    assert manifest["exported_at"] == "2026-10-07T09:30:00+00:00"
    assert set(manifest["tables"]) == {table.name for table in export.tables()}


def test_numbering_carries_on_after_a_load(filled: sa.Connection, tmp_path: Path) -> None:
    """Postgres numbers search_audit rows itself; the next one written after a
    load must not collide with one that came back."""
    export.export(filled, tmp_path / "out", now=NOW)
    for table in reversed(export.tables()):
        filled.execute(table.delete())
    export.load(filled, tmp_path / "out")

    highest = filled.execute(sa.select(sa.func.max(SearchAudit.id))).scalar_one()
    new = filled.execute(
        sa.insert(SearchAudit).values(query="news", allowed=True).returning(SearchAudit.id)
    ).scalar_one()

    assert new > highest


def test_a_load_into_a_database_that_holds_rows_is_refused(
    filled: sa.Connection, tmp_path: Path
) -> None:
    export.export(filled, tmp_path / "out", now=NOW)

    with pytest.raises(export.ExportError, match="already holds rows"):
        export.load(filled, tmp_path / "out")


def test_a_load_at_another_revision_is_refused(filled: sa.Connection, tmp_path: Path) -> None:
    export.export(filled, tmp_path / "out", now=NOW)
    manifest_path = tmp_path / "out" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["alembic_revision"] = "0000000000aa"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for table in reversed(export.tables()):
        filled.execute(table.delete())

    with pytest.raises(export.ExportError, match="revision 0000000000aa"):
        export.load(filled, tmp_path / "out")


def test_a_file_that_is_not_the_one_recorded_is_refused(
    filled: sa.Connection, tmp_path: Path
) -> None:
    export.export(filled, tmp_path / "out", now=NOW)
    tampered = tmp_path / "out" / "account.jsonl"
    tampered.write_text(tampered.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    for table in reversed(export.tables()):
        filled.execute(table.delete())

    with pytest.raises(export.ExportError, match="account.jsonl"):
        export.load(filled, tmp_path / "out")


def test_an_export_may_not_land_in_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_EXPORT_DIR", str(REPO / "exports"))

    with pytest.raises(export.ExportError, match="inside the repository"):
        export.destination(NOW)


def test_a_chosen_folder_outside_the_repository_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEARTH_EXPORT_DIR", str(tmp_path))

    assert export.destination(NOW) == tmp_path.resolve() / "hearth-20261007-093000"


def test_ids_survive_as_the_same_ids(filled: sa.Connection, tmp_path: Path) -> None:
    """Threads are linked to by id from the model log and the audit; an export
    that renumbered them would break every link."""
    thread_ids = set(filled.execute(sa.text("select id from thread")).scalars())
    export.export(filled, tmp_path / "out", now=NOW)

    lines = (tmp_path / "out" / "thread.jsonl").read_text(encoding="utf-8").splitlines()

    assert {uuid.UUID(json.loads(line)["id"]) for line in lines} == thread_ids


def test_a_fresh_process_finds_every_table() -> None:
    """What `make export` runs: a new interpreter that has imported nothing
    else. It once found no tables at all and wrote an empty export, because
    nothing had imported the models; every other test here had."""
    import subprocess
    import sys

    found = subprocess.run(
        [sys.executable, "-c", "from scripts import export; print(len(export.tables()))"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    )

    assert int(found.stdout) == len(export.tables()) > 10
