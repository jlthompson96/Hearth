"""Manual entry for training: workouts with their sets, and body weight.

The second half of the other door (rule 3), with the promises the money half
keeps — nothing overwritten, nothing beside the golden fixture, a hand-entered
entry removable and an imported one only with its import.

Weights are stored in the unit they were typed in, pounds, and never
converted. The schema has carried a unit beside every weight since Phase 1, so
nothing needs to: 225 lb through kilograms and back is 224.999 lb, which is the
rounding failure the evals exist to catch. What that does ask for is one unit
per lift, because the tools subtract a lift's weights from each other — so a
second unit for anything already logged is refused here, and the tools refuse
to subtract across units whatever got in.
"""

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from db.models import BodyMetric, ImportBatch, Workout, WorkoutSet
from ingest.errors import Conflict, NotFound, Refused
from ingest.importer import refuse_if_fixture
from ingest.measures import BODY_WEIGHT, WEIGHT_UNIT
from tools.fitness import spelling

#: Re-exported: the route and the tests read the unit from the entry module.
__all__ = ["BODY_WEIGHT", "WEIGHT_UNIT"]

#: Mirrors the `kind_known` check constraint on `workout`.
KINDS = ("strength", "cardio", "mobility", "sport", "other")

#: A note is for the person reading their own log, not a document.
NOTES_LIMIT = 500


@dataclass(frozen=True)
class SetIn:
    exercise: str
    reps: int
    #: None for bodyweight work: no load, which is not zero load.
    weight: Decimal | None


@dataclass(frozen=True)
class SetView:
    exercise: str
    set_number: int
    reps: int
    weight: Decimal | None
    unit: str | None


@dataclass(frozen=True)
class WorkoutView:
    id: uuid.UUID
    performed_on: dt.date
    kind: str
    duration_minutes: Decimal | None
    notes: str | None
    sets: tuple[SetView, ...]


@dataclass(frozen=True)
class BodyWeightView:
    id: int
    as_of: dt.date
    weight: Decimal
    unit: str


def exercise_names(conn: sa.Connection) -> list[str]:
    """Every lift logged, for the form to offer — the names the tool matches."""
    return list(
        conn.execute(
            sa.select(WorkoutSet.exercise).distinct().order_by(WorkoutSet.exercise)
        ).scalars()
    )


def _as_logged(conn: sa.Connection, typed: str) -> str:
    """The logged name `typed` means, or `typed` itself, trimmed and in lower
    case. The tool matches a lift by name, so "Bench Press" beside "bench press"
    would be two lifts to it — and a question about either would match
    neither. Only spelling is forgiven: "bench" is a new name, not a guess."""
    wanted = spelling(typed)
    for name in exercise_names(conn):
        if spelling(name) == wanted:
            return name
    return " ".join(typed.lower().split())


def _refuse_another_unit(conn: sa.Connection, exercise: str) -> None:
    logged = conn.execute(
        sa.select(WorkoutSet.weight_unit)
        .distinct()
        .where(
            WorkoutSet.exercise == exercise,
            WorkoutSet.weight_unit.is_not(None),
            WorkoutSet.weight_unit != WEIGHT_UNIT,
        )
    ).scalars()
    other = next(iter(logged), None)
    if other is not None:
        raise Refused(
            f"{exercise} is already logged in {other}, and weights here are entered in "
            f"{WEIGHT_UNIT}. A lift is kept in one unit, because its progress is one "
            "weight subtracted from another."
        )


def record_workout(
    conn: sa.Connection,
    *,
    performed_on: dt.date,
    sets: Sequence[SetIn],
    kind: str = "strength",
    duration_minutes: Decimal | None = None,
    notes: str | None = None,
) -> WorkoutView:
    """`performed_on` is required and never defaulted. Sets are numbered in the
    order given, counting within each exercise, which is how a lifter counts
    them. Every set is checked before anything is written."""
    refuse_if_fixture(conn)

    if kind not in KINDS:
        raise Refused(f"Unknown kind {kind!r}. It must be one of {list(KINDS)}.")
    if kind == "strength" and not sets:
        raise Refused("A strength session needs at least one set.")
    if duration_minutes is not None and duration_minutes <= 0:
        raise Refused("A duration, when given, is more than zero minutes.")
    notes = (notes or "").strip() or None
    if notes and len(notes) > NOTES_LIMIT:
        raise Refused(f"Notes are at most {NOTES_LIMIT} characters.")

    named: list[tuple[str, SetIn]] = []
    # A new lift typed two ways in one session is still one lift.
    chosen: dict[str, str] = {}
    for position, entered in enumerate(sets, start=1):
        if not entered.exercise.strip():
            raise Refused(f"Set {position} needs an exercise name.")
        if entered.reps < 0:
            raise Refused(f"Set {position}: reps cannot be fewer than none.")
        if entered.weight is not None and entered.weight < 0:
            raise Refused(f"Set {position}: a weight is never negative.")
        key = spelling(entered.exercise)
        if key not in chosen:
            chosen[key] = _as_logged(conn, entered.exercise)
        exercise = chosen[key]
        if entered.weight is not None:
            _refuse_another_unit(conn, exercise)
        named.append((exercise, entered))

    workout_id = conn.execute(
        sa.insert(Workout)
        .values(
            performed_on=performed_on, kind=kind, duration_minutes=duration_minutes, notes=notes
        )
        .returning(Workout.id)
    ).scalar_one()

    counted: Counter[str] = Counter()
    for exercise, entered in named:
        counted[exercise] += 1
        conn.execute(
            sa.insert(WorkoutSet).values(
                workout_id=workout_id,
                exercise=exercise,
                set_number=counted[exercise],
                reps=entered.reps,
                weight=entered.weight,
                weight_unit=WEIGHT_UNIT if entered.weight is not None else None,
            )
        )

    return next(w for w in _workouts(conn, sa.true()) if w.id == workout_id)


