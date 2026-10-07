"""Every table, as JSON lines anyone can read — and a load that puts it back.

`make backup` keeps a `pg_dump`, which only Postgres can read back. This is the
open copy: one `<table>.jsonl` per table and a `manifest.json`, readable by any
language and any database, written for the day Hearth moves somewhere a dump
cannot follow. `make export` writes one; `make load-export DIR=<folder>` puts one
back into an empty database at the same migration.

## What a file holds

One JSON object per row, keys in column order, rows in primary-key order, so the
same data writes the same bytes. Values keep their meaning exactly:

- NUMERIC — every money and measurement column — as a **string**, `"38250.00"`.
  A JSON number is a float in most readers, and a float is how a balance stops
  being the balance.
- Dates as `YYYY-MM-DD`; timestamps as ISO 8601 with their UTC offset.
- UUIDs as strings; JSONB columns (raw CSV rows, tool calls, the model log) as
  the JSON they hold.

The schema is the migrations at the manifest's `alembic_revision`; this is
data only. The manifest also records each file's row count and SHA-256, the
commit that wrote it, and the chat and embedding models in use — the embedding
model so vectors, once there are any, can be judged rebuildable.

## Where it goes

`HEARTH_EXPORT_DIR`, or `%LOCALAPPDATA%\\Hearth\\exports` (`~/.local/share/...`
elsewhere), in a folder named for when it was taken — never inside the
repository. It is every balance, thread and model-log entry in plain text, kept
where the dumps are kept.

An export reads through the read-only role, so taking one cannot change
anything. A load writes, and refuses unless the target is empty, at the same
revision, and every file matches the checksum its manifest recorded.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa

# Importing the models is what puts the tables on Base.metadata. Without it, a
# fresh process — `make export` — found no tables and wrote an empty export.
import db.models  # noqa: F401
from db.base import Base
from ingest.datadir import REPO, inside_repository

#: Bumped if what a file holds ever changes shape.
FORMAT = 1

DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "Hearth/exports"

#: How many rows go to the database in one statement on a load.
BATCH = 500


class ExportError(RuntimeError):
    """Said plainly, because this runs from a terminal."""


def destination(now: dt.datetime) -> Path:
    """A new folder for one export, named for when it was taken."""
    chosen = Path(os.environ.get("HEARTH_EXPORT_DIR") or DEFAULT_DIR).expanduser()
    root = chosen.resolve() if chosen.is_absolute() else (REPO / chosen).resolve()
    if inside_repository(root):
        raise ExportError(
            f"{root} is inside the repository. An export is every balance and thread in "
            "plain text; put it somewhere that is never committed (HEARTH_EXPORT_DIR)."
        )
    return root / f"hearth-{now:%Y%m%d-%H%M%S}"


def tables() -> list[sa.Table]:
    """Every table the schema defines, parents before children."""
    found = list(Base.metadata.sorted_tables)
    if not found:
        # An empty export would read as an empty database.
        raise ExportError("No tables are defined: the schema's models were not loaded.")
    return found


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _decoder(column: sa.Column[Any]) -> Any:
    kind = column.type
    if isinstance(kind, sa.Numeric):
        return Decimal
    if isinstance(kind, sa.DateTime):
        return dt.datetime.fromisoformat
    if isinstance(kind, sa.Date):
        return dt.date.fromisoformat
    if isinstance(kind, sa.Uuid):
        return uuid.UUID
    return None


def _revision(conn: sa.Connection) -> str | None:
    found = conn.execute(sa.text("select version_num from alembic_version")).scalar()
    return str(found) if found is not None else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip() or None


def _models() -> dict[str, str | None]:
    try:
        from config import get_model_settings

        settings = get_model_settings()
    except Exception:  # noqa: BLE001 - an export does not need a model configured
        return {"chat_model": None, "embedding_model": None}
    return {"chat_model": settings.chat_model, "embedding_model": settings.embedding_model}


def export(conn: sa.Connection, folder: Path, *, now: dt.datetime) -> dict[str, Any]:
    """Write every table to `folder`, which must not exist yet, and its
    manifest. Returns the manifest."""
    folder.mkdir(parents=True, exist_ok=False)
    written: dict[str, dict[str, Any]] = {}
    for table in tables():
        path = folder / f"{table.name}.jsonl"
        order = list(table.primary_key.columns)
        count = 0
        with path.open("w", encoding="utf-8", newline="\n") as out:
            for row in conn.execute(sa.select(table).order_by(*order)).mappings():
                record = {name: _encode(value) for name, value in row.items()}
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        written[table.name] = {"rows": count, "sha256": _sha256(path)}

    manifest = {
        "format": FORMAT,
        "exported_at": now.astimezone(dt.UTC).isoformat(),
        "alembic_revision": _revision(conn),
        "git_sha": _git_sha(),
        **_models(),
        "tables": written,
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def load(conn: sa.Connection, folder: Path) -> dict[str, int]:
    """Put an export back. Refuses unless the target database is at the
    export's revision, every table it names is empty, and every file is the
    file its manifest describes. Returns the rows loaded per table."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExportError(f"{folder} holds no readable manifest.json ({error}).") from None
    if manifest.get("format") != FORMAT:
        raise ExportError(f"This reads export format {FORMAT}; that is {manifest.get('format')}.")

    here = _revision(conn)
    if here != manifest["alembic_revision"]:
        raise ExportError(
            f"The export was taken at revision {manifest['alembic_revision']}; this database "
            f"is at {here}. Migrate an empty database to that revision, then load."
        )

    recorded: dict[str, dict[str, Any]] = manifest["tables"]
    by_name = {table.name: table for table in tables()}
    unknown = sorted(set(recorded) - set(by_name))
    if unknown:
        raise ExportError(f"The export holds tables this schema does not: {unknown}.")
    for name, facts in recorded.items():
        path = folder / f"{name}.jsonl"
        if not path.is_file() or _sha256(path) != facts["sha256"]:
            raise ExportError(f"{path.name} is missing or is not the file the manifest recorded.")
        if conn.execute(sa.select(sa.func.count()).select_from(by_name[name])).scalar_one():
            raise ExportError(f"{name} already holds rows. A load goes into an empty database.")

    loaded: dict[str, int] = {}
    for table in tables():
        if table.name not in recorded:
            continue
        decoders = {c.name: _decoder(c) for c in table.columns}
        batch: list[dict[str, Any]] = []
        count = 0
        with (folder / f"{table.name}.jsonl").open(encoding="utf-8") as lines:
            for line in lines:
                record = json.loads(line)
                for name, value in record.items():
                    decode = decoders.get(name)
                    if decode is not None and value is not None:
                        record[name] = decode(value)
                batch.append(record)
                if len(batch) == BATCH:
                    conn.execute(table.insert(), batch)
                    count += len(batch)
                    batch = []
        if batch:
            conn.execute(table.insert(), batch)
            count += len(batch)
        if count != recorded[table.name]["rows"]:
            raise ExportError(
                f"{table.name}: loaded {count} rows; the manifest recorded "
                f"{recorded[table.name]['rows']}."
            )
        _continue_numbering(conn, table)
        loaded[table.name] = count
    return loaded


