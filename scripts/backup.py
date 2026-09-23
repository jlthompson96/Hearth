"""A copy of the database, kept outside the repository.

Imports can be run again from the CSVs in `$HEARTH_DATA_DIR`, but nothing else
in here can be: balances entered by hand, every thread, and the model log exist
in one Postgres cluster on one machine. `make backup` writes a compressed dump
beside your other data and keeps the last `KEEP` of them.

Two rules it follows, for the same reasons the rest of the app does:

- **The password never reaches a command line.** `pg_dump` accepts a URL, and a
  URL carries the password into the process list where any other process can
  read it. Host, port, user and database go as flags; the password goes in the
  environment, as `PGPASSWORD`.
- **Backups live outside the repository**, like the CSVs. A dump is the whole
  database in one file, and the one place it must never be is a directory that
  gets committed.

Restoring, when the day comes:

    createdb -U <user> hearth
    pg_restore -U <user> -d hearth --clean --if-exists <the .dump file>
"""

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[1]

#: How many dumps to keep. Daily-ish use makes this a fortnight of history,
#: which is longer than it takes to notice something is wrong.
KEEP = 14

#: Where they go when `HEARTH_BACKUP_DIR` is unset.
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "Hearth/backups"


class BackupError(RuntimeError):
    """Said plainly, because this runs from a terminal and nowhere else."""


def destination() -> Path:
    """Where dumps are written. Never inside the repository."""
    chosen = Path(os.environ.get("HEARTH_BACKUP_DIR") or DEFAULT_DIR).expanduser()
    resolved = chosen.resolve() if chosen.is_absolute() else (REPO / chosen).resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise BackupError(
            f"{resolved} is inside the repository. A dump is the whole database in one "
            "file; put it somewhere that is never committed (HEARTH_BACKUP_DIR)."
        )
    return resolved


def pg_dump() -> Path:
    """`pg_dump` from `PGBIN` if it is set, else from PATH."""
    binaries = os.environ.get("PGBIN")
    if binaries:
        candidate = Path(binaries) / ("pg_dump.exe" if os.name == "nt" else "pg_dump")
        if candidate.exists():
            return candidate
    found = shutil.which("pg_dump")
    if found:
        return Path(found)
    raise BackupError(
        "pg_dump was not found. Set PGBIN in .env to the folder holding the "
        "PostgreSQL binaries, or put them on PATH."
    )


def command(url: str, into: Path) -> tuple[list[str], dict[str, str]]:
    """The command and the environment it runs with. The password is in the
    environment, never in the arguments."""
    parsed = make_url(url)
    arguments = [
        str(pg_dump()),
        "--format=custom",
        "--no-owner",
        f"--host={parsed.host or 'localhost'}",
        f"--port={parsed.port or 5432}",
        f"--username={parsed.username or ''}",
        f"--dbname={parsed.database or ''}",
        f"--file={into}",
    ]
    environment = dict(os.environ)
    if parsed.password:
        environment["PGPASSWORD"] = parsed.password
    return arguments, environment


def prune(folder: Path, keep: int = KEEP) -> list[Path]:
    """Delete all but the newest `keep` dumps. Returns what was deleted."""
    dumps = sorted(folder.glob("hearth-*.dump"), key=lambda p: p.name, reverse=True)
    removed = []
    for old in dumps[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def run(now: dt.datetime | None = None) -> Path:
    from config import get_settings

    folder = destination()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    into = folder / f"hearth-{stamp}.dump"

    arguments, environment = command(get_settings().database_url, into)
    finished = subprocess.run(arguments, env=environment, capture_output=True, text=True)
    if finished.returncode != 0:
        # pg_dump's own message, which names the database and never a row.
        raise BackupError(f"pg_dump failed: {finished.stderr.strip().splitlines()[-1:]}")
    prune(folder)
    return into


if __name__ == "__main__":
    try:
        written = run()
    except BackupError as problem:
        print(problem, file=sys.stderr)
        raise SystemExit(1) from problem
    print(f"backed up to {written} ({written.stat().st_size / 2**20:.1f} MiB)")
    print(f"keeping the newest {KEEP}; restore with pg_restore --clean --if-exists")
