"""Reading a figure out of an export cell.

Strict on purpose: every accepted form below is unambiguous, and anything else
fails with the cell's shape rather than a guess at what it meant. A lenient
parser that reads `1.234,56` as one thousand and something is a net worth that
is wrong by a factor nobody notices.
"""

from decimal import Decimal

import pytest

from ingest.values import (
    ValueFormatError,
    looks_like_account_number,
    parse_money,
    parse_price,
    parse_quantity,
    parse_weight,
    shape,
)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("$1,234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1234.56", "1234.56"),
        ("$0.00", "0.00"),
        ("$5", "5"),
        ("-$1,800.00", "-1800.00"),
        ("+$125.63", "125.63"),
        ("$-1,800.00", "-1800.00"),
        ("($1,800.00)", "-1800.00"),
        ("$1,234,567.89", "1234567.89"),
        (" $12.30 ", "12.30"),
    ],
)
def test_us_currency_is_read_exactly(cell: str, expected: str) -> None:
    assert parse_money(cell) == Decimal(expected)


@pytest.mark.parametrize(
    "cell",
    [
        "",
        "--",
        "n/a",
        "1.234,56",  # European grouping: not a US amount, and not guessed at
        "$1,23.45",  # misgrouped
        "$12.345",  # a third decimal place would be rounded away
        "12.5%",
        "USD 12.00",
        "$ 12.00",
        "--$12.00",
        "(-$12.00)",
        "1e3",
    ],
)
def test_anything_else_is_refused(cell: str) -> None:
    with pytest.raises(ValueFormatError):
        parse_money(cell)


def test_a_price_keeps_its_precision_and_is_never_negative() -> None:
    assert parse_price("$72.1234") == Decimal("72.1234")
    assert parse_price("$1.00") == Decimal("1.00")
    with pytest.raises(ValueFormatError):
        parse_price("$1.1234567")
    with pytest.raises(ValueFormatError):
        parse_price("-$1.00")


def test_a_quantity_is_a_plain_decimal() -> None:
    assert parse_quantity("100.5") == Decimal("100.5")
    assert parse_quantity("1,250") == Decimal("1250")
    assert parse_quantity("0.12345678") == Decimal("0.12345678")
    # A short position is a negative quantity, and it is still a quantity.
    assert parse_quantity("-10") == Decimal("-10")
    with pytest.raises(ValueFormatError):
        parse_quantity("$100")
    with pytest.raises(ValueFormatError):
        parse_quantity("0.123456789")


def test_the_error_carries_the_shape_never_the_value() -> None:
    """An import error gets read, pasted, and asked about. What it must not
    carry along with it is the balance."""
    with pytest.raises(ValueFormatError) as caught:
        parse_money("$18,432.117")

    message = str(caught.value)
    assert "$##,###.###" in message
    assert "18" not in message and "432" not in message


def test_shape_masks_every_digit_and_bounds_the_length() -> None:
    assert shape("-$1,800.00") == "-$#,###.##"
    assert shape("FAKEX") == "FAKEX"
    assert len(shape("x" * 500)) <= 43


@pytest.mark.parametrize(
    "text",
    ["Checking 1234", "Z23-456789", "****1234", "XXXX5678", "Account 1 2 3 4 5 6"],
)
def test_an_account_number_or_its_last_four_is_recognised(text: str) -> None:
    """Rule 4: not masked, not last-four. Four digits in a row in an account's
    name could be either, and the cost of a false alarm is choosing a name."""
    assert looks_like_account_number(text)


@pytest.mark.parametrize(
    "text", ["Roth IRA", "Joint Brokerage", "401(k)", "Emergency Savings", "HSA 2"]
)
def test_an_ordinary_name_is_not(text: str) -> None:
    assert not looks_like_account_number(text)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("225", "225"), ("235.5", "235.5"), (" 1,005.25 ", "1005.25"), ("0.125", "0.125")],
)
def test_a_weight_is_a_plain_non_negative_decimal(cell: str, expected: str) -> None:
    assert parse_weight(cell) == Decimal(expected)


@pytest.mark.parametrize("cell", ["-5", "225.1234", "225 lb", "$225", "", "2.2.5"])
def test_a_weight_that_cannot_be_stored_as_typed_is_refused(cell: str) -> None:
    """A fourth decimal place is refused, not rounded: the column holds three,
    and rounding would happen silently."""
    with pytest.raises(ValueFormatError):
        parse_weight(cell)
