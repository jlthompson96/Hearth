"""Training typed in by hand: workouts with their sets, and body weight.

The same promises as the money side of manual entry — nothing overwritten,
nothing beside the fixture, a hand-entered figure removable and an imported one
not — plus two of its own. Weights are stored in the unit they were typed in,
pounds, and never converted: 225 lb through kilograms and back is 224.999 lb.
And a lift is logged in one unit only, because the tools subtract its weights.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from db.models import BodyMetric, ImportBatch, Workout, WorkoutSet
from ingest.errors import Conflict, FixtureLoaded, NotFound, Refused
from ingest.training import (
    BODY_WEIGHT,
    WEIGHT_UNIT,
    SetIn,
    exercise_names,
    recent_body_weights,
    recent_workouts,
    record_body_weight,
    record_workout,
    remove_body_weight,
    remove_workout,
)

DAY = dt.date(2026, 9, 21)


def _batch(conn: sa.Connection, digit: str) -> object:
    """An import, as far as a workout's `batch_id` needs one."""
    return conn.execute(
        sa.insert(ImportBatch)
        .values(
            file_sha256=digit * 64,
            original_filename="history.csv",
            source_label="test",
            normalizer="test",
            as_of=DAY,
            row_count=0,
        )
        .returning(ImportBatch.id)
    ).scalar_one()


def _squat(*weights: str | None, reps: int = 5, name: str = "back squat") -> list[SetIn]:
    return [SetIn(exercise=name, reps=reps, weight=Decimal(w) if w else None) for w in weights]


def test_the_unit_is_pounds() -> None:
    """Decided by the owner, 2026-09-25."""
    assert WEIGHT_UNIT == "lb"


# --- workouts -----------------------------------------------------------------


def test_a_workout_is_stored_with_its_sets_in_pounds_exactly(conn: sa.Connection) -> None:
    record_workout(conn, performed_on=DAY, sets=_squat("225", "225", "235.5"))

    rows = conn.execute(
        sa.select(WorkoutSet.set_number, WorkoutSet.weight, WorkoutSet.weight_unit).order_by(
            WorkoutSet.set_number
        )
    ).all()
    assert [tuple(r) for r in rows] == [
        (1, Decimal("225.000"), "lb"),
        (2, Decimal("225.000"), "lb"),
        (3, Decimal("235.500"), "lb"),
    ]


def test_set_numbers_count_within_each_exercise(conn: sa.Connection) -> None:
    record_workout(
        conn,
        performed_on=DAY,
        sets=[*_squat("225", "225"), *_squat("185", name="bench press"), *_squat("235")],
    )

    rows = conn.execute(
        sa.select(WorkoutSet.exercise, WorkoutSet.set_number).order_by(WorkoutSet.id)
    ).all()
    assert [tuple(r) for r in rows] == [
        ("back squat", 1),
        ("back squat", 2),
        ("bench press", 1),
        ("back squat", 3),
    ]


def test_a_bodyweight_set_records_no_load_and_no_unit(conn: sa.Connection) -> None:
    """Zero pounds would read as a lift of nothing; no load is what happened."""
    record_workout(conn, performed_on=DAY, sets=_squat(None, reps=8, name="pull-up"))

    row = conn.execute(sa.select(WorkoutSet.weight, WorkoutSet.weight_unit)).one()
    assert tuple(row) == (None, None)


def test_a_name_already_logged_is_reused_whatever_its_spelling(conn: sa.Connection) -> None:
    """The tool matches a lift by name. "Bench Press" beside "bench press" would
    be two lifts to it, and neither would match a question about either."""
    record_workout(conn, performed_on=DAY, sets=_squat("185", name="bench press"))
    record_workout(
        conn,
        performed_on=DAY + dt.timedelta(days=2),
        sets=[*_squat("190", name="  Bench   Press "), *_squat(None, name="Pull Up")],
    )
    record_workout(conn, performed_on=DAY + dt.timedelta(days=4), sets=_squat(None, name="pull_up"))

    assert exercise_names(conn) == ["bench press", "pull up"]


def test_a_new_lift_typed_two_ways_in_one_session_is_one_lift(conn: sa.Connection) -> None:
    record_workout(
        conn,
        performed_on=DAY,
        sets=[*_squat(None, name="pull-up"), *_squat(None, name="Pull Up")],
    )

    rows = conn.execute(
        sa.select(WorkoutSet.exercise, WorkoutSet.set_number).order_by(WorkoutSet.id)
    ).all()
    assert [tuple(r) for r in rows] == [("pull-up", 1), ("pull-up", 2)]


def test_a_new_name_is_stored_trimmed_and_in_lower_case(conn: sa.Connection) -> None:
    record_workout(conn, performed_on=DAY, sets=_squat("95", name="  Romanian   Deadlift "))

    assert exercise_names(conn) == ["romanian deadlift"]


def test_a_lift_logged_in_another_unit_is_refused(conn: sa.Connection) -> None:
    """Its weights are subtracted from each other; kilograms from pounds is a
    wrong number, not a change."""
    workout = conn.execute(
        sa.insert(Workout).values(performed_on=DAY, kind="strength").returning(Workout.id)
    ).scalar_one()
    conn.execute(
        sa.insert(WorkoutSet).values(
            workout_id=workout,
            exercise="back squat",
            set_number=1,
            reps=5,
            weight=Decimal("100"),
            weight_unit="kg",
        )
    )

    with pytest.raises(Refused, match="kg"):
        record_workout(conn, performed_on=DAY + dt.timedelta(days=1), sets=_squat("225"))


