"""Phase 1's other exit criterion: migrations up and down cleanly."""

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.engine import Engine

import db.models  # noqa: F401  (registers the tables on Base.metadata)
from db.base import Base


def _public_tables(connection: sa.Connection) -> list[str]:
    return list(
        connection.execute(
            sa.text(
                "select tablename from pg_tables where schemaname = 'public' "
                "and tablename <> 'alembic_version' order by 1"
            )
        ).scalars()
    )


def _public_views(connection: sa.Connection) -> list[str]:
    return list(
        connection.execute(
            sa.text("select viewname from pg_views where schemaname = 'public' order by 1")
        ).scalars()
    )


def test_upgrade_created_everything(engine: Engine) -> None:
    with engine.connect() as connection:
        tables = _public_tables(connection)
        views = _public_views(connection)

    assert len(tables) == 13, tables
    assert views == ["snapshot_coverage"]


def test_downgrade_leaves_nothing_behind(alembic_config: Config, engine: Engine) -> None:
    """An unnamed constraint or a hand-written view that upgrade creates and
    downgrade forgets is invisible until the day you need to roll back."""
    command.downgrade(alembic_config, "base")
    with engine.connect() as connection:
        tables_after_downgrade = _public_tables(connection)
        views_after_downgrade = _public_views(connection)

    # Restore before asserting, so a failure does not strand the database.
    command.upgrade(alembic_config, "head")

    assert tables_after_downgrade == []
    assert views_after_downgrade == []


def test_models_and_migrations_agree(engine: Engine) -> None:
    """Hand-written migrations drift from the models silently. This is the only
    thing that notices."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        difference = compare_metadata(context, Base.metadata)

    # The coverage view is created by a migration and is not a model.
    unexplained = [d for d in difference if "snapshot_coverage" not in str(d)]
    assert unexplained == [], unexplained
