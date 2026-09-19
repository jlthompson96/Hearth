"""read only role

Rule 2: query tools connect with a read-only Postgres role. The model never
writes SQL, and the connection it reaches the database through could not write
anything even if it did.

The role's name and password are taken from DATABASE_URL_RO so there is exactly
one source of truth, and Postgres does the quoting — a password is never
interpolated into DDL by hand here.

Creating a login role from a migration is slightly unusual. The alternative was
an initdb script, but those run only once on an empty volume: adding one later
would silently do nothing on any machine whose database already exists, which
is precisely the machine where a missing read-only role matters.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

from config import get_settings

revision = "fa7860f9535d"
down_revision = "fb1ff5eda05b"
branch_labels = None
depends_on = None


def _ro_credentials() -> tuple[str, str]:
    url = make_url(get_settings().database_url_ro)
    if not url.username or not url.password:
        raise RuntimeError(
            "DATABASE_URL_RO must carry both a username and a password: the "
            "read-only role is created from it."
        )
    return url.username, url.password


def _quote(conn: sa.Connection, ident: str) -> str:
    return str(
        conn.execute(sa.text("select quote_ident(:v)"), {"v": ident}).scalar_one()
    )


def upgrade() -> None:
    role, password = _ro_credentials()
    conn = op.get_bind()

    role_ident = _quote(conn, role)
    password_literal = str(
        conn.execute(
            sa.text("select quote_literal(:v)"), {"v": password}
        ).scalar_one()
    )
    db_ident = _quote(
        conn, str(conn.execute(sa.text("select current_database()")).scalar_one())
    )

    already = conn.execute(
        sa.text("select 1 from pg_roles where rolname = :r"), {"r": role}
    ).scalar()

    # exec_driver_sql, not text(): the quoted password must not be parsed for
    # bind parameters.
    verb = "ALTER" if already else "CREATE"
    conn.exec_driver_sql(f"{verb} ROLE {role_ident} LOGIN PASSWORD {password_literal}")

    conn.exec_driver_sql(f"GRANT CONNECT ON DATABASE {db_ident} TO {role_ident}")
    conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role_ident}")
    conn.exec_driver_sql(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role_ident}")

    # Tables a later migration adds must be readable too, or a tool starts
    # failing on a table it was never granted.
    conn.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT ON TABLES TO {role_ident}"
    )

    # SELECT and nothing else. No INSERT, UPDATE, DELETE or TRUNCATE, no
    # sequence access, and no CREATE on the schema.
    conn.exec_driver_sql(f"REVOKE CREATE ON SCHEMA public FROM {role_ident}")


def downgrade() -> None:
    role, _ = _ro_credentials()
    conn = op.get_bind()

    role_ident = _quote(conn, role)
    db_ident = _quote(
        conn, str(conn.execute(sa.text("select current_database()")).scalar_one())
    )

    conn.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT ON TABLES FROM {role_ident}"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_ident}")
    conn.exec_driver_sql(f"REVOKE ALL ON SCHEMA public FROM {role_ident}")
    conn.exec_driver_sql(f"REVOKE ALL ON DATABASE {db_ident} FROM {role_ident}")
    conn.exec_driver_sql(f"DROP ROLE IF EXISTS {role_ident}")
