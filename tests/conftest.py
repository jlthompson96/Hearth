"""Test harness for the data layer.

Every database test runs against a throwaway `hearth_test` database, created
and dropped per session. The development database is never touched, so a test
run cannot cost you the state you were looking at.

If Postgres is not running, the database tests skip with a reason rather than
failing — but they skip loudly, because a silently green run that proved
nothing is worse than a red one.
"""

import os
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, Engine, make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE = "hearth_test"


@pytest.fixture(autouse=True)
def _no_search_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unpinned router asks SearXNG whether it is there. `make test` makes no
    network calls, and a closed port on Windows takes about a second to refuse,
    so every test sees Errand as absent unless it says otherwise."""
    from tools import errand

    monkeypatch.setattr(errand, "available", lambda client=None: False)


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
    #
    # It is tolerated when absent, and only until Phase 10. The GPU host runs a
    # native Postgres rather than the pgvector image, and building the extension
    # there needs an MSVC toolchain that is not installed. Nothing before Phase
    # 10 stores a vector — no migration, model or query tool references one — so
    # a hard failure here would take out fifty tests that have nothing to do
    # with embeddings. The warning is the point: Phase 10 must assert the
    # extension exists rather than inheriting this silence.
    seed = sa.create_engine(rw_url, isolation_level="AUTOCOMMIT")
    with seed.connect() as connection:
        try:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        except sa.exc.DBAPIError as error:
            warnings.warn(
                f"pgvector is not installed ({error.__class__.__name__}); continuing "
                "without it. Phase 10 (RAG) cannot run against this database.",
                stacklevel=2,
            )
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
    """The role the query tools use. It must not be able to write anything.

    NullPool, because the downgrade test drops and recreates this role: a
    pooled connection authenticated as the old role survives that and then
    fails with "invalid role OID" in whatever test happens to run next.
    """
    _, ro_url = _test_database
    created = sa.create_engine(ro_url, poolclass=sa.pool.NullPool)
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


@pytest.fixture
def seeded(conn: sa.Connection) -> sa.Connection:
    """The golden fixture, loaded inside the test's transaction so it rolls
    back. Tests that need data share the dataset the evals will use, rather
    than inventing their own and drifting from it."""
    from sqlalchemy.orm import Session

    from scripts.seed import seed

    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    seed(session)
    session.flush()
    return conn
