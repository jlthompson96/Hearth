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

from tools.finance import (
    UnknownAccountError,
    get_allocation,
    get_balance_history,
    get_net_worth_trend,
)

JAN = dt.date(2024, 1, 1)
DEC = dt.date(2024, 12, 31)
GAP = dt.date(2024, 7, 31)


# --- balance history ----------------------------------------------------------


def test_balance_history_returns_every_month(seeded: sa.Connection) -> None:
    history = get_balance_history(seeded, "Everyday Checking", JAN, DEC)

    assert len(history.points) == 12
    assert history.points[0].balance == Decimal("4200.00")
    assert history.points[-1].balance == Decimal("4750.00")
    assert history.change == Decimal("550.00")
    assert history.currency == "USD"


def test_balance_history_skips_the_month_an_export_missed(seeded: sa.Connection) -> None:
    """Retirement is missing July. The history reports eleven points rather
    than inventing a twelfth."""
    history = get_balance_history(seeded, "Retirement", JAN, DEC)

    assert len(history.points) == 11
    assert GAP not in [point.as_of for point in history.points]


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
    history = get_balance_history(seeded, "Everyday Checking", JAN, dt.date(2024, 2, 1))

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
    assert trend.coverage.complete_from == dt.date(2024, 8, 31)


def test_the_caveat_names_the_gap(seeded: sa.Connection) -> None:
    """The agent has to state this before describing the trend, so the tool
    hands it a finished sentence rather than a list to summarise."""
    caveat = get_net_worth_trend(seeded, JAN, DEC).coverage.caveat()

    assert caveat is not None
    assert "2024-07-31" in caveat
    assert "2024-08-31" in caveat


def test_a_fully_covered_period_has_no_caveat(seeded: sa.Connection) -> None:
    trend = get_net_worth_trend(seeded, dt.date(2024, 8, 1), DEC)

    assert trend.coverage.is_complete is True
    assert trend.coverage.incomplete_dates == ()
    assert trend.coverage.caveat() is None


def test_a_period_with_no_complete_date_says_so(seeded: sa.Connection) -> None:
    """July alone has no complete date, so there is no date to be complete
    from and the caveat must not imply otherwise."""
    trend = get_net_worth_trend(seeded, dt.date(2024, 7, 1), dt.date(2024, 7, 31))

    assert trend.coverage.complete_from is None
    caveat = trend.coverage.caveat()
    assert caveat is not None
    assert "No date" in caveat


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


def test_allocation_reports_the_date_it_actually_used(seeded: sa.Connection) -> None:
    """Asking for mid-June answers with May's snapshot, and says so. Silently
    answering with a different date is how a number stops being trustworthy
    without ever being wrong."""
    allocation = get_allocation(seeded, dt.date(2024, 6, 15))

    assert allocation.as_of_requested == dt.date(2024, 6, 15)
    assert allocation.as_of_used == dt.date(2024, 5, 31)


def test_allocation_before_any_holdings_exist(seeded: sa.Connection) -> None:
    allocation = get_allocation(seeded, dt.date(2024, 1, 15))

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
