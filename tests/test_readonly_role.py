"""Phase 1's exit criterion: the read-only role provably cannot write.

Rule 2 says the model never writes SQL and query tools connect with a read-only
role. The first half is a design decision. This file is the second half — the
part that stays true even if the first half is someday got wrong.
"""

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

WRITES = [
    "insert into account (label, kind) values ('smuggled', 'checking')",
    "update account set label = 'renamed'",
    "delete from account",
    "truncate account",
    "insert into message (thread_id, role, content) values (gen_random_uuid(), 'user', 'x')",
    "insert into search_audit (query, allowed) values ('x', true)",
    "create table trespass (id int)",
    "drop table account",
    "alter table account add column account_number text",
    "create index on message (role)",
]


def test_read_only_role_can_read(ro_engine: Engine) -> None:
    with ro_engine.connect() as connection:
        count = connection.execute(sa.text("select count(*) from account")).scalar_one()
    assert count >= 0


def test_read_only_role_can_read_the_coverage_view(ro_engine: Engine) -> None:
    """The view is what the trend tools read; a grant that misses it would not
    show up until an agent tried to describe a trend."""
    with ro_engine.connect() as connection:
        connection.execute(sa.text("select * from snapshot_coverage")).all()


@pytest.mark.parametrize("statement", WRITES, ids=lambda s: s.split()[0] + "-" + s.split()[1])
def test_read_only_role_cannot_write(ro_engine: Engine, statement: str) -> None:
    with ro_engine.connect() as connection, pytest.raises(sa.exc.ProgrammingError) as raised:
        connection.execute(sa.text(statement))
        connection.commit()

    # Refused on privilege, not on anything incidental like a bad value.
    # Postgres words it two ways -- "permission denied" for DML, "must be owner"
    # for DDL -- but both arrive as InsufficientPrivilege, so assert on the class
    # rather than on the phrasing.
    assert isinstance(raised.value.orig, psycopg.errors.InsufficientPrivilege)


def test_read_only_role_is_not_a_superuser(ro_engine: Engine) -> None:
    with ro_engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "select rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                "from pg_roles where rolname = current_user"
            )
        ).one()
    assert row == (False, False, False, False)
