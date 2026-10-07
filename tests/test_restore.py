"""A backup restores into a working database — on a cluster that has never seen it.

The review that prompted this found the restore README described would leave
every query tool unable to connect on a new machine: `pg_dump` carries no roles,
and the migration that made the read-only role never runs again on a database
that already records the newest revision. So this is the whole round trip,
with the real programs:

  migrate and seed a scratch database → `pg_dump` it as `make backup` does →
  read it back as `make backup` does → restore it into another database, under
  a read-only role that did not exist → read a known figure through that role.

It needs `pg_dump` and `pg_restore` (PGBIN or PATH). Without them it skips,
unless HEARTH_REQUIRE_DB is set, as CI sets it, when it fails instead.
"""

import datetime as dt
import subprocess
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from scripts import backup, restore
from scripts.seed import YEAR, seed
from tests.conftest import REPO_ROOT, _unavailable
from tools.finance import get_net_worth_trend

SOURCE = "hearth_restore_src"
TARGET = "hearth_restore_dst"
#: A role no cluster has before the restore makes it.
ROLE = "hearth_ro_restore_test"


def _config(url: URL) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return config


@pytest.fixture
def scratch(engine: Engine) -> Iterator[tuple[URL, URL]]:
    """(a migrated, seeded source database; a target that does not exist yet)."""
    for program in ("pg_dump", "pg_restore"):
        try:
            backup.binary(program)
        except backup.BackupError as missing:
            _unavailable(f"{missing} The restore round trip needs it.")

    base = engine.url
    admin = sa.create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")

    def clear() -> None:
        with admin.connect() as conn:
            for name in (TARGET, SOURCE):
                conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{ROLE}"')

    clear()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{SOURCE}"')
    source = base.set(database=SOURCE)
    command.upgrade(_config(source), "head")
    writer = sa.create_engine(source)
    with Session(writer) as session:
        seed(session)
        session.commit()
    writer.dispose()

    try:
        yield source, base.set(database=TARGET)
    finally:
        clear()
        admin.dispose()


def _dump(source: URL, into: Path) -> None:
    """As `make backup` takes one, read back included."""
    arguments, environment = backup.command(source.render_as_string(hide_password=False), into)
    finished = subprocess.run(arguments, env=environment, capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr
    backup.verify(into)


def test_a_backup_restores_onto_a_cluster_without_the_role(
    scratch: tuple[URL, URL], tmp_path: Path
) -> None:
    source, target = scratch
    dump = tmp_path / "hearth.dump"
    _dump(source, dump)
    reader_url = target.set(username=ROLE, password="restore-test-only")

    revision = restore.restore(
        dump,
        url=target.render_as_string(hide_password=False),
        ro_url=reader_url.render_as_string(hide_password=False),
    )

    assert revision == ScriptDirectory.from_config(_config(target)).get_current_head()
    # The query tools read through the role the restore made, and can do no more.
    reader = sa.create_engine(reader_url, poolclass=sa.pool.NullPool)
    try:
        with reader.connect() as conn:
            trend = get_net_worth_trend(conn, dt.date(YEAR, 1, 1), dt.date(YEAR, 12, 31))
            with pytest.raises(sa.exc.ProgrammingError):
                conn.execute(sa.text("delete from account"))
    finally:
        reader.dispose()
    assert trend.change == Decimal("47650.00")


def test_a_restore_into_a_database_that_holds_tables_is_refused(
    scratch: tuple[URL, URL], tmp_path: Path
) -> None:
    source, _ = scratch
    dump = tmp_path / "unused.dump"
    dump.write_bytes(b"never read")

    with pytest.raises(restore.RestoreError, match="already holds tables"):
        restore.restore(
            dump,
            url=source.render_as_string(hide_password=False),
            ro_url=source.set(username=ROLE, password="x").render_as_string(hide_password=False),
        )
