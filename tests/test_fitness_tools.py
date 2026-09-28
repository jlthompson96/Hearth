"""The lift progression tool.

Weights are asserted exactly as recorded. Estimated one-rep max is asserted
against the Epley figure the tool computes, which is the point of computing it
in Python: the same input always yields the same number, which is what makes an
eval case meaningful.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from db.models import BodyMetric, Workout, WorkoutSet
from scripts.seed import YEAR
from tools.fitness import (
    MAX_GROUPS,
    MAX_POINTS,
    MetricPoint,
    MixedUnitsError,
    UnknownExerciseError,
    get_body_metric_trend,
    get_lift_progression,
    group_points,
)

JAN = dt.date(YEAR, 1, 1)
DEC = dt.date(YEAR, 12, 31)


def test_progression_returns_one_session_per_workout(seeded: sa.Connection) -> None:
    progression = get_lift_progression(seeded, "back squat", JAN, DEC)

    assert len(progression.sessions) == 12
    assert progression.unit == "kg"


def test_progression_reports_weights_as_recorded(seeded: sa.Connection) -> None:
    """100.000, not 100.0. The fixture records three decimal places and the
    tool does not quietly round them away."""
    progression = get_lift_progression(seeded, "back squat", JAN, DEC)

    assert progression.sessions[0].best_weight == Decimal("100.000")
    assert progression.sessions[-1].best_weight == Decimal("127.500")
    assert progression.change == Decimal("27.500")


def test_progression_takes_the_heaviest_set_of_each_session(
    seeded: sa.Connection,
) -> None:
    progression = get_lift_progression(seeded, "bench press", JAN, DEC)

    assert progression.sessions[0].best_weight == Decimal("70.000")
    assert progression.sessions[-1].best_weight == Decimal("83.750")
    assert progression.sessions[0].reps_at_best == 5


def test_estimated_one_rep_max_is_computed_not_guessed(seeded: sa.Connection) -> None:
    progression = get_lift_progression(seeded, "back squat", JAN, DEC)

    assert progression.sessions[0].estimated_1rm == Decimal("116.7")
    assert progression.sessions[-1].estimated_1rm == Decimal("148.8")


def test_bodyweight_sets_are_skipped_not_read_as_zero(seeded: sa.Connection) -> None:
    """Pull-ups are logged with no load. Treating a null weight as 0 kg would
    produce a progression that is not merely wrong but insulting."""
    progression = get_lift_progression(seeded, "pull-up", JAN, DEC)

    assert progression.sessions == ()
    assert progression.change is None
    assert progression.unit is None


def test_unknown_exercise_raises(seeded: sa.Connection) -> None:
    """Usually a typo in the exercise name, and "no sessions found" hides that
    while looking like an answer."""
    with pytest.raises(UnknownExerciseError):
        get_lift_progression(seeded, "clean and jerk", JAN, DEC)


def test_a_narrow_window_reports_no_change(seeded: sa.Connection) -> None:
    progression = get_lift_progression(seeded, "back squat", JAN, dt.date(YEAR, 2, 1))

    assert len(progression.sessions) == 1
    assert progression.change is None


def _squat_in_pounds(conn: sa.Connection) -> None:
    workout = conn.execute(
        sa.insert(Workout)
        .values(performed_on=dt.date(YEAR, 9, 15), kind="strength")
        .returning(Workout.id)
    ).scalar_one()
    conn.execute(
        sa.insert(WorkoutSet).values(
            workout_id=workout,
            exercise="back squat",
            set_number=1,
            reps=5,
            weight=Decimal("275"),
            weight_unit="lb",
        )
    )


def test_a_lift_logged_in_two_units_is_not_subtracted(seeded: sa.Connection) -> None:
    """127.500 kg to 275 lb is not a change of +147.5. Manual entry and imports
    both refuse a second unit for a lift; this is the tool's own guard."""
    _squat_in_pounds(seeded)

    with pytest.raises(MixedUnitsError) as mixed:
        get_lift_progression(seeded, "back squat", JAN, DEC)

    assert mixed.value.units == ("kg", "lb")


def test_a_window_in_one_unit_is_still_answered(seeded: sa.Connection) -> None:
    """The mix is judged over the period asked about, not the whole log."""
    _squat_in_pounds(seeded)

    progression = get_lift_progression(seeded, "back squat", JAN, dt.date(YEAR, 8, 31))
    assert progression.unit == "kg"


