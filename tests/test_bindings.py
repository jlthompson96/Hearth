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
from decimal import Decimal

import pytest
import sqlalchemy as sa

from scripts.seed import YEAR
from tools import bindings
from tools.bindings import TALLY_TOOLS, allocation, balance_history, net_worth_trend, schema_cost

#: Taken from the fixture rather than restated here: the seed defaults to the
#: current year, so a hardcoded one would pass today and fail in January.
#: Brokerage opens in March and Retirement is missing its July export.
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
        assert f"${point.balance:,.2f}" in rendered


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("38250.00", "$38,250.00"),
        ("-1800.00", "-$1,800.00"),
        ("1234567.50", "$1,234,567.50"),
        ("0.00", "$0.00"),
        # Decimal keeps the sign of zero. A balance of nothing is not negative.
        ("-0.00", "$0.00"),
    ],
)
def test_money_is_written_as_us_currency(value: str, expected: str) -> None:
    """The model copies figures verbatim, so this string is the format the
    person reads. The sign goes before the dollar sign, never after it."""
    assert bindings._money(Decimal(value)) == expected


def test_a_change_is_always_signed() -> None:
    assert bindings._signed(Decimal("38250.00")) == "+$38,250.00"
    assert bindings._signed(Decimal("-50.00")) == "-$50.00"


def test_a_liability_reaches_the_model_as_a_negative_amount(_bound: None) -> None:
    """The credit card is stored negative. It has to arrive as `-$1,800.00` —
    the form the UI colours red — and not as a positive figure with the minus
    stranded somewhere the model can drop it."""
    result = balance_history.invoke(
        {"account_label": "Credit Card", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "-$1,800.00" in result


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


# --- Forge's tools ------------------------------------------------------------


def test_lift_progression_reports_sessions_and_marks_the_estimate(_bound: None) -> None:
    """The fixture's squat climbs 2.5kg a month from 100kg. An estimated 1RM is
    Epley applied to a working set, not something they have lifted, and saying
    so is how nobody ends up under a bar they have never held."""
    from tools.bindings import lift_progression

    result = lift_progression.invoke(
        {"exercise": "back squat", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "back squat" in result
    assert "100.000" in result
    assert "est. 1RM" in result
    assert "estimate, not a tested max" in result


def test_an_unlogged_lift_lists_the_logged_ones(_bound: None) -> None:
    from tools.bindings import lift_progression

    result = lift_progression.invoke(
        {"exercise": "hack squat", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "has ever been logged" in result
    assert "back squat" in result


def test_body_metric_trend_reports_values_exactly_as_recorded(_bound: None) -> None:
    """82.500 is not 82.5. Re-rounding a logged measurement loses precision in
    the one place the person tracking it would notice."""
    from tools.bindings import body_metric_trend

    result = body_metric_trend.invoke(
        {"metric": "body_mass", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "82.500" in result
    assert "kg" in result
    assert "change over the period:" in result


def test_an_unrecorded_metric_lists_what_is_recorded(_bound: None) -> None:
    from tools.bindings import body_metric_trend

    result = body_metric_trend.invoke(
        {"metric": "body fat", "start": FULL_YEAR[0], "end": FULL_YEAR[1]}
    )

    assert "has been recorded" in result
    assert "body_mass" in result


def test_an_empty_lift_period_is_no_data_not_a_plateau(_bound: None) -> None:
    """Same failure mode Tally had: an absence of sessions is not a stalled
    lift, and only the tool can tell the model which one it is holding."""
    from tools.bindings import lift_progression

    result = lift_progression.invoke(
        {"exercise": "back squat", "start": "2099-01-01", "end": "2099-12-31"}
    )

    assert "NO DATA" in result
    assert "not a stalled lift" in result


def test_forge_and_tally_do_not_share_tools() -> None:
    """Rule 11: tools are scoped per agent. A fitness turn does not pay for net
    worth schemas it will never call, and vice versa."""
    from tools.bindings import FORGE_TOOLS

    forge_names = {t.name for t in FORGE_TOOLS}
    tally_names = {t.name for t in TALLY_TOOLS}

    assert forge_names == {"lift_progression", "body_metric_trend"}
    assert not (forge_names & tally_names)
    print(f"\n  Forge tool schemas: ~{schema_cost(FORGE_TOOLS)} tokens")
