"""The data folder: the one place the app reads real exports from.

No database and no real file is involved — every folder here is a temporary
one, and every file in it is fake.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

import config
from ingest.datadir import REPO, data_dir, list_exports, read_export_file
from ingest.errors import DataDirProblem


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Set HEARTH_DATA_DIR for one test, and make sure settings forget it after."""
    config.get_settings.cache_clear()
    yield monkeypatch
    monkeypatch.undo()
    config.get_settings.cache_clear()


def _point_at(configured: pytest.MonkeyPatch, value: str) -> None:
    configured.setenv("HEARTH_DATA_DIR", value)
    config.get_settings.cache_clear()


def test_unset_is_a_problem_that_says_what_to_set(configured: pytest.MonkeyPatch) -> None:
    _point_at(configured, "")

    with pytest.raises(DataDirProblem, match="HEARTH_DATA_DIR is not set"):
        data_dir()


def test_a_relative_path_is_refused(configured: pytest.MonkeyPatch) -> None:
    _point_at(configured, "exports")

    with pytest.raises(DataDirProblem, match="absolute"):
        data_dir()


def test_a_folder_inside_the_repository_is_refused(configured: pytest.MonkeyPatch) -> None:
    """CLAUDE.md keeps real data out of the workspace. This is the check that
    makes it more than a sentence."""
    _point_at(configured, str(REPO / "tests"))

    with pytest.raises(DataDirProblem, match="inside this repository"):
        data_dir()


def test_a_folder_that_does_not_exist_is_refused(
    configured: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _point_at(configured, str(tmp_path / "nowhere"))

    with pytest.raises(DataDirProblem, match="not a folder"):
        data_dir()


def test_a_folder_outside_the_repository_is_used(
    configured: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _point_at(configured, str(tmp_path))

    assert data_dir() == tmp_path.resolve()


def test_only_csv_files_directly_inside_are_listed(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"x")
    (tmp_path / "B.CSV").write_bytes(b"y")
    (tmp_path / "notes.txt").write_bytes(b"z")
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "c.csv").write_bytes(b"w")

    assert sorted(f.name for f in list_exports(tmp_path)) == ["B.CSV", "a.csv"]


def test_a_listed_file_carries_the_hash_the_importer_uses(tmp_path: Path) -> None:
    from ingest.importer import file_sha256

    (tmp_path / "a.csv").write_bytes(b"content")

    [listed] = list_exports(tmp_path)
    assert listed.sha256 == file_sha256(b"content")


@pytest.mark.parametrize(
    "name",
    [
        "../escape.csv",
        "old/c.csv",
        ".." + chr(92) + "escape.csv",
        "..",
        "",
        "C:" + chr(92) + "x.csv",
    ],
)
def test_a_path_is_never_accepted_only_a_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(DataDirProblem):
        read_export_file(tmp_path, name)


def test_a_missing_file_or_a_non_csv_is_refused(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_bytes(b"z")

    with pytest.raises(DataDirProblem, match="no file named"):
        read_export_file(tmp_path, "missing.csv")
    with pytest.raises(DataDirProblem, match="not a .csv"):
        read_export_file(tmp_path, "notes.txt")


def test_a_file_in_the_folder_is_read(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes(b"content")

    assert read_export_file(tmp_path.resolve(), "a.csv") == b"content"
