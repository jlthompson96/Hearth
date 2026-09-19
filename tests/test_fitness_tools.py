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

from tools.fitness import UnknownExerciseError, get_lift_progression

JAN = dt.date(2024, 1, 1)
DEC = dt.date(2024, 12, 31)


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
    progression = get_lift_progression(seeded, "back squat", JAN, dt.date(2024, 2, 1))

    assert len(progression.sessions) == 1
    assert progression.change is None
