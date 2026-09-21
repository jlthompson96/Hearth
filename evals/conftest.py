"""The evals' own database.

`make eval` used to read the development database, which held the golden
fixture only because nothing had been imported into it yet. Phase 2 imports real
exports into that database. From then on an eval run would be graded against
real balances — failing, because the cases assert fixture figures — and
`results/<sha>.json` keeps the opening of every failing answer, which is a file
that gets committed. Real figures would reach the repository through the one
door nobody was watching.

So the evals get a database of their own, `hearth_eval`, rebuilt from the
fixture at the start of every run: dropped and recreated rather than reused, so
no run inherits the last one's state. The development database is never opened.
It is left in place afterwards, so a surprising result can be looked at.
"""

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import get_settings
from db import session as db_session
from evals.runner import EVALS
from scripts.seed import seed

EVAL_DATABASE = "hearth_eval"


@pytest.fixture(scope="session", autouse=True)
def _eval_database() -> Iterator[None]:
    try:
        settings = get_settings()
        rw = make_url(settings.database_url)
        ro = make_url(settings.database_url_ro)
    except Exception as error:  # missing or incomplete .env
        pytest.skip(f"database configuration unavailable: {error}")

    admin = sa.create_engine(rw.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{EVAL_DATABASE}" WITH (FORCE)')
            connection.exec_driver_sql(f'CREATE DATABASE "{EVAL_DATABASE}"')
    except sa.exc.OperationalError as error:
        pytest.skip(f"Postgres unreachable — is `make up` running? ({error.__class__.__name__})")
    finally:
        admin.dispose()

    eval_rw = rw.set(database=EVAL_DATABASE).render_as_string(hide_password=False)
    eval_ro = ro.set(database=EVAL_DATABASE).render_as_string(hide_password=False)

    # The tools connect through DATABASE_URL_RO. Same role, other database: the
    # read-only migration grants it here as it does on the development one.
    previous = os.environ.get("DATABASE_URL_RO")
    os.environ["DATABASE_URL_RO"] = eval_ro
    get_settings.cache_clear()
    db_session.readonly_engine.cache_clear()

    config = Config(str(EVALS.parent / "alembic.ini"))
    config.set_main_option("script_location", str(EVALS.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", eval_rw)
    command.upgrade(config, "head")

    writer = sa.create_engine(eval_rw)
    with Session(writer) as session:
        seed(session)
        session.commit()
    writer.dispose()

    # Asserted rather than assumed. If a cached engine survived the switch, the
    # tools would quietly go on reading the development database — the one
    # this fixture exists to keep them away from.
    with db_session.readonly_connection() as connection:
        reading = connection.execute(sa.text("select current_database()")).scalar_one()
    assert reading == EVAL_DATABASE, f"eval tools are reading {reading!r}"

    yield

    db_session.readonly_engine().dispose()
    if previous is None:
        os.environ.pop("DATABASE_URL_RO", None)
    else:
        os.environ["DATABASE_URL_RO"] = previous
    get_settings.cache_clear()
    db_session.readonly_engine.cache_clear()
