"""Data & imports: the exports waiting in the data folder, importing one, and
the history of what has been imported.

Nothing here accepts a file upload or a path. A request names a file, by name,
that already sits in `$HEARTH_DATA_DIR`; the export never travels through the
browser, and the API has no way to reach a file anywhere else.

Reads go through the read-only role, like everything else that only looks.
Writes use `db.writer`, the one read-write connection outside migrations.
"""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from api.refusals import REFUSALS
from db.session import readonly_connection
from db.writer import writer_connection
from ingest import datadir
from ingest.errors import DataDirProblem
from ingest.export import NORMALIZERS
from ingest.importer import ImportResult, fixture_loaded, import_export, remove_import
from ingest.queries import batches, imported_hashes

router = APIRouter(prefix="/api/imports", tags=["imports"])


class ExportFile(BaseModel):
    name: str
    size_bytes: int
    modified_at: dt.datetime
    #: The date the filename states, if it states one — offered as the import's
    #: date, which is still the person's to confirm.
    date_in_name: dt.date | None
    imported_batch: uuid.UUID | None
    imported_as_of: dt.date | None


class ExportListing(BaseModel):
    data_dir: str | None
    #: Why the folder cannot be read, when it cannot. Shown, not thrown: the
    #: rest of the screen still works.
    problem: str | None
    fixture_loaded: bool
    files: list[ExportFile]


class ImportRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    #: Required, with no default. The date an export describes is the person's
    #: to state (CLAUDE.md: `as_of` never defaults to today).
    as_of: dt.date


class AccountImported(BaseModel):
    label: str
    balance: Decimal
    holdings: int


class ImportOutcome(BaseModel):
    batch_id: uuid.UUID
    filename: str
    source_label: str
    as_of: dt.date
    rows: int
    already_imported: bool
    accounts: list[AccountImported]
    #: Weigh-ins recorded, for a weight history; 0 for a positions export.
    body_weights: int


class Batch(BaseModel):
    id: uuid.UUID
    filename: str
    source_label: str
    normalizer: str
    as_of: dt.date
    rows: int
    status: str
    imported_at: dt.datetime


def _date_in_name(name: str) -> dt.date | None:
    for normalizer in NORMALIZERS:
        stated = normalizer.date_in_filename(name)
        if stated is not None:
            return stated
    return None


@router.get("/files", response_model=ExportListing, summary="Exports in the data folder")
def list_files() -> ExportListing:
    with readonly_connection() as conn:
        loaded = fixture_loaded(conn)
        known = imported_hashes(conn)

    try:
        folder = datadir.data_dir()
        found = datadir.list_exports(folder)
    except DataDirProblem as problem:
        return ExportListing(data_dir=None, problem=str(problem), fixture_loaded=loaded, files=[])

    return ExportListing(
        data_dir=str(folder),
        problem=None,
        fixture_loaded=loaded,
        files=[
            ExportFile(
                name=f.name,
                size_bytes=f.size,
                modified_at=f.modified,
                date_in_name=_date_in_name(f.name),
                imported_batch=known[f.sha256][0] if f.sha256 in known else None,
                imported_as_of=known[f.sha256][1] if f.sha256 in known else None,
            )
            for f in found
        ],
    )


@router.post(
    "",
    response_model=ImportOutcome,
    responses=REFUSALS,
    summary="Import one export from the data folder",
)
def create_import(request: ImportRequest) -> ImportOutcome:
    folder = datadir.data_dir()
    content = datadir.read_export_file(folder, request.filename)
    with writer_connection() as conn:
        result = import_export(
            conn, content=content, filename=request.filename, as_of=request.as_of
        )
    return _outcome(result)


@router.get("", response_model=list[Batch], summary="Everything imported, newest first")
def list_imports() -> list[Batch]:
    with readonly_connection() as conn:
        return [Batch(**vars(b)) for b in batches(conn)]


@router.delete(
    "/{batch_id}",
    status_code=204,
    responses=REFUSALS,
    summary="Take an import back out, with its snapshots",
)
def delete_import(batch_id: uuid.UUID) -> Response:
    with writer_connection() as conn:
        remove_import(conn, batch_id)
    return Response(status_code=204)


def _outcome(result: ImportResult) -> ImportOutcome:
    return ImportOutcome(
        batch_id=result.batch_id,
        filename=result.filename,
        source_label=result.source_label,
        as_of=result.as_of,
        rows=result.rows,
        already_imported=result.already_imported,
        accounts=[AccountImported(**vars(a)) for a in result.accounts],
        body_weights=result.body_weights,
    )
