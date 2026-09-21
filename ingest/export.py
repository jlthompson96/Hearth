"""Reading an export file into raw rows, before anything is written.

The order here is the point. By the time `read_export` returns, the file has
been matched to exactly one layout and every check that protects the database
from what is *in* the file has already run — above all rule 4. An account
number that reaches `import_row` has broken the rule however quickly something
notices afterwards, so nothing here is allowed to be "caught later".

What it will accept: a header that matches a known layout exactly, data rows up
to the first blank line, and after that only a footer — lines with at most one
non-empty cell, which is what a disclaimer or a download timestamp looks like.
A byte-order mark, CRLF line endings and trailing empty cells are tolerated,
because none of them carry data. Nothing else is.
"""

import csv
import io
import re
from dataclasses import dataclass

from ingest.errors import AccountNumberRefused, MalformedExport, UnknownLayout
from ingest.fidelity import FidelityPositions
from ingest.normalizer import Normalizer, RawRow
from ingest.values import looks_like_account_number, shape

NORMALIZERS: tuple[Normalizer, ...] = (FidelityPositions(),)

#: A column whose name says it holds an identifier Hearth never stores.
_IDENTIFIER_COLUMN = re.compile(
    r"account\s*(number|no\b|num|#)|acct|last\s*(4|four)|routing", re.IGNORECASE
)

#: Dates written with separators. A filename is checked for account numbers
#: once these are taken out, so "Sep-21-2026" is not mistaken for one.
_DATES_IN_NAMES = re.compile(
    r"[A-Za-z]{3}-\d{1,2}-\d{4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-_.]\d{1,2}[-_.]\d{4}"
)


@dataclass(frozen=True)
class Export:
    normalizer: Normalizer
    rows: tuple[RawRow, ...]


def normalizer_named(name: str) -> Normalizer:
    """The normalizer recorded on a batch, for re-reading its raw rows."""
    for normalizer in NORMALIZERS:
        if normalizer.name == name:
            return normalizer
    raise LookupError(f"no normalizer named {name!r}")


def read_export(content: bytes, filename: str) -> Export:
    _screen_filename(filename)

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MalformedExport("The file is not UTF-8 text, so it is not a CSV export.") from None

    try:
        records = [_trimmed(record) for record in csv.reader(io.StringIO(text, newline=""))]
    except csv.Error as error:
        raise MalformedExport(f"The file is not well-formed CSV: {error}.") from None

    if not records or not records[0]:
        raise MalformedExport("The file is empty.")

    header = tuple(records[0])
    normalizer = _layout(header)
    rows = _data_rows(header, records)

    for row in rows:
        for column in normalizer.identifying_columns:
            if looks_like_account_number(row.cells.get(column, "")):
                raise AccountNumberRefused(
                    f"row {row.number}: {column} looks like it carries an account number "
                    f"or its last four ({shape(row.cells[column])!r}). Hearth never stores "
                    "one, masked or not (rule 4). Give the account a name without four "
                    "digits in a row where you exported it, and export again."
                )

    return Export(normalizer=normalizer, rows=rows)


def _trimmed(record: list[str]) -> list[str]:
    """Trailing empty cells carry nothing; a trailing comma is not a column."""
    while record and not record[-1].strip():
        record.pop()
    return record


def _layout(header: tuple[str, ...]) -> Normalizer:
    for normalizer in NORMALIZERS:
        if normalizer.header == header:
            return normalizer

    identifiers = [column for column in header if _IDENTIFIER_COLUMN.search(column)]
    if identifiers:
        raise AccountNumberRefused(
            f"This file has a column Hearth never stores: {identifiers}. Accounts carry a "
            "label you choose and no number of any kind (rule 4). Export without that "
            "column if the institution allows it."
        )

    nearest = min(NORMALIZERS, key=lambda n: len(set(n.header) ^ set(header)))
    missing = [column for column in nearest.header if column not in header]
    unexpected = [column for column in header if column not in nearest.header]
    raise UnknownLayout(
        f"No importer reads this layout. Its columns are {list(header)}. Nearest known "
        f"layout, {nearest.source_label}: missing {missing}, unexpected {unexpected}"
        + (", in a different order" if not missing and not unexpected else "")
        + ". Hearth reads exactly the layouts it has been written for and never guesses "
        "at a column mapping."
    )


def _data_rows(header: tuple[str, ...], records: list[list[str]]) -> tuple[RawRow, ...]:
    rows: list[RawRow] = []
    in_footer = False

    for number, record in enumerate(records[1:], start=2):
        if in_footer:
            if sum(1 for cell in record if cell.strip()) > 1:
                raise MalformedExport(
                    f"row {number} comes after the blank line that ends the data, but it "
                    "has several filled cells, like a data row. Refusing rather than "
                    "guessing whether it belongs."
                )
            continue
        if not record:
            in_footer = True
            continue
        if len(record) > len(header):
            raise MalformedExport(
                f"row {number} has {len(record)} cells; the header has {len(header)}."
            )
        rows.append(RawRow(number=number, cells=dict(zip(header, record, strict=False))))

    if not rows:
        raise MalformedExport("The file has a header and no rows.")
    return tuple(rows)


def _screen_filename(filename: str) -> None:
    if looks_like_account_number(_DATES_IN_NAMES.sub("", filename)):
        raise AccountNumberRefused(
            f"The filename looks like it carries an account number ({shape(filename)!r}). "
            "It is stored with the import, so rename the file first (rule 4)."
        )
