"""Fidelity's positions export ("Portfolio_Positions_<Mon>-<DD>-<YYYY>.csv").

Five of its fifteen columns become a snapshot: which account, which symbol, how
many, at what price, worth how much. The other ten — description, type, the
day's and total gain and loss, percentages, cost basis — are Fidelity's own
derived figures. They stay in `import_row` untouched, so cost basis in
particular can be read later without anyone re-downloading anything.

There is no balance column. An account's balance is the sum of its rows'
`Current value`, computed here. Rule 1 is about the model: arithmetic belongs in
Python, deterministic and tested, and this is that. The first real import is
the check that it matches the total Fidelity shows.

What the header says was read from a real download. What the rows under it look
like was not — only the header was ever shared — so anything a row does that
this does not expect is refused with the row, the column and the cell's shape,
rather than read as something plausible.
"""

import datetime as dt
import re
from collections.abc import Callable, Sequence
from decimal import Decimal

from ingest.errors import RowRefused
from ingest.normalizer import Holding, Normalized, RawRow
from ingest.values import ValueFormatError, parse_money, parse_price, parse_quantity

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: Not `\b`: in "Positions_Sep-21-2026" the underscore is a word character, so
#: there is no word boundary before the month.
_FILENAME_DATE = re.compile(rf"(?<![A-Za-z])({'|'.join(_MONTHS)})-(\d{{2}})-(\d{{4}})(?!\d)")

#: An optional figure that is not there. Blank, or the double dash exports use
#: for "not applicable" — neither can be mistaken for a number.
_ABSENT = {"", "--"}


class FidelityPositions:
    name: str = "fidelity-positions-v1"
    source_label: str = "Fidelity positions"
    header: tuple[str, ...] = (
        "Account name",
        "Symbol",
        "Description",
        "Quantity",
        "Last price",
        "Last price change",
        "Current value",
        "Today's gain/loss dollar",
        "Today's gain/loss percent",
        "Total gain/loss dollar",
        "Total gain/loss percent",
        "Percent of account",
        "Cost basis total",
        "Average cost basis",
        "Type",
    )
    identifying_columns: tuple[str, ...] = ("Account name",)

    def date_in_filename(self, filename: str) -> dt.date | None:
        match = _FILENAME_DATE.search(filename)
        if not match:
            return None
        month, day, year = match.groups()
        try:
            return dt.date(int(year), _MONTHS.index(month) + 1, int(day))
        except ValueError:
            return None

    def normalize(self, rows: Sequence[RawRow]) -> Normalized:
        holdings: list[Holding] = []
        first_seen: dict[tuple[str, str], int] = {}
        balances: dict[str, Decimal] = {}

        for row in rows:
            account = _required_text(row, "Account name")
            symbol = _required_text(row, "Symbol")

            earlier = first_seen.setdefault((account, symbol), row.number)
            if earlier != row.number:
                raise RowRefused(
                    f"{symbol} appears twice in {account!r} (rows {earlier} and "
                    f"{row.number}). That can mean one position held two ways — in "
                    "cash and on margin — and adding them together would be a guess "
                    "about what the export meant."
                )

            holding = Holding(
                row=row.number,
                account=account,
                symbol=symbol,
                quantity=_optional(row, "Quantity", parse_quantity),
                price=_optional(row, "Last price", parse_price),
                market_value=_required(row, "Current value", parse_money),
            )
            holdings.append(holding)
            balances[account] = balances.get(account, Decimal("0")) + holding.market_value

        return Normalized(holdings=tuple(holdings), balances=balances)


def _cell(row: RawRow, column: str) -> str:
    return row.cells.get(column, "").strip()


def _required_text(row: RawRow, column: str) -> str:
    value = _cell(row, column)
    if not value:
        raise RowRefused(f"row {row.number}, {column}: blank")
    return value


def _required(row: RawRow, column: str, parse: Callable[[str], Decimal]) -> Decimal:
    value = _cell(row, column)
    if value in _ABSENT:
        raise RowRefused(f"row {row.number}, {column}: blank, and every row needs one")
    return _parsed(row, column, value, parse)


def _optional(row: RawRow, column: str, parse: Callable[[str], Decimal]) -> Decimal | None:
    value = _cell(row, column)
    return None if value in _ABSENT else _parsed(row, column, value, parse)


def _parsed(row: RawRow, column: str, value: str, parse: Callable[[str], Decimal]) -> Decimal:
    try:
        return parse(value)
    except ValueFormatError as error:
        raise RowRefused(f"row {row.number}, {column}: {error}") from None
