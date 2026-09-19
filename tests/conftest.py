"""Test harness for the data layer.

Every database test runs against a throwaway `hearth_test` database, created
and dropped per session. The development database is never touched, so a test
run cannot cost you the state you were looking at.

If Postgres is not running, the database tests skip with a reason rather than
failing — but they skip loudly, because a silently green run that proved
nothing is worse than a red one.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, Engine, make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE = "hearth_test"


def _urls() -> tuple[URL, URL, URL]:
    """(maintenance, test read-write, test read-only).

    The read-only role is a cluster-level object shared with the development
    database, and the downgrade test drops whatever role the migration created.
    So the test run gets its own role: dropping `hearth_ro_test` costs nothing,
    where dropping `hearth_ro` would silently strip the grants your dev database
    depends on.
    """
    from config import get_settings

    settings = get_settings()
    rw = make_url(settings.database_url)
    ro = make_url(settings.database_url_ro)
    return (
        rw.set(database="postgres"),
        rw.set(database=TEST_DATABASE),
        ro.set(database=TEST_DATABASE, username=f"{ro.username}_test"),
    )


@pytest.fixture(scope="session")
def _test_database() -> Iterator[tuple[URL, URL]]:
    try:
        admin_url, rw_url, ro_url = _urls()
    except Exception as error:  # missing or incomplete .env
        pytest.skip(f"database configuration unavailable: {error}")

    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
            connection.exec_driver_sql(f'CREATE DATABASE "{TEST_DATABASE}"')
    except sa.exc.OperationalError as error:
        pytest.skip(f"Postgres unreachable — is `make up` running? ({error.__class__.__name__})")

    # pgvector is created by the initdb script on the real database; the test
    # database is made here, so it needs the extension too.
    seed = sa.create_engine(rw_url, isolation_level="AUTOCOMMIT")
    with seed.connect() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    seed.dispose()

    # The read-only migration creates whatever role DATABASE_URL_RO names.
    # Point it at the test role for the duration of the session.
    import config as app_config

    previous = os.environ.get("DATABASE_URL_RO")
    os.environ["DATABASE_URL_RO"] = ro_url.render_as_string(hide_password=False)
    app_config.get_settings.cache_clear()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", rw_url.render_as_string(hide_password=False))
    command.upgrade(config, "head")

    yield rw_url, ro_url

    if previous is None:
        os.environ.pop("DATABASE_URL_RO", None)
    else:
        os.environ["DATABASE_URL_RO"] = previous
    app_config.get_settings.cache_clear()

    with admin.connect() as connection:
        connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
    admin.dispose()


@pytest.fixture(scope="session")
def engine(_test_database: tuple[URL, URL]) -> Iterator[Engine]:
    """Read-write connection. Migrations and fixtures use this."""
    rw_url, _ = _test_database
    created = sa.create_engine(rw_url)
    yield created
    created.dispose()


@pytest.fixture(scope="session")
def ro_engine(_test_database: tuple[URL, URL]) -> Iterator[Engine]:
    """The role the query tools use. It must not be able to write anything."""
    _, ro_url = _test_database
    created = sa.create_engine(ro_url)
    yield created
    created.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[sa.Connection]:
    """A connection whose work is rolled back, so tests cannot leak into each
    other through shared rows."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def alembic_config(_test_database: tuple[URL, URL]) -> Config:
    rw_url, _ = _test_database
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", rw_url.render_as_string(hide_password=False))
    return config