@pytest.mark.parametrize(
    ("sets", "match"),
    [
        ([], "at least one set"),
        (_squat("225", name="   "), "name"),
        (_squat("225", reps=-1), "reps"),
        (_squat("-5"), "negative"),
    ],
)
def test_a_workout_that_cannot_be_read_as_it_stands_is_refused(
    conn: sa.Connection, sets: list[SetIn], match: str
) -> None:
    with pytest.raises(Refused, match=match):
        record_workout(conn, performed_on=DAY, sets=sets)

    assert conn.execute(sa.select(sa.func.count()).select_from(Workout)).scalar_one() == 0


def test_a_session_with_no_sets_is_allowed_when_it_is_not_strength(conn: sa.Connection) -> None:
    record_workout(conn, performed_on=DAY, kind="cardio", duration_minutes=Decimal("30"), sets=[])

    [workout] = recent_workouts(conn)
    assert (workout.kind, workout.duration_minutes, workout.sets) == ("cardio", Decimal("30"), ())


def test_an_unknown_kind_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(Refused, match="kind"):
        record_workout(conn, performed_on=DAY, kind="yoga", sets=[])


def test_recent_workouts_are_the_hand_entered_ones_newest_first(conn: sa.Connection) -> None:
    batch = _batch(conn, "0")
    conn.execute(sa.insert(Workout).values(performed_on=DAY, kind="strength", batch_id=batch))
    record_workout(conn, performed_on=DAY - dt.timedelta(days=7), sets=_squat("215"))
    record_workout(conn, performed_on=DAY, sets=_squat("225", "225"))

    workouts = recent_workouts(conn)
    assert [w.performed_on for w in workouts] == [DAY, DAY - dt.timedelta(days=7)]
    assert [(s.exercise, s.reps, s.weight) for s in workouts[0].sets] == [
        ("back squat", 5, Decimal("225.000")),
        ("back squat", 5, Decimal("225.000")),
    ]


def test_a_hand_entered_workout_can_be_removed_with_its_sets(conn: sa.Connection) -> None:
    entry = record_workout(conn, performed_on=DAY, sets=_squat("225", "225"))

    remove_workout(conn, entry.id)

    assert recent_workouts(conn) == []
    assert conn.execute(sa.select(sa.func.count()).select_from(WorkoutSet)).scalar_one() == 0


def test_an_imported_workout_goes_with_its_import(conn: sa.Connection) -> None:
    batch = _batch(conn, "1")
    imported = conn.execute(
        sa.insert(Workout)
        .values(performed_on=DAY, kind="strength", batch_id=batch)
        .returning(Workout.id)
    ).scalar_one()

    with pytest.raises(Refused, match="import"):
        remove_workout(conn, imported)


def test_removing_a_workout_that_is_not_there_says_so(conn: sa.Connection) -> None:
    import uuid

    with pytest.raises(NotFound):
        remove_workout(conn, uuid.uuid4())


# --- body weight --------------------------------------------------------------


def test_body_weight_is_body_mass_in_pounds(conn: sa.Connection) -> None:
    """Stored under the name the tool already reads, `body_mass`."""
    record_body_weight(conn, as_of=DAY, weight=Decimal("181.4"))

    row = conn.execute(sa.select(BodyMetric.metric, BodyMetric.value, BodyMetric.unit)).one()
    assert tuple(row) == (BODY_WEIGHT, Decimal("181.400"), "lb")
    assert BODY_WEIGHT == "body_mass"


def test_a_day_already_weighed_is_never_overwritten(conn: sa.Connection) -> None:
    record_body_weight(conn, as_of=DAY, weight=Decimal("181.4"))

    with pytest.raises(Conflict, match="entered by hand"):
        record_body_weight(conn, as_of=DAY, weight=Decimal("180.0"))


def test_body_weight_in_another_unit_is_refused(conn: sa.Connection) -> None:
    conn.execute(
        sa.insert(BodyMetric).values(
            as_of=DAY - dt.timedelta(days=1), metric="body_mass", value=Decimal("82.5"), unit="kg"
        )
    )

    with pytest.raises(Refused, match="kg"):
        record_body_weight(conn, as_of=DAY, weight=Decimal("181.4"))


def test_a_negative_or_zero_body_weight_is_refused(conn: sa.Connection) -> None:
    for weight in ("0", "-181.4"):
        with pytest.raises(Refused):
            record_body_weight(conn, as_of=DAY, weight=Decimal(weight))


def test_a_hand_entered_body_weight_is_listed_and_can_be_removed(conn: sa.Connection) -> None:
    entry = record_body_weight(conn, as_of=DAY, weight=Decimal("181.4"))

    assert [(e.as_of, e.weight) for e in recent_body_weights(conn)] == [(DAY, Decimal("181.400"))]
    remove_body_weight(conn, entry.id)
    assert recent_body_weights(conn) == []


# --- beside the fixture -------------------------------------------------------


def test_nothing_is_entered_beside_the_fixture(seeded: sa.Connection) -> None:
    """The fixture's lifts are invented and in kilograms. A real session beside
    them would be summed into a progression that is neither."""
    with pytest.raises(FixtureLoaded):
        record_workout(seeded, performed_on=DAY, sets=_squat("225"))
    with pytest.raises(FixtureLoaded):
        record_body_weight(seeded, as_of=DAY, weight=Decimal("181.4"))
