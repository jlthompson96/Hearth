"""Importing an export into the database: Phase 2's exit criteria and the
recovery path.

    Importing the same file twice makes the second a no-op.
    A malformed file fails with no partial writes.

"No partial writes" is taken strictly. A file refused before anything is stored
is the easy case; the one that matters is a file whose raw rows have already
been written when normalization finds row 5 unreadable. Those raw rows must go
too, or the database holds half an import that nothing will ever finish.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from ingest.errors import (
    Conflict,
    DateMismatch,
    FixtureLoaded,
    RowRefused,
    UnknownAccounts,
    UnknownLayout,
)
from ingest.importer import import_export, renormalize
from tests.fake_exports import BALANCES, FILENAME, HEADER, ROWS, export

AS_OF = dt.date(2026, 9, 21)
TABLES = ("import_batch", "import_row", "balance_snapshot", "holding_snapshot")


def _accounts(conn: sa.Connection, *labels: str) -> None:
    for label in labels or tuple(BALANCES):
        conn.execute(
            sa.text("insert into account (label, kind) values (:label, 'brokerage')"),
            {"label": label},
        )


def _counts(conn: sa.Connection) -> dict[str, int]:
    return {t: conn.execute(sa.text(f"select count(*) from {t}")).scalar_one() for t in TABLES}


NOTHING = dict.fromkeys(TABLES, 0)


def test_an_export_becomes_one_batch_its_raw_rows_and_snapshots(conn: sa.Connection) -> None:
    _accounts(conn)

    result = import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    assert not result.already_imported
    assert {a.label: str(a.balance) for a in result.accounts} == BALANCES
    assert {a.label: a.holdings for a in result.accounts} == {"Joint Brokerage": 3, "Roth IRA": 1}
    assert _counts(conn) == {
        "import_batch": 1,
        "import_row": 4,
        "balance_snapshot": 2,
        "holding_snapshot": 4,
    }
    status, as_of = conn.execute(sa.text("select status, as_of from import_batch")).one()
    assert (status, as_of) == ("normalized", AS_OF)


def test_importing_the_same_file_twice_makes_the_second_a_no_op(conn: sa.Connection) -> None:
    """Exit criterion. The second import is recognised by the file's hash,
    before it is even parsed, and writes nothing."""
    _accounts(conn)
    first = import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)
    before = _counts(conn)

    second = import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    assert second.already_imported
    assert second.batch_id == first.batch_id
    assert second.accounts == first.accounts
    assert _counts(conn) == before


def test_the_same_file_under_a_different_date_is_refused(conn: sa.Connection) -> None:
    _accounts(conn)
    import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    with pytest.raises(Conflict, match="2026-09-21"):
        import_export(conn, content=export(), filename="renamed.csv", as_of=dt.date(2026, 9, 22))


def test_a_file_refused_before_storage_writes_nothing(conn: sa.Connection) -> None:
    """Exit criterion, the easy half: an unknown layout never reaches a table."""
    _accounts(conn)
    content = export(header=HEADER.replace("Current value", "Market value"))

    with pytest.raises(UnknownLayout):
        import_export(conn, content=content, filename=FILENAME, as_of=AS_OF)

    assert _counts(conn) == NOTHING


def test_a_row_refused_after_the_raw_rows_were_stored_leaves_nothing(conn: sa.Connection) -> None:
    """Exit criterion, the hard half. Rows 2-4 are fine and row 5 is not, so
    the batch and every raw row have been inserted by the time normalization
    fails. All of it must be gone afterwards."""
    _accounts(conn)
    bad_last_row = ROWS[3].replace('"$21,600.00"', "twenty")

    with pytest.raises(RowRefused, match="row 5"):
        import_export(conn, content=export(*ROWS[:3], bad_last_row), filename=FILENAME, as_of=AS_OF)

    assert _counts(conn) == NOTHING


def test_accounts_hearth_does_not_know_are_named_and_nothing_is_written(
    conn: sa.Connection,
) -> None:
    """The export does not say what kind of account each one is, and the label
    is the person's to choose, so accounts are never created by an import."""
    _accounts(conn, "Roth IRA")

    with pytest.raises(UnknownAccounts) as caught:
        import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    assert "'Joint Brokerage'" in str(caught.value)
    assert _counts(conn) == NOTHING


