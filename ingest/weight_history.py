"""A body-weight history export: `Date, Recorded, Moving Average`.

One row per day, the weigh-in in pounds under "Recorded". The layout was first
seen as a screenshot of the file open in Excel — the header cut off at "Moving
Av", the date shown as "1/4/2017" — so the first version read M/D/YYYY and was
strict about it. The first real import (2026-09-25) refused on row 2, and the
file's structure, checked with every digit masked, said why: all 809 dates
are `####-##-##`. Excel had been re-formatting them for display. The file
writes ISO dates, which cannot be misread, so that one form is read and any
other is refused by its shape. Every weight was `###.##`.

The moving average is the app's own arithmetic. It stays in `import_row` and
never becomes a figure: a trend of a trend is not a weigh-in, and a model
handed both would report the smoothed one as what the scale said.
"""

import datetime as dt
import re
from collections.abc import Sequence

from ingest.errors import RowRefused
from ingest.normalizer import BodyReading, Normalized, RawRow
from ingest.values import ValueFormatError, parse_weight, shape

_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class WeightHistory:
    name: str = "weight-history-v1"
    source_label: str = "Body weight history"
    header: tuple[str, ...] = ("Date", "Recorded", "Moving Average")
    #: Nothing in it names an account.
    identifying_columns: tuple[str, ...] = ()

    def date_in_filename(self, filename: str) -> dt.date | None:
        # A history spans years; no one date in its name would be the one it
        # describes. The date asked for at import is when it was exported.
        return None

    def normalize(self, rows: Sequence[RawRow]) -> Normalized:
        readings: list[BodyReading] = []
        first_seen: dict[dt.date, int] = {}

        for row in rows:
            as_of = _date(row)
            recorded = row.cells.get("Recorded", "").strip()
            if not recorded:
                # A day nobody stepped on the scale: the app wrote the row for
                # its average. No weigh-in is not a weigh-in of zero.
                continue

            earlier = first_seen.setdefault(as_of, row.number)
            if earlier != row.number:
                raise RowRefused(
                    f"{as_of:%Y-%m-%d} is weighed twice (rows {earlier} and {row.number}). "
                    "Which one the scale said is not for the importer to choose."
                )
            try:
                weight = parse_weight(recorded)
            except ValueFormatError as error:
                raise RowRefused(f"row {row.number}, Recorded: {error}") from None
            if weight <= 0:
                raise RowRefused(f"row {row.number}, Recorded: a body weight is more than zero")
            readings.append(BodyReading(row=row.number, as_of=as_of, weight=weight))

        return Normalized(body_weights=tuple(readings))


def _date(row: RawRow) -> dt.date:
    cell = row.cells.get("Date", "").strip()
    match = _DATE.match(cell)
    if not match:
        raise RowRefused(
            f"row {row.number}, Date: {shape(cell)!r} is not a date written YYYY-MM-DD, the "
            "one form this layout is read in"
        )
    year, month, day = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        raise RowRefused(f"row {row.number}, Date: {shape(cell)!r} is not a date") from None