def test_a_body_metric_logged_in_two_units_is_not_subtracted(seeded: sa.Connection) -> None:
    seeded.execute(
        sa.insert(BodyMetric).values(
            as_of=dt.date(YEAR, 9, 15), metric="body_mass", value=Decimal("181.4"), unit="lb"
        )
    )

    with pytest.raises(MixedUnitsError):
        get_body_metric_trend(seeded, "body_mass", JAN, DEC)


# --- a long history is grouped, not listed ----------------------------------------
#
# The real body-weight history is 809 daily weigh-ins. Listed one to a line that
# is about 19,500 characters — more than the model's whole 8,192-token window —
# so past MAX_POINTS the tool groups them and computes each group here, in
# Python, where rule 1 puts arithmetic.

#: A Monday, so the weekly groups below are whole weeks.
MONDAY = dt.date(2024, 1, 1)


def _daily(days: int, *, every: int = 1) -> tuple[MetricPoint, ...]:
    """A reading every `every` days from MONDAY, cycling 80.000 to 80.600 over
    each week: every whole week averages exactly 80.3."""
    return tuple(
        MetricPoint(
            as_of=MONDAY + dt.timedelta(days=day),
            value=Decimal("80.000") + Decimal("0.100") * (day % 7),
        )
        for day in range(0, days, every)
    )


def test_a_short_history_is_not_grouped(seeded: sa.Connection) -> None:
    """The fixture's twelve month-end weigh-ins are listed as they are, so
    every existing answer and eval case reads exactly what it read before."""
    trend = get_body_metric_trend(seeded, "body_mass", JAN, DEC)

    assert len(trend.points) == 12 <= MAX_POINTS
    assert trend.grouped_by is None
    assert trend.groups == ()


def test_ten_weeks_of_daily_readings_are_grouped_by_week() -> None:
    grain, groups = group_points(_daily(70))

    assert grain == "week"
    assert len(groups) == 10
    first = groups[0]
    assert first.label == "week of 2024-01-01"
    assert (first.first, first.last) == (MONDAY, dt.date(2024, 1, 7))
    assert first.count == 7
    assert first.average == Decimal("80.3")
    # Low and high are recordings, so they are reported as recorded.
    assert (first.low, first.high) == (Decimal("80.000"), Decimal("80.600"))


def test_a_year_of_daily_readings_is_grouped_by_month() -> None:
    """53 weeks is more than MAX_GROUPS lines, so the next grain up."""
    grain, groups = group_points(_daily(366))

    assert grain == "month"
    assert [g.label for g in groups] == [f"2024-{m:02d}" for m in range(1, 13)]
    assert sum(g.count for g in groups) == 366


def test_three_years_of_weekly_readings_are_grouped_by_quarter() -> None:
    grain, groups = group_points(_daily(3 * 364, every=7))

    assert grain == "quarter"
    assert groups[0].label == "2024-Q1"
    assert groups[-1].label == "2026-Q4"
    assert len(groups) == 12 <= MAX_GROUPS


def test_an_average_is_rounded_to_tenths_in_python() -> None:
    """80.000, 80.100 and 80.100 average 80.0666…; the model is handed 80.1
    rather than a long decimal it might round differently each time."""
    points = tuple(
        MetricPoint(MONDAY + dt.timedelta(days=d), Decimal(v))
        for d, v in enumerate(["80.000", "80.100", "80.100"])
    )

    _, (only,) = group_points(points)

    assert only.average == Decimal("80.1")


def test_a_long_history_from_the_database_is_grouped(seeded: sa.Connection) -> None:
    """The real shape: a daily weight history in pounds, the year before the
    fixture's, so the fixture's kilograms are outside the period asked."""
    start = dt.date(YEAR - 3, 1, 1)
    for day in range(809):
        seeded.execute(
            sa.insert(BodyMetric).values(
                as_of=start + dt.timedelta(days=day),
                metric="body_mass",
                value=Decimal("182.40"),
                unit="lb",
            )
        )
    end = start + dt.timedelta(days=808)

    trend = get_body_metric_trend(seeded, "body_mass", start, end)

    assert len(trend.points) == 809
    assert trend.grouped_by is not None
    assert 0 < len(trend.groups) <= MAX_GROUPS
    assert sum(g.count for g in trend.groups) == 809