def _continue_numbering(conn: sa.Connection, table: sa.Table) -> None:
    """A table numbered by Postgres goes on from the highest id loaded, so the
    next row written does not collide with one that came back."""
    for column in table.primary_key.columns:
        if not isinstance(column.type, sa.Integer):
            continue
        sequence = conn.execute(
            sa.text("select pg_get_serial_sequence(:t, :c)"),
            {"t": table.name, "c": column.name},
        ).scalar()
        if sequence is None:
            continue
        highest = conn.execute(sa.select(sa.func.max(column))).scalar()
        if highest is not None:
            conn.execute(sa.text("select setval(:s, :v)"), {"s": sequence, "v": highest})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--load",
        type=Path,
        metavar="DIR",
        help="load this export into DATABASE_URL's database instead of writing one",
    )
    parser.add_argument(
        "--database",
        help="use this database instead of DATABASE_URL's, e.g. to check an export",
    )
    args = parser.parse_args(argv)

    from sqlalchemy.engine import make_url

    from config import get_settings

    settings = get_settings()

    def _engine(url: str) -> sa.Engine:
        chosen = make_url(url)
        return sa.create_engine(chosen.set(database=args.database) if args.database else chosen)

    try:
        if args.load is not None:
            writer = _engine(settings.database_url)
            try:
                with writer.begin() as conn:
                    loaded = load(conn, args.load)
            finally:
                writer.dispose()
            print(f"loaded {sum(loaded.values()):,} rows into {len(loaded)} tables")
            return 0

        # Local time in the folder's name, as the dumps are named; UTC in the
        # manifest. Read through the read-only role: an export changes nothing.
        now = dt.datetime.now().astimezone()
        folder = destination(now)
        reader = _engine(settings.database_url_ro)
        try:
            with reader.connect() as conn:
                manifest = export(conn, folder, now=now)
        finally:
            reader.dispose()
        rows = sum(t["rows"] for t in manifest["tables"].values())
        print(f"exported {rows:,} rows from {len(manifest['tables'])} tables to {folder}")
        print("plain text: every balance and thread is readable there. Keep it like a backup.")
        return 0
    except ExportError as problem:
        print(problem, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
