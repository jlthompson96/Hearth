"""Restoring a dump from `make backup` — onto this machine, or onto a new one.

`make restore DUMP=<file>` restores into `DATABASE_URL`'s database, and
`DB=<name>` into another, to look at a backup without touching the one in use.
The order is the point, because a new cluster is missing something `pg_dump`
never carries — roles:

1. **Refuse a database that already holds tables.** A restore into a live
   database is a merge nobody asked for.
2. **Create the database** if it does not exist.
3. **`pg_restore --no-owner --no-acl --exit-on-error`.** The dump's GRANTs name
   the read-only role, which a new cluster does not have; restored with them,
   each one fails. Ownership goes to the user restoring.
4. **Make the read-only role and grant it** (`db.roles.grant_readonly`), with
   the name and password in `DATABASE_URL_RO`. Without this every query tool
   fails to connect, and the migration that made the role will not run again:
   the restored database already records the newest revision.
5. **Say which Alembic revision it is at.** Older than the code's head, and
   `make migrate` brings it up.

The password reaches `pg_restore` in the environment, never on the command
line, as in `scripts/backup.py`.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from db.roles import credentials, grant_readonly
from scripts.backup import pg_restore


class RestoreError(RuntimeError):
    """Said plainly, because this runs from a terminal."""


def _tables(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                sa.text(
                    "select count(*) from information_schema.tables where table_schema = 'public'"
                )
            ).scalar_one()
        )


def restore(dump: Path, *, url: str, ro_url: str) -> str | None:
    """Restore `dump` into `url`'s database and grant `ro_url`'s role on it.
    Returns the Alembic revision the restored database is at."""
    if not dump.is_file():
        raise RestoreError(f"{dump} is not a file.")
    target = make_url(url)
    if not target.database:
        raise RestoreError("The database URL names no database.")
    role, password = credentials(ro_url)

    admin = sa.create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                sa.text("select 1 from pg_database where datname = :d"), {"d": target.database}
            ).scalar()
            if not exists:
                conn.exec_driver_sql(f'CREATE DATABASE "{target.database}"')
    finally:
        admin.dispose()

    engine = sa.create_engine(target)
    try:
        if _tables(engine):
            raise RestoreError(
                f"{target.database!r} already holds tables. A restore goes into an empty "
                "database: restore into a new one with DB=<name>, or drop this one first."
            )

        environment = dict(os.environ)
        if target.password:
            environment["PGPASSWORD"] = target.password
        finished = subprocess.run(
            [
                str(pg_restore()),
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                f"--host={target.host or 'localhost'}",
                f"--port={target.port or 5432}",
                f"--username={target.username or ''}",
                f"--dbname={target.database}",
                str(dump),
            ],
            env=environment,
            capture_output=True,
            text=True,
        )
        if finished.returncode != 0:
            raise RestoreError(f"pg_restore failed: {finished.stderr.strip().splitlines()[-1:]}")

        with engine.begin() as conn:
            grant_readonly(conn, role, password)
            revision = conn.execute(sa.text("select version_num from alembic_version")).scalar()
        return str(revision) if revision is not None else None
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    from config import get_settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dump", type=Path, help="a .dump file written by make backup")
    parser.add_argument(
        "--database",
        help="restore into this database instead of DATABASE_URL's, e.g. to look at a backup",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    url = make_url(settings.database_url)
    ro = make_url(settings.database_url_ro)
    if args.database:
        url = url.set(database=args.database)
        ro = ro.set(database=args.database)
    try:
        revision = restore(
            args.dump,
            url=url.render_as_string(hide_password=False),
            ro_url=ro.render_as_string(hide_password=False),
        )
    except (RestoreError, ValueError) as problem:
        print(problem, file=sys.stderr)
        return 1
    print(f"restored into {url.database!r}; the read-only role {ro.username!r} can read it")
    print(f"at revision {revision}; run make migrate if the code is newer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
