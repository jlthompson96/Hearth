"""The read-only role, made again outside its migration.

`db.roles.grant_readonly` exists for a restored database, where the migration
that made the role will never run again and `pg_dump` carried no roles. It must
grant exactly what the migration granted: read everything, write nothing, and
read tables made later too.

Everything happens inside the test's transaction, the new role included, and is
rolled back. Privileges are read with Postgres's own `has_*_privilege`
functions, which see a role created in the same transaction.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from db import roles

ROLE = "hearth_roles_test"
WRITES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _relations(conn: sa.Connection) -> list[str]:
    return list(
        conn.execute(
            sa.text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' order by table_name"
            )
        ).scalars()
    )


def _privileges(conn: sa.Connection, role: str) -> dict[str, set[str]]:
    held: dict[str, set[str]] = {}
    for relation in _relations(conn):
        held[relation] = {
            privilege
            for privilege in ("SELECT", *WRITES)
            if conn.execute(
                sa.text("select has_table_privilege(:r, :t, :p)"),
                {"r": role, "t": f"public.{relation}", "p": privilege},
            ).scalar_one()
        }
    return held


def test_the_role_can_read_every_table_and_view_and_write_none(conn: sa.Connection) -> None:
    roles.grant_readonly(conn, ROLE, "not-a-real-password")

    held = _privileges(conn, ROLE)

    assert "snapshot_coverage" in held and "account" in held
    assert all(privileges == {"SELECT"} for privileges in held.values()), held


def test_it_grants_what_the_migration_granted(conn: sa.Connection, ro_engine: Engine) -> None:
    """The harness's read-only role was made by migration fa7860f9535d."""
    migrated = ro_engine.url.username
    assert migrated is not None

    roles.grant_readonly(conn, ROLE, "not-a-real-password")

    assert _privileges(conn, ROLE) == _privileges(conn, migrated)


def test_tables_made_later_are_readable_too(conn: sa.Connection) -> None:
    roles.grant_readonly(conn, ROLE, "not-a-real-password")

    conn.exec_driver_sql("create table later_table (id int)")

    assert _privileges(conn, ROLE)["later_table"] == {"SELECT"}


def test_it_cannot_create_anything_and_is_no_superuser(conn: sa.Connection) -> None:
    roles.grant_readonly(conn, ROLE, "not-a-real-password")

    can_create = conn.execute(
        sa.text("select has_schema_privilege(:r, 'public', 'CREATE')"), {"r": ROLE}
    ).scalar_one()
    flags = conn.execute(
        sa.text(
            "select rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin "
            "from pg_roles where rolname = :r"
        ),
        {"r": ROLE},
    ).one()

    assert can_create is False
    assert tuple(flags) == (False, False, False, False, True)


def test_running_it_twice_changes_nothing(conn: sa.Connection) -> None:
    """`make grant-ro` is safe to run again, which is the point of it."""
    roles.grant_readonly(conn, ROLE, "not-a-real-password")
    first = _privileges(conn, ROLE)

    roles.grant_readonly(conn, ROLE, "another-password")

    assert _privileges(conn, ROLE) == first


def test_credentials_come_from_the_read_only_url() -> None:
    assert roles.credentials("postgresql+psycopg://hearth_ro:pw@localhost/hearth") == (
        "hearth_ro",
        "pw",
    )
    with pytest.raises(ValueError, match="username and a password"):
        roles.credentials("postgresql+psycopg://hearth_ro@localhost/hearth")
