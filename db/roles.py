"""The read-only role the query tools connect as, created or brought up to date.

Migration fa7860f9535d creates it, once, on the database it migrates. There are
two things that migration cannot do:

- **Run again on a restored database.** `pg_dump` carries no roles, and a
  restored database already records the newest revision, so `alembic upgrade
  head` has nothing to do. On a new machine the role the tools connect as does
  not exist.
- **Repair grants after a restore made with `--no-acl`,** which is how
  `scripts/restore.py` restores, because the dump's GRANTs name a role the new
  cluster may not have.

This does both, and can be run any number of times. `make grant-ro` runs it
against `DATABASE_URL` with the name and password in `DATABASE_URL_RO`.

The statements are the migration's, repeated rather than shared, so a change
here cannot change what an old migration did. `tests/test_roles.py` holds the
two to the same privileges.
"""

import sqlalchemy as sa
from sqlalchemy.engine import make_url


def credentials(ro_url: str) -> tuple[str, str]:
    url = make_url(ro_url)
    if not url.username or not url.password:
        raise ValueError(
            "DATABASE_URL_RO must carry both a username and a password: the read-only "
            "role is created from it."
        )
    return url.username, url.password


def grant_readonly(conn: sa.Connection, role: str, password: str) -> None:
    """Create `role`, or reset its password, and grant it SELECT on everything
    in `public` — tables made later included — and nothing else."""
    role_ident = _quote_ident(conn, role)
    password_literal = str(
        conn.execute(sa.text("select quote_literal(:v)"), {"v": password}).scalar_one()
    )
    database = str(conn.execute(sa.text("select current_database()")).scalar_one())
    db_ident = _quote_ident(conn, database)

    exists = conn.execute(
        sa.text("select 1 from pg_roles where rolname = :r"), {"r": role}
    ).scalar()
    verb = "ALTER" if exists else "CREATE"
    # exec_driver_sql, not text(): the quoted password must not be parsed for
    # bind parameters.
    conn.exec_driver_sql(f"{verb} ROLE {role_ident} LOGIN PASSWORD {password_literal}")
    conn.exec_driver_sql(f"GRANT CONNECT ON DATABASE {db_ident} TO {role_ident}")
    conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role_ident}")
    conn.exec_driver_sql(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role_ident}")
    conn.exec_driver_sql(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role_ident}"
    )
    conn.exec_driver_sql(f"REVOKE CREATE ON SCHEMA public FROM {role_ident}")


def _quote_ident(conn: sa.Connection, ident: str) -> str:
    return str(conn.execute(sa.text("select quote_ident(:v)"), {"v": ident}).scalar_one())


def main() -> int:
    from config import get_settings
    from db.writer import writer_connection

    settings = get_settings()
    role, password = credentials(settings.database_url_ro)
    with writer_connection() as conn:
        grant_readonly(conn, role, password)
        database = conn.execute(sa.text("select current_database()")).scalar_one()
    print(f"read-only role {role!r} can read every table in {database!r}, and write none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
