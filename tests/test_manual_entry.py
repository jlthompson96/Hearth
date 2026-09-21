"""Manual entry: accounts, and balances typed in by hand.

The same guarantees as an import, reached by a different door. A label is
checked for anything resembling an account number before it is stored, a figure
already recorded is never overwritten, and nothing goes in beside the fixture.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from ingest.errors import AccountNumberRefused, Conflict, FixtureLoaded, Refused, UnknownAccounts
from ingest.importer import import_export
from ingest.manual import (
    accounts,
    create_account,
    recent_entries,
    record_balance,
    remove_balance,
)
from tests.fake_exports import FILENAME, export

DAY = dt.date(2026, 9, 21)


def test_a_new_account_is_listed_with_no_balance_yet(conn: sa.Connection) -> None:
    create_account(conn, label="Everyday Checking", kind="checking", opened_on=dt.date(2020, 1, 1))

    [account] = accounts(conn)
    assert (account.label, account.kind, account.currency) == (
        "Everyday Checking",
        "checking",
        "USD",
    )
    assert account.latest_balance is None


def test_a_label_carrying_an_account_number_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(AccountNumberRefused):
        create_account(conn, label="Checking 1234", kind="checking")

    assert accounts(conn) == []


def test_a_label_is_required_and_trimmed(conn: sa.Connection) -> None:
    with pytest.raises(Refused):
        create_account(conn, label="   ", kind="checking")

    create_account(conn, label="  Savings  ", kind="savings")
    assert [a.label for a in accounts(conn)] == ["Savings"]


def test_an_unknown_kind_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(Refused, match="kind"):
        create_account(conn, label="Savings", kind="piggy bank")


def test_a_label_can_only_be_used_once(conn: sa.Connection) -> None:
    create_account(conn, label="Savings", kind="savings")

    with pytest.raises(Conflict):
        create_account(conn, label="Savings", kind="checking")


def test_a_balance_becomes_the_account_s_latest(conn: sa.Connection) -> None:
    create_account(conn, label="Credit Card", kind="credit")
    record_balance(
        conn, label="Credit Card", as_of=dt.date(2026, 8, 31), balance=Decimal("-1200.00")
    )
    record_balance(conn, label="Credit Card", as_of=DAY, balance=Decimal("-950.50"))

    [account] = accounts(conn)
    assert (account.latest_as_of, account.latest_balance) == (DAY, Decimal("-950.50"))


def test_a_day_already_recorded_is_a_conflict(conn: sa.Connection) -> None:
    create_account(conn, label="Savings", kind="savings")
    record_balance(conn, label="Savings", as_of=DAY, balance=Decimal("100.00"))

    with pytest.raises(Conflict, match="entered by hand"):
        record_balance(conn, label="Savings", as_of=DAY, balance=Decimal("200.00"))


def test_a_day_already_imported_names_the_import(conn: sa.Connection) -> None:
    create_account(conn, label="Joint Brokerage", kind="brokerage")
    create_account(conn, label="Roth IRA", kind="retirement")
    import_export(conn, content=export(), filename=FILENAME, as_of=DAY)

    with pytest.raises(Conflict, match="Portfolio_Positions"):
        record_balance(conn, label="Roth IRA", as_of=DAY, balance=Decimal("1.00"))


def test_a_balance_before_the_account_opened_is_refused(conn: sa.Connection) -> None:
    """Coverage treats the time before an account opened as not existing
    rather than missing. A balance from then contradicts it."""
    create_account(conn, label="Brokerage", kind="brokerage", opened_on=dt.date(2026, 3, 1))

    with pytest.raises(Refused, match="opened"):
        record_balance(conn, label="Brokerage", as_of=dt.date(2026, 2, 28), balance=Decimal("1.00"))


def test_a_balance_for_an_unknown_account_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(UnknownAccounts):
        record_balance(conn, label="Nowhere", as_of=DAY, balance=Decimal("1.00"))


def test_recent_entries_are_the_hand_entered_ones(conn: sa.Connection) -> None:
    create_account(conn, label="Joint Brokerage", kind="brokerage")
    create_account(conn, label="Roth IRA", kind="retirement")
    create_account(conn, label="Savings", kind="savings")
    import_export(conn, content=export(), filename=FILENAME, as_of=DAY)
    record_balance(conn, label="Savings", as_of=DAY, balance=Decimal("100.00"))

    assert [(e.label, e.balance) for e in recent_entries(conn)] == [("Savings", Decimal("100.00"))]


def test_a_hand_entered_balance_can_be_removed(conn: sa.Connection) -> None:
    create_account(conn, label="Savings", kind="savings")
    entry = record_balance(conn, label="Savings", as_of=DAY, balance=Decimal("100.00"))

    remove_balance(conn, entry.id)

    assert recent_entries(conn) == []


def test_an_imported_balance_is_removed_with_its_import_not_on_its_own(
    conn: sa.Connection,
) -> None:
    """Removing one account's figure from an import would leave the rest of the
    batch describing a day that no longer adds up."""
    create_account(conn, label="Joint Brokerage", kind="brokerage")
    create_account(conn, label="Roth IRA", kind="retirement")
    import_export(conn, content=export(), filename=FILENAME, as_of=DAY)
    imported = conn.execute(sa.text("select min(id) from balance_snapshot")).scalar_one()

    with pytest.raises(Refused, match="import"):
        remove_balance(conn, imported)


def test_nothing_is_entered_beside_the_fixture(seeded: sa.Connection) -> None:
    with pytest.raises(FixtureLoaded):
        create_account(seeded, label="Real Savings", kind="savings")
    with pytest.raises(FixtureLoaded):
        record_balance(seeded, label="Retirement", as_of=DAY, balance=Decimal("1.00"))
