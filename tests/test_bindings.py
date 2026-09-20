"""The `@tool` layer over the Phase 3 query functions.

No model is involved here. What is being checked is that a tool call renders
the figures Phase 3 computed, without altering them, and that the two things an
agent is required to say out loud — the coverage caveat and a substituted
allocation date — actually appear in the text the model will receive. A caveat
that Phase 3 writes correctly and the binding layer drops is the same bug as
one that was never written.

The bindings open their own read-only connection, which in production points at
the development database. Here that is redirected at the throwaway test
database, so these tests read the same golden fixture as everything else.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from tools import bindings
from tools.bindings import TALLY_TOOLS, allocation, balance_history, net_worth_trend, schema_cost

#: The golden fixture is a 2024 dataset: month ends through the year, Brokerage
#: opening in March, and Retirement missing its July export.
YEAR = 2024
FULL_YEAR = (f"{YEAR}-01-01", f"{YEAR}-12-31")


@pytest.fixture
def _bound(seeded: sa.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the tools' own connection at the test database."""

    @contextmanager
    def _conn() -> Iterator[sa.Connection]:
        yield seeded

    monkeypatch.setattr(bindings, "readonly_connection", _conn)


def test_net_worth_trend_states_the_coverage_caveat(_bound: None) -> None:
    """Retirement is missing its July export, so a full-year question must come
    back carrying the qualification, marked for the agent to repeat."""
    result = net_worth_trend.invoke({"start": FULL_YEAR[0], "end": FULL_YEAR[1]})

    assert "caveat:" in result
    assert f"{YEAR}-07-31" in result
    assert "complete only from" in result


def test_net_worth_trend_reports_complete_coverage_when_it_is_complete(_bound: None) -> None:
    """August onward has every account reporting. Saying nothing about coverage
    is not the same as saying it is complete, and the agent needs the latter."""
    result = net_worth_trend.invoke({"start": f"{YEAR}-08-31", "end": f"{YEAR}-12-31"})

    assert "coverage: complete" in result
    assert "CAVEAT" not in result


def test_net_worth_figures_match_the_query_result_exactly(
    _bound: None, seeded: sa.Connection
) -> None:
    """The binding formats; it must not re-derive. Every total it prints has to
    be the one Phase 3 computed, to the cent (CLAUDE.md, rule 1)."""
    from tools.finance import get_net_worth_trend

    computed = get_net_worth_trend(seeded, dt.date(YEAR, 1, 1), dt.date(YEAR, 12, 31))
    rendered = net_worth_trend.invoke({"start": FULL_YEAR[0], "end": FULL_YEAR[1]})

    for point in computed.points:
        assert f"{point.balance:,.2f}" in rendered


def test_balance_history_for_a_named_account(_bound: None) -> None:
    result = balance_history.invoke(
        {"account_label": "Emergency Savings", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "Emergency Savings" in result
    assert "change over the period:" in result


def test_unknown_account_lists_the_real_ones(_bound: None) -> None:
    """An unknown label is usually a typo, so the answer that helps is the list
    of labels that do exist — not an exception that ends the turn, and not an
    empty history that reads as "no movement"."""
    result = balance_history.invoke(
        {"account_label": "Savings Acount", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "no account labelled" in result.lower()
    assert "Emergency Savings" in result


def test_allocation_declares_a_substituted_date(_bound: None) -> None:
    """Asked about a date with no holdings, it answers with the most recent
    earlier one and says so. Answering silently is how a figure stops being
    trustworthy without ever being wrong."""
    result = allocation.invoke({"as_of": f"{YEAR}-12-15"})

    assert f"{YEAR}-11-30" in result
    assert "caveat:" in result
    assert "You asked about" in result
    assert f"{YEAR}-12-15" in result


def test_allocation_before_any_holdings_exist(_bound: None) -> None:
    """Brokerage opens in March. January is not missing data — there was no
    account yet — and the tool has to say so rather than return an empty table."""
    result = allocation.invoke({"as_of": f"{YEAR}-01-15"})

    assert "No holdings" in result


def test_allocation_percentages_come_from_the_query(_bound: None) -> None:
    result = allocation.invoke({"as_of": f"{YEAR}-06-30"})

    assert "VTI" in result and "BND" in result
    assert "%" in result
    assert "total:" in result


def test_tally_is_not_given_forge_s_tools() -> None:
    """Tools are scoped per agent (CLAUDE.md, rule 11). Lift progression costs
    context on a finance turn and buys nothing, so it is not in this list."""
    names = {t.name for t in TALLY_TOOLS}

    assert names == {"net_worth_trend", "balance_history", "allocation"}
    assert not any("lift" in name for name in names)


def test_schema_cost_is_reported_and_bounded() -> None:
    """Rule 11 asks for the total to be logged rather than assumed. It is also
    a budget: three tools should not be eating a meaningful slice of 8,192
    tokens before the user has typed anything."""
    cost = schema_cost(TALLY_TOOLS)

    print(f"\n  Tally tool schemas: ~{cost} tokens of the 8,192 window")
    assert 0 < cost < 600


def test_an_empty_period_is_no_data_not_no_change(_bound: None) -> None:
    """The failure this guards against was observed, not imagined: asked about a
    year the fixture does not cover, the model reported that net worth "has not
    changed". An absent figure and a figure of zero are different claims, and
    only the tool can tell the model which one it is holding."""
    result = net_worth_trend.invoke({"start": "2099-01-01", "end": "2099-12-31"})

    assert "NO DATA" in result
    assert "not a flat balance" in result
    # It must also say where the data does live, or "no data" invites the model
    # to fill the silence.
    assert f"{YEAR}-01-31" in result
