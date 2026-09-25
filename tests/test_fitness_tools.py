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
    MixedUnitsError,
    UnknownExerciseError,
    get_body_metric_trend,
    get_lift_progression,
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
