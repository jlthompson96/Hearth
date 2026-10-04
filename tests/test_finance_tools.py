"""The finance tools, including the partial-coverage cases.

Exact figures throughout. The evals will assert that a number appears verbatim
in an answer, and a tool that returns 47650.0 where the fixture implies
47650.00 fails that for reasons no one will enjoy tracking down.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from db.models import Account, BalanceSnapshot
from scripts.seed import YEAR
from tools.finance import (
    CARRY_DAYS,
    Carried,
    UnknownAccountError,
    get_allocation,
    get_balance_history,
    get_net_worth_trend,
)

JAN = dt.date(YEAR, 1, 1)
DEC = dt.date(YEAR, 12, 31)
#: Retirement is missing June and July. June is 30 days after its May balance,
#: inside the limit, so May's is carried forward; July is 61 days after it,
#: past the limit, so July stays a gap.
MAY = dt.date(YEAR, 5, 31)
CARRIED = dt.date(YEAR, 6, 30)
GAP = dt.date(YEAR, 7, 31)


# --- balance history ----------------------------------------------------------


def test_balance_history_returns_every_month(seeded: sa.Connection) -> None:
    history = get_balance_history(seeded, "Everyday Checking", JAN, DEC)

    assert len(history.points) == 12
    assert history.points[0].balance == Decimal("4200.00")
    assert history.points[-1].balance == Decimal("4750.00")
    assert history.change == Decimal("550.00")
    assert history.currency == "USD"


def test_balance_history_skips_the_months_an_export_missed(seeded: sa.Connection) -> None:
    """Retirement is missing June and July. One account's history reports the
    ten balances recorded rather than inventing the other two: carrying a
    balance forward is for a total across accounts, not a history of one."""
    history = get_balance_history(seeded, "Retirement", JAN, DEC)

    assert len(history.points) == 10
    assert {CARRIED, GAP}.isdisjoint(point.as_of for point in history.points)


def test_balance_history_of_a_liability_stays_negative(seeded: sa.Connection) -> None:
    history = get_balance_history(seeded, "Credit Card", JAN, DEC)

    assert history.points[0].balance == Decimal("-1800.00")
    assert history.points[-1].balance == Decimal("-1250.00")
    # Paying a card down is a rise in net worth, and the sign has to carry that.
    assert history.change == Decimal("550.00")


def test_unknown_account_raises_rather_than_returning_nothing(seeded: sa.Connection) -> None:
    """An empty history reads as "no movement", which is a different and much
    more misleading answer than "there is no such account"."""
    with pytest.raises(UnknownAccountError):
        get_balance_history(seeded, "Offshore Vault", JAN, DEC)


def test_single_point_reports_no_change(seeded: sa.Connection) -> None:
    history = get_balance_history(seeded, "Everyday Checking", JAN, dt.date(YEAR, 2, 1))

    assert len(history.points) == 1
    assert history.change is None


# --- net worth trend ----------------------------------------------------------


def test_net_worth_trend_sums_assets_and_liabilities(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, JAN, DEC)

    assert len(trend.points) == 12
    # 4200 + 15000 + 82000 - 1800, before the brokerage account exists.
    assert trend.points[0].balance == Decimal("99400.00")
    assert trend.points[-1].balance == Decimal("147050.00")
    assert trend.change == Decimal("47650.00")


def test_net_worth_trend_reports_the_incomplete_date(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, JAN, DEC)

    assert trend.coverage.incomplete_dates == (GAP,)
    assert trend.coverage.is_complete is False
    assert trend.coverage.complete_from == dt.date(YEAR, 8, 31)


def test_the_caveat_names_the_gap(seeded: sa.Connection) -> None:
    """The agent has to state this before describing the trend, so the tool
    hands it a finished sentence rather than a list to summarise."""
    caveat = get_net_worth_trend(seeded, JAN, DEC).coverage.caveat()

    assert caveat is not None
    assert f"{YEAR}-07-31" in caveat
    assert f"{YEAR}-08-31" in caveat


def test_a_fully_covered_period_has_no_caveat(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, dt.date(YEAR, 8, 1), DEC)

    assert trend.coverage.is_complete is True
    assert trend.coverage.incomplete_dates == ()
    assert trend.coverage.caveat() is None


def test_a_period_with_no_complete_date_says_so(seeded: sa.Connection) -> None:
    """July alone has no complete date, so there is no date to be complete
    from and the caveat must not imply otherwise."""
    trend = get_net_worth_trend(seeded, dt.date(YEAR, 7, 1), dt.date(YEAR, 7, 31))

    assert trend.coverage.complete_from is None
    caveat = trend.coverage.caveat()
    assert caveat is not None
    assert f"{YEAR}-07-31" in caveat
    assert "partial" in caveat
    assert "complete only from" not in caveat


def test_a_period_ending_on_a_gap_does_not_claim_every_date_is_partial(
    seeded: sa.Connection,
) -> None:
    """May is complete and July is not. A period ending on the gap has no date
    to be complete *from*, but the old sentence — "No date in this period has
    data for every account" — said May was partial too."""
    trend = get_net_worth_trend(seeded, dt.date(YEAR, 5, 1), dt.date(YEAR, 7, 31))

    assert trend.coverage.complete_from is None
    caveat = trend.coverage.caveat()
    assert caveat is not None
    assert f"{YEAR}-05-31" not in caveat
    assert "No date" not in caveat


# --- carrying a balance forward ------------------------------------------------
#
# Accounts are updated on different days: an export on the 21st, a balance
# typed in on the 20th. Summed per exact date, both dates would be partial
# totals. So an account with no balance on a date contributes its latest
# balance from up to CARRY_DAYS earlier, and every such balance is named.


