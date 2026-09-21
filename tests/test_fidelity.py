"""Reading a Fidelity positions export, before anything is written.

Everything here happens before the database is touched. That is where the
checks that matter most have to live: an account number that reaches
`import_row` has already broken rule 4, however quickly something notices
afterwards.
"""

import datetime as dt
from decimal import Decimal

import pytest

from ingest.errors import AccountNumberRefused, MalformedExport, RowRefused, UnknownLayout
from ingest.export import read_export
from ingest.fidelity import FidelityPositions
from tests.fake_exports import BALANCES, FILENAME, HEADER, ROWS, export


def test_the_real_header_selects_the_fidelity_normalizer() -> None:
    parsed = read_export(export(), FILENAME)

    assert isinstance(parsed.normalizer, FidelityPositions)
    assert [row.number for row in parsed.rows] == [2, 3, 4, 5]


def test_the_footer_is_not_data() -> None:
    parsed = read_export(export(), FILENAME)

    assert all(row.cells["Account name"] in BALANCES for row in parsed.rows)


def test_raw_rows_are_keyed_by_the_export_s_own_header_and_not_reshaped() -> None:
    """The recovery path: a normalizer bug found in six months is fixed by
    re-reading these, so they are the cells as they arrived."""
    parsed = read_export(export(), FILENAME)

    first = parsed.rows[0].cells
    assert first["Current value"] == "$20,100.00"
    assert first["Today's gain/loss percent"] == "+0.63%"
    assert first["Type"] == "Cash"


@pytest.mark.parametrize("bom", [True, False])
@pytest.mark.parametrize("newline", ["\r\n", "\n"])
def test_line_endings_and_a_byte_order_mark_are_not_a_different_layout(
    bom: bool, newline: str
) -> None:
    assert len(read_export(export(bom=bom, newline=newline), FILENAME).rows) == 4


def test_balances_are_the_sum_of_each_account_s_positions() -> None:
    """No balance column exists, so the account total is summed here — in
    Python, where rule 1 wants arithmetic, and checked to the cent."""
    normalized = FidelityPositions().normalize(read_export(export(), FILENAME).rows)

    assert {k: str(v) for k, v in normalized.balances.items()} == BALANCES


def test_a_cash_line_has_a_value_and_no_share_count() -> None:
    normalized = FidelityPositions().normalize(read_export(export(), FILENAME).rows)
    cash = next(h for h in normalized.holdings if h.symbol == "CASHX**")

    assert cash.quantity is None
    assert cash.price is None
    assert cash.market_value == Decimal("5025.00")


def test_a_position_keeps_quantity_and_price_as_exported() -> None:
    normalized = FidelityPositions().normalize(read_export(export(), FILENAME).rows)
    fund = next(h for h in normalized.holdings if h.symbol == "FAKEX")

    assert fund.quantity == Decimal("100.5")
    assert fund.price == Decimal("200.00")
    assert fund.account == "Joint Brokerage"


def test_the_date_in_the_filename_is_read() -> None:
    assert FidelityPositions().date_in_filename(FILENAME) == dt.date(2026, 9, 21)
    assert FidelityPositions().date_in_filename("positions.csv") is None


# --- refused before anything is stored ----------------------------------------


def test_an_account_number_column_is_refused_by_name() -> None:
    """Other versions of this export have led with an Account Number column.
    If it comes back, the file is refused before a single row is stored."""
    header = "Account Number," + HEADER
    rows = tuple("Z00000000," + row for row in ROWS)

    with pytest.raises(AccountNumberRefused) as caught:
        read_export(export(*rows, header=header), FILENAME)

    assert "Account Number" in str(caught.value)
    assert "Z00000000" not in str(caught.value)


def test_an_account_name_carrying_digits_is_refused_without_repeating_them() -> None:
    rows = (ROWS[0].replace("Joint Brokerage", "Brokerage 4417", 1),)

    with pytest.raises(AccountNumberRefused) as caught:
        read_export(export(*rows), FILENAME)

    assert "row 2" in str(caught.value)
    assert "4417" not in str(caught.value)


