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
    seen: dict[str, object] = {}

    def _ran(arguments: list[str], **kwargs: object) -> object:
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