def test_a_missed_month_is_carried_forward_and_named(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, JAN, DEC)
    june = {p.as_of: p.balance for p in trend.points}[CARRIED]

    # 4450 checking + 16250 savings + 85600 retirement (May's) - 1550 credit
    # card + 27300 brokerage.
    assert june == Decimal("132050.00")
    assert trend.coverage.carried == (Carried(CARRIED, "Retirement", MAY),)
    assert CARRIED not in trend.coverage.incomplete_dates


def test_a_gap_longer_than_the_limit_is_still_a_gap(seeded: sa.Connection) -> None:
    """July is 61 days after Retirement's last balance: it is left out of the
    total, and the date is incomplete, exactly as before carrying existed."""
    trend = get_net_worth_trend(seeded, JAN, DEC)
    july = {p.as_of: p.balance for p in trend.points}[GAP]

    # 4500 checking + 16500 savings - 1500 credit card + 28400 brokerage.
    assert july == Decimal("47900.00")
    assert trend.coverage.incomplete_dates == (GAP,)


def test_the_limit_is_counted_to_the_day(seeded: sa.Connection) -> None:
    """A balance CARRY_DAYS old is carried; one a day older is not."""
    loan = seeded.execute(
        sa.insert(Account)
        .values(label="Loan", kind="loan", opened_on=dt.date(YEAR, 8, 1))
        .returning(Account.id)
    ).scalar_one()
    checking = seeded.execute(
        sa.select(Account.id).where(Account.label == "Everyday Checking")
    ).scalar_one()
    recorded = dt.date(YEAR, 8, 31)
    within = recorded + dt.timedelta(days=CARRY_DAYS)
    beyond = within + dt.timedelta(days=1)
    for account_id, as_of in ((loan, recorded), (checking, within), (checking, beyond)):
        seeded.execute(
            sa.insert(BalanceSnapshot).values(
                account_id=account_id, as_of=as_of, balance=Decimal("-500.00")
            )
        )

    coverage = get_net_worth_trend(seeded, recorded, beyond).coverage

    assert Carried(within, "Loan", recorded) in coverage.carried
    assert not any(c.as_of == beyond and c.label == "Loan" for c in coverage.carried)
    assert beyond in coverage.incomplete_dates


def test_the_carried_caveat_names_each_balance_and_both_dates(seeded: sa.Connection) -> None:
    """A finished sentence, like the gap's, for the agent to repeat."""
    caveat = get_net_worth_trend(seeded, JAN, DEC).coverage.carried_caveat()

    assert caveat is not None
    assert "Retirement" in caveat
    assert f"{YEAR}-06-30" in caveat and f"{YEAR}-05-31" in caveat
    assert f"{CARRY_DAYS} days" in caveat


def test_nothing_carried_says_nothing(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, dt.date(YEAR, 8, 1), DEC)

    assert trend.coverage.carried == ()
    assert trend.coverage.carried_caveat() is None


def test_an_empty_period_yields_no_points(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, dt.date(2020, 1, 1), dt.date(2020, 12, 31))

    assert trend.points == ()
    assert trend.change is None


# --- allocation ---------------------------------------------------------------


def test_allocation_percentages_are_computed_and_total_one_hundred(
    seeded: sa.Connection,
) -> None:
    allocation = get_allocation(seeded, DEC)

    assert allocation.as_of_used == DEC
    assert allocation.total == Decimal("49200.00")
    assert {s.symbol: s.market_value for s in allocation.slices} == {
        "VTI": Decimal("27600.00"),
        "BND": Decimal("21600.00"),
    }
    assert sum(s.percentage for s in allocation.slices) == Decimal("100.0")


def test_allocation_breaks_positions_down_by_account(seeded: sa.Connection) -> None:
    """ "What are my positions" deserves the positions: which account holds
    what, how many, at what price. Each account's total is summed here, in
    Python, so the model never has to add them (rule 1)."""
    allocation = get_allocation(seeded, DEC)

    [account] = allocation.accounts
    assert (account.label, account.total) == ("Brokerage", Decimal("49200.00"))
    assert [(p.symbol, p.quantity, p.price, p.market_value) for p in account.positions] == [
        ("VTI", Decimal("120.00000000"), Decimal("230.000000"), Decimal("27600.00")),
        ("BND", Decimal("300.00000000"), Decimal("72.000000"), Decimal("21600.00")),
    ]


def test_allocation_reports_the_date_it_actually_used(seeded: sa.Connection) -> None:
    """Asking for mid-June answers with May's snapshot, and says so. Silently
    answering with a different date is how a number stops being trustworthy
    without ever being wrong."""
    allocation = get_allocation(seeded, dt.date(YEAR, 6, 15))

    assert allocation.as_of_requested == dt.date(YEAR, 6, 15)
    assert allocation.as_of_used == dt.date(YEAR, 5, 31)


def test_allocation_before_any_holdings_exist(seeded: sa.Connection) -> None:
    allocation = get_allocation(seeded, dt.date(YEAR, 1, 15))

    assert allocation.as_of_used is None
    assert allocation.slices == ()
    assert allocation.total == Decimal("0.00")


# --- the connection the tools run on ------------------------------------------


def test_tools_run_on_the_read_only_connection(ro_engine: Engine) -> None:
    """Rule 2, end to end: the tools work through the role that cannot write.
    A missing grant on the coverage view would not surface until an agent tried
    to describe a trend."""
    with ro_engine.connect() as connection:
        trend = get_net_worth_trend(connection, JAN, DEC)
        allocation = get_allocation(connection, DEC)

    assert trend.points == ()
    assert allocation.as_of_used is None
