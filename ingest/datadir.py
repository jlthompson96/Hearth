"""Where real exports live: `$HEARTH_DATA_DIR`, outside this repository.

The app reads exports from there and from nowhere else. The API accepts a file
*name*, never a path, and it must name a CSV directly inside that folder — so
there is no request that reaches a file anywhere else on the machine.

The folder itself must not be inside the repository. CLAUDE.md keeps real data
out of the workspace, and until now that was a sentence; this is the point
where it stops being one.
"""

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from config import get_settings
from ingest.errors import DataDirProblem

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ExportFile:
    name: str
    size: int
    modified: dt.datetime
    sha256: str


def data_dir() -> Path:
    configured = get_settings().hearth_data_dir
    # An empty `HEARTH_DATA_DIR=` in .env arrives as Path("."), not None.
    if configured is None or configured == Path(""):
        raise DataDirProblem(
            "HEARTH_DATA_DIR is not set. In .env, point it at the folder your exports "
            "download to — an absolute path outside this repository — and restart the "
            "backend."
        )
    if not configured.is_absolute():
        raise DataDirProblem(
            f"HEARTH_DATA_DIR must be an absolute path; {str(configured)!r} is relative."
        )

    folder = configured.resolve()
    if folder == REPO or REPO in folder.parents:
        raise DataDirProblem(
            "HEARTH_DATA_DIR is inside this repository. Real exports never enter the "
            "workspace — point it at a folder outside it."
        )
    if not folder.is_dir():
        raise DataDirProblem(f"HEARTH_DATA_DIR ({folder}) is not a folder that exists.")
    return folder


def list_exports(folder: Path) -> list[ExportFile]:
    """CSV files directly inside the folder, newest first. Not recursive: a
    subfolder is a place to keep things out of the list."""
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        ExportFile(
            name=p.name,
            size=p.stat().st_size,
            modified=dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.UTC),
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in files
    ]


def read_export_file(folder: Path, name: str) -> bytes:
    # A name with a separator in it — either kind, whatever the platform — is a
    # path, and paths are not accepted.
    if (
        name in {"", ".", ".."}
        or PurePosixPath(name).name != name
        or PureWindowsPath(name).name != name
    ):
        raise DataDirProblem(f"{name!r} is not a file name. Only a name is accepted, never a path.")

    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise DataDirProblem(f"There is no file named {name!r} in the data folder.")
    if path.suffix.lower() != ".csv":
        raise DataDirProblem(f"{name!r} is not a .csv file.")
    return path.read_bytes()
