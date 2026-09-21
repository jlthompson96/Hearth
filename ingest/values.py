"""Reading a figure out of an export cell, strictly.

US currency and plain decimals, in forms that cannot be misread, and nothing
that needs a guess. A cell that does not match fails loudly.

The error carries the cell's *shape* — every digit replaced by `#` — and never
its value. An import error is the thing that gets read aloud, pasted into a
question, asked about; `'$##,###.###' is not a US dollar amount` says exactly
what is wrong with the file without saying what is in the account.
"""

import re
from decimal import Decimal

#: 1234, 1,234 or 1,234,567 — grouped properly or not at all — then any
#: decimal places. How many places are allowed is the caller's business.
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

_MONEY = re.compile(
    rf"""^(?:
        (?P<sign>[+-])?\$?(?P<plain>{_NUMBER})   # $1.00  -$1.00  +$1.00  1.00
      | \$(?P<inner>-)(?P<signed>{_NUMBER})      # $-1.00
      | \(\$?(?P<bracketed>{_NUMBER})\)          # ($1.00), the accounting negative
    )$""",
    re.VERBOSE,
)

_QUANTITY = re.compile(rf"^(?P<sign>[+-])?(?P<plain>{_NUMBER})$")

_SHAPE_LIMIT = 40


class ValueFormatError(ValueError):
    """A cell that is not the kind of figure its column promises."""


def shape(cell: str) -> str:
    """The cell with every digit masked, bounded in length. Safe to show."""
    masked = re.sub(r"\d", "#", cell.strip())
    return masked if len(masked) <= _SHAPE_LIMIT else masked[:_SHAPE_LIMIT] + "..."


def _places(number: str) -> int:
    return len(number.partition(".")[2])


def _decimal(number: str, *, negative: bool) -> Decimal:
    value = Decimal(number.replace(",", ""))
    return -value if negative else value


def parse_money(cell: str) -> Decimal:
    """A US dollar amount, to the cent. A third decimal place is refused rather
    than rounded: rounding is arithmetic, and it would happen silently."""
    return _money(cell, places=2, what="a US dollar amount (at most two decimal places)")


def parse_price(cell: str) -> Decimal:
    """A unit price: dollars, up to six places, never negative."""
    value = _money(cell, places=6, what="a unit price (at most six decimal places)")
    if value < 0:
        raise ValueFormatError(f"{shape(cell)!r} is a negative price")
    return value


def parse_quantity(cell: str) -> Decimal:
    """A share count: a plain decimal, up to eight places. Signed, because a
    short position is still a position."""
    match = _QUANTITY.match(cell.strip())
    if not match or _places(match["plain"]) > 8:
        raise ValueFormatError(
            f"{shape(cell)!r} is not a quantity (a plain number, at most eight decimal places)"
        )
    return _decimal(match["plain"], negative=match["sign"] == "-")


def _money(cell: str, *, places: int, what: str) -> Decimal:
    match = _MONEY.match(cell.strip())
    if match:
        number = match["plain"] or match["signed"] or match["bracketed"]
        negative = match["sign"] == "-" or bool(match["inner"]) or bool(match["bracketed"])
        if _places(number) <= places:
            return _decimal(number, negative=negative)
    raise ValueFormatError(f"{shape(cell)!r} is not {what}")


#: Four digits in a row, allowing a space or hyphen between them. That is what
#: an account number, a masked one, or a last four all have in common.
_ACCOUNT_NUMBER = re.compile(r"\d(?:[ -]?\d){3,}")


def looks_like_account_number(text: str) -> bool:
    """Whether a name could be carrying an account number or part of one.

    Rule 4 says absent — not masked, not last-four — and a label like
    "Checking 1234" is a last four with a word in front of it. This errs toward
    refusing: "Roth 2024" is refused too, and the cost of that false alarm is
    choosing a different name, where the cost of a miss is an identifier in
    the database.
    """
    return _ACCOUNT_NUMBER.search(text) is not None