def remove_workout(conn: sa.Connection, workout_id: uuid.UUID) -> None:
    """Only one entered by hand, and its sets with it. An imported session goes
    with its import, as an imported balance does."""
    row = conn.execute(sa.select(Workout.batch_id).where(Workout.id == workout_id)).first()
    if row is None:
        raise NotFound(f"no workout {workout_id}")
    if row.batch_id is not None:
        raise Refused(
            "That session came from an import. Remove the import instead, under Data & imports."
        )
    conn.execute(sa.delete(WorkoutSet).where(WorkoutSet.workout_id == workout_id))
    conn.execute(sa.delete(Workout).where(Workout.id == workout_id))


def recent_workouts(conn: sa.Connection, limit: int = 10) -> list[WorkoutView]:
    """The last few sessions entered by hand, newest first, so a slip can be
    seen and removed."""
    return _workouts(conn, Workout.batch_id.is_(None), limit=limit)


def _workouts(
    conn: sa.Connection, where: sa.ColumnElement[bool], limit: int | None = None
) -> list[WorkoutView]:
    workouts = conn.execute(
        sa.select(
            Workout.id, Workout.performed_on, Workout.kind, Workout.duration_minutes, Workout.notes
        )
        .where(where)
        .order_by(Workout.performed_on.desc(), Workout.created_at.desc())
        .limit(limit)
    ).all()
    if not workouts:
        return []
    sets: dict[uuid.UUID, list[SetView]] = {w.id: [] for w in workouts}
    for s in conn.execute(
        sa.select(
            WorkoutSet.workout_id,
            WorkoutSet.exercise,
            WorkoutSet.set_number,
            WorkoutSet.reps,
            WorkoutSet.weight,
            WorkoutSet.weight_unit,
        )
        .where(WorkoutSet.workout_id.in_(sets))
        .order_by(WorkoutSet.id)
    ):
        sets[s.workout_id].append(
            SetView(s.exercise, s.set_number, s.reps, s.weight, s.weight_unit)
        )
    return [
        WorkoutView(w.id, w.performed_on, w.kind, w.duration_minutes, w.notes, tuple(sets[w.id]))
        for w in workouts
    ]


# --- body weight --------------------------------------------------------------


def record_body_weight(conn: sa.Connection, *, as_of: dt.date, weight: Decimal) -> BodyWeightView:
    """`as_of` is required and never defaulted. A day already weighed is never
    overwritten; remove that entry first if this one replaces it."""
    refuse_if_fixture(conn)

    if weight <= 0:
        raise Refused("A body weight is more than zero.")
    other = conn.execute(
        sa.select(BodyMetric.unit)
        .where(BodyMetric.metric == BODY_WEIGHT, BodyMetric.unit != WEIGHT_UNIT)
        .limit(1)
    ).scalar()
    if other is not None:
        raise Refused(
            f"Body weight is already logged in {other}, and weights here are entered in "
            f"{WEIGHT_UNIT}. A measurement is kept in one unit, because its change is one "
            "value subtracted from another."
        )

    existing = conn.execute(
        sa.select(ImportBatch.original_filename)
        .select_from(BodyMetric)
        .outerjoin(ImportBatch, ImportBatch.id == BodyMetric.batch_id)
        .where(BodyMetric.metric == BODY_WEIGHT, BodyMetric.as_of == as_of)
    ).first()
    if existing is not None:
        source = f"imported from {existing[0]}" if existing[0] else "entered by hand"
        raise Conflict(
            f"There is already a body weight for {as_of:%Y-%m-%d}, {source}. A recorded "
            "figure is never overwritten; remove that one first if this replaces it."
        )

    entry_id = conn.execute(
        sa.insert(BodyMetric)
        .values(as_of=as_of, metric=BODY_WEIGHT, value=weight, unit=WEIGHT_UNIT)
        .returning(BodyMetric.id)
    ).scalar_one()
    return next(e for e in _body_weights(conn, BodyMetric.id == entry_id))


def remove_body_weight(conn: sa.Connection, entry_id: int) -> None:
    row = conn.execute(
        sa.select(BodyMetric.batch_id).where(
            BodyMetric.id == entry_id, BodyMetric.metric == BODY_WEIGHT
        )
    ).first()
    if row is None:
        raise NotFound(f"no body weight {entry_id}")
    if row.batch_id is not None:
        raise Refused(
            "That body weight came from an import. Remove the import instead, under Data & imports."
        )
    conn.execute(sa.delete(BodyMetric).where(BodyMetric.id == entry_id))


def recent_body_weights(conn: sa.Connection, limit: int = 10) -> list[BodyWeightView]:
    return _body_weights(conn, BodyMetric.batch_id.is_(None), limit=limit)


def _body_weights(
    conn: sa.Connection, where: sa.ColumnElement[bool], limit: int | None = None
) -> list[BodyWeightView]:
    rows = conn.execute(
        sa.select(BodyMetric.id, BodyMetric.as_of, BodyMetric.value, BodyMetric.unit)
        .where(BodyMetric.metric == BODY_WEIGHT, where)
        .order_by(BodyMetric.as_of.desc(), BodyMetric.id.desc())
        .limit(limit)
    ).all()
    return [BodyWeightView(*row) for row in rows]
