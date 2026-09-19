"""Alembic environment.

The database URL comes from config, never from alembic.ini — the same rule that
applies to model names applies here. alembic.ini is committed; .env is not.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models is what registers them on Base.metadata. Without this,
# autogenerate cheerfully reports that the entire schema should be dropped.
import db.models  # noqa: F401  (imported for its side effect)
from config import get_settings
from db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A caller (the test harness) may inject a URL; otherwise use the configured one.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata

#: Objects Alembic must not manage. `snapshot_coverage` is a view created by a
#: migration; autogenerate reflects it as a table it has never heard of and
#: proposes dropping it on every run.
UNMANAGED = {"snapshot_coverage"}


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    return not (type_ == "table" and name in UNMANAGED)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