def test_the_date_in_the_filename_must_agree_with_the_date_given(conn: sa.Connection) -> None:
    _accounts(conn)

    with pytest.raises(DateMismatch):
        import_export(conn, content=export(), filename=FILENAME, as_of=dt.date(2026, 9, 20))

    assert _counts(conn) == NOTHING


def test_a_balance_already_recorded_for_that_day_is_not_overwritten(conn: sa.Connection) -> None:
    _accounts(conn)
    conn.execute(
        sa.text(
            "insert into balance_snapshot (account_id, as_of, balance) "
            "select id, :as_of, 1.00 from account where label = 'Roth IRA'"
        ),
        {"as_of": AS_OF},
    )

    with pytest.raises(Conflict) as caught:
        import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    assert "Roth IRA" in str(caught.value)
    assert "entered by hand" in str(caught.value)
    assert _counts(conn)["import_batch"] == 0


def test_a_second_download_for_the_same_day_is_a_conflict(conn: sa.Connection) -> None:
    """Different bytes, same day — a re-download after prices moved. Which one
    is right is not something the importer can know."""
    _accounts(conn)
    import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    redownload = export(*ROWS[:3], ROWS[3].replace('"$21,600.00"', '"$21,650.00"'))
    with pytest.raises(Conflict, match=FILENAME):
        import_export(conn, content=redownload, filename=FILENAME, as_of=AS_OF)


def test_a_cash_line_is_stored_without_a_quantity(conn: sa.Connection) -> None:
    _accounts(conn)
    import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    quantity, value = conn.execute(
        sa.text("select quantity, market_value from holding_snapshot where symbol = 'CASHX**'")
    ).one()
    assert quantity is None
    assert value == Decimal("5025.00")


def test_raw_rows_are_stored_as_they_arrived(conn: sa.Connection) -> None:
    _accounts(conn)
    import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)

    raw = conn.execute(sa.text("select raw from import_row where row_number = 2")).scalar_one()
    assert raw["Current value"] == "$20,100.00"
    assert raw["Cost basis total"] == "$18,000.00"


def test_the_raw_rows_alone_are_enough_to_rebuild_an_import(conn: sa.Connection) -> None:
    """The recovery path, exercised rather than asserted in a docstring. A
    normalizer bug wrote a wrong figure months ago and the export is long gone:
    re-normalizing from `import_row` puts it right without the file."""
    _accounts(conn)
    result = import_export(conn, content=export(), filename=FILENAME, as_of=AS_OF)
    conn.execute(sa.text("update holding_snapshot set market_value = 1.00"))
    conn.execute(sa.text("update balance_snapshot set balance = 1.00"))

    rebuilt = renormalize(conn, result.batch_id)

    assert {a.label: str(a.balance) for a in rebuilt.accounts} == BALANCES
    fund = conn.execute(
        sa.text("select market_value from holding_snapshot where symbol = 'FAKEX'")
    ).scalar_one()
    assert fund == Decimal("20100.00")
    assert _counts(conn)["holding_snapshot"] == 4


def test_renormalizing_an_unknown_batch_is_an_error(conn: sa.Connection) -> None:
    with pytest.raises(LookupError):
        renormalize(conn, uuid.uuid4())


def test_real_data_is_not_imported_beside_the_fixture(seeded: sa.Connection) -> None:
    """A net worth summed across invented accounts and real ones is neither."""
    _accounts(seeded)

    with pytest.raises(FixtureLoaded, match="make unseed"):
        import_export(seeded, content=export(), filename=FILENAME, as_of=AS_OF)
