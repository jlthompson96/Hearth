"""The `Normalizer` protocol: one per institution and export layout.

A normalizer is chosen by exact header match and never by resemblance. It turns
raw rows — the cells as they arrived, keyed by the export's own header — into
snapshots. It reads rows, not files, so the same code runs on an import and on
a re-normalization from `import_row` months later; that is what makes the raw
rows a recovery path rather than an archive.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class RawRow:
    #: 1-based, counting the header as row 1 — as a person reading the file
    #: in a spreadsheet would count.
    number: int
    cells: Mapping[str, str]


@dataclass(frozen=True)
class Holding:
    row: int
    #: The export's name for the account, matched exactly to a Hearth label.
    account: str
    symbol: str
    #: Absent on a cash line, which reports a value and no share count.
    quantity: Decimal | None
    price: Decimal | None
    market_value: Decimal


@dataclass(frozen=True)
class Normalized:
    holdings: tuple[Holding, ...]
    #: Account name -> balance, in the order accounts first appear.
    balances: Mapping[str, Decimal]


class Normalizer(Protocol):
    #: Recorded on every batch, so a bug found later is attributable. Bump the
    #: version when the reading of a column changes.
    name: str
    #: What the person sees: "Fidelity positions".
    source_label: str
    header: tuple[str, ...]
    #: Columns that name an account. Screened for anything resembling an account
    #: number before a single row is stored (rule 4).
    identifying_columns: tuple[str, ...]

    def date_in_filename(self, filename: str) -> dt.date | None: ...

    def normalize(self, rows: Sequence[RawRow]) -> Normalized: ...
