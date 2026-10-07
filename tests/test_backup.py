"""The database's only copy.

Imports can be re-run from the CSVs; hand-entered balances, threads and the
model log cannot. What is tested here is the part that has to be right before
anyone needs a restore: where dumps go, what the command is, and that the
password is not in it.
"""

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from scripts import backup

URL = "postgresql://hearth:s3cret@localhost:5432/hearth"


def test_the_password_is_never_in_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL on the command line puts the password in the process list, where
    any other process on this machine can read it."""
    monkeypatch.setattr(backup, "pg_dump", lambda: Path("pg_dump"))

    arguments, environment = backup.command(URL, Path("/tmp/x.dump"))

    assert "s3cret" not in " ".join(arguments)
    assert environment["PGPASSWORD"] == "s3cret"
    assert "--username=hearth" in arguments and "--dbname=hearth" in arguments
    assert "--format=custom" in arguments


def test_a_dump_may_not_land_in_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dump is the whole database in one file. The repository is the one
    place it must never be."""
    monkeypatch.setenv("HEARTH_BACKUP_DIR", str(backup.REPO / "backups"))

    with pytest.raises(backup.BackupError, match="inside the repository"):
        backup.destination()


def test_a_chosen_folder_outside_the_repository_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEARTH_BACKUP_DIR", str(tmp_path / "dumps"))

    assert backup.destination() == (tmp_path / "dumps").resolve()


def test_pruning_keeps_the_newest(tmp_path: Path) -> None:
    for day in range(1, 6):
        (tmp_path / f"hearth-2026090{day}-120000.dump").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a dump", encoding="utf-8")

    removed = backup.prune(tmp_path, keep=2)

    kept = sorted(p.name for p in tmp_path.glob("hearth-*.dump"))
    assert kept == ["hearth-20260904-120000.dump", "hearth-20260905-120000.dump"]
    assert len(removed) == 3
    assert (tmp_path / "notes.txt").exists(), "only dumps are pruned"


def test_each_dump_is_named_for_when_it_was_taken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEARTH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(backup, "pg_dump", lambda: Path("pg_dump"))
    monkeypatch.setattr(backup, "pg_restore", lambda: Path("pg_restore"))
    seen: dict[str, object] = {}

    def _ran(arguments: list[str], **kwargs: object) -> object:
        if arguments[0] == "pg_dump":
            seen["file"] = [a for a in arguments if a.startswith("--file=")][0]
            (tmp_path / "hearth-20260922-201500.dump").write_text("dump", encoding="utf-8")
        return type("Finished", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", _ran)

    written = backup.run(now=dt.datetime(2026, 9, 22, 20, 15, 0))

    assert written.name == "hearth-20260922-201500.dump"
    assert seen["file"] == f"--file={written}"


def test_a_failed_dump_says_so_without_inventing_a_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEARTH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(backup, "pg_dump", lambda: Path("pg_dump"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: type("Finished", (), {"returncode": 1, "stderr": "could not connect"})(),
    )

    with pytest.raises(backup.BackupError, match="pg_dump failed"):
        backup.run()


def _finished(returncode: int, stderr: str = "") -> object:
    return type("Finished", (), {"returncode": returncode, "stderr": stderr})()


def test_every_dump_is_read_back_before_it_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dump that cannot be listed cannot be restored."""
    monkeypatch.setenv("HEARTH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(backup, "pg_dump", lambda: Path("pg_dump"))
    monkeypatch.setattr(backup, "pg_restore", lambda: Path("pg_restore"))
    ran: list[list[str]] = []

    def _ran(arguments: list[str], **kwargs: object) -> object:
        ran.append(arguments)
        return _finished(0)

    monkeypatch.setattr(subprocess, "run", _ran)

    written = backup.run(now=dt.datetime(2026, 10, 7, 9, 0, 0))

    assert [a[0] for a in ran] == ["pg_dump", "pg_restore"]
    assert ran[1] == ["pg_restore", "--list", str(written)]


def test_a_dump_that_cannot_be_read_back_is_deleted_and_nothing_is_pruned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No file is left looking like a backup that is not one, and the older
    good ones are not pruned to make room for it."""
    monkeypatch.setenv("HEARTH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(backup, "pg_dump", lambda: Path("pg_dump"))
    monkeypatch.setattr(backup, "pg_restore", lambda: Path("pg_restore"))
    for day in range(1, 4):
        (tmp_path / f"hearth-2026090{day}-120000.dump").write_text("x", encoding="utf-8")
    broken = tmp_path / "hearth-20261007-090000.dump"

    def _ran(arguments: list[str], **kwargs: object) -> object:
        if arguments[0] == "pg_dump":
            broken.write_text("truncated", encoding="utf-8")
            return _finished(0)
        return _finished(1, "pg_restore: error: could not read input file")

    monkeypatch.setattr(subprocess, "run", _ran)
    monkeypatch.setattr(backup, "KEEP", 2)

    with pytest.raises(backup.BackupError, match="could not be read back"):
        backup.run(now=dt.datetime(2026, 10, 7, 9, 0, 0))

    assert not broken.exists()
    assert len(list(tmp_path.glob("hearth-*.dump"))) == 3