def test_a_filename_carrying_an_account_number_is_refused() -> None:
    with pytest.raises(AccountNumberRefused):
        read_export(export(), "History_for_Account_Z12345678.csv")


def test_an_unknown_layout_names_its_columns_and_the_nearest_known_one() -> None:
    header = HEADER.replace("Current value", "Market value")

    with pytest.raises(UnknownLayout) as caught:
        read_export(export(header=header), FILENAME)

    message = str(caught.value)
    assert "Market value" in message
    assert "Fidelity positions" in message
    assert "missing ['Current value']" in message


def test_the_header_must_match_exactly_including_case() -> None:
    with pytest.raises(UnknownLayout):
        read_export(export(header=HEADER.replace("Account name", "Account Name")), FILENAME)


def test_a_row_wider_than_the_header_is_malformed() -> None:
    with pytest.raises(MalformedExport, match="row 2"):
        read_export(export(ROWS[0] + ",surplus"), FILENAME)


def test_data_after_the_blank_line_is_not_mistaken_for_a_footer() -> None:
    footer = ("", ROWS[3])

    with pytest.raises(MalformedExport, match="blank line"):
        read_export(export(*ROWS[:3], footer=footer), FILENAME)


def test_a_file_with_no_rows_is_malformed() -> None:
    with pytest.raises(MalformedExport):
        read_export(b"", FILENAME)
    with pytest.raises(MalformedExport, match="no rows"):
        read_export(("\ufeff" + HEADER + "\r\n").encode(), FILENAME)


def test_a_file_that_is_not_utf8_is_malformed() -> None:
    with pytest.raises(MalformedExport):
        read_export(HEADER.encode() + b"\r\n\xff\xfe\x00", FILENAME)


# --- refused during normalization ----------------------------------------------


def _normalize(*rows: str) -> None:
    FidelityPositions().normalize(read_export(export(*rows), FILENAME).rows)


def test_a_value_that_is_not_currency_names_row_column_and_shape() -> None:
    row = ROWS[3].replace('"$21,600.00"', '"21.600,00 USD"')

    with pytest.raises(RowRefused) as caught:
        _normalize(row)

    message = str(caught.value)
    assert "row 2" in message and "Current value" in message
    assert "##.###,## USD" in message
    assert "21" not in message


def test_a_blank_current_value_is_refused() -> None:
    with pytest.raises(RowRefused, match="Current value"):
        _normalize("Roth IRA,FAKEB,FAKE BOND FUND,300,$72.00,,,,,,,,,,Cash")


def test_the_same_symbol_twice_in_one_account_is_refused_not_merged() -> None:
    """Held in both cash and margin, one symbol can appear twice. Summing the
    two would be a decision about what the export meant; refusing is not."""
    with pytest.raises(RowRefused, match="rows 2 and 3"):
        _normalize(ROWS[0], ROWS[0].replace(",Cash", ",Margin"))


def test_a_position_you_owe_has_a_negative_quantity_and_value() -> None:
    """The first real import carried one of these and the fixture had none: a
    negative share count with a negative value — a short position or a written
    option. It is a liability, the account's own total subtracts it, and Hearth
    stores the value the export states rather than multiplying anything out."""
    written = (
        "Roth IRA,-FAKE261016C100,CALL (FAKE) OCT 16 26 $100,-1,$2.50,+$0.10,-$250.00,"
        "-$10.00,-4.17%,+$50.00,+16.67%,-1.17%,$300.00,$3.00,Cash"
    )
    normalized = FidelityPositions().normalize(read_export(export(ROWS[3], written), FILENAME).rows)
    option = next(h for h in normalized.holdings if h.symbol.startswith("-FAKE"))

    assert option.quantity == Decimal("-1")
    assert option.market_value == Decimal("-250.00")
    # 21,600.00 - 250.00: the sum the balance is, checked to the cent.
    assert normalized.balances["Roth IRA"] == Decimal("21350.00")
