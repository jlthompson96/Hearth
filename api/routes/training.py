"""Manual entry for training: workouts with their sets, and body weight.

A weight arrives as text — "225", "235.5" — and is read strictly, as a dollar
amount is: exactly, or refused by the shape of what was typed, never parsed as
a float on the way. Every weight is in pounds; the unit is not typed, it is the
one the listing names.
"""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from api.refusals import REFUSALS
from db.session import readonly_connection
from db.writer import writer_connection
from ingest import training
from ingest.errors import Refused
from ingest.importer import fixture_loaded
from ingest.values import ValueFormatError, parse_weight

router = APIRouter(prefix="/api", tags=["manual entry"])


class WorkoutSetOut(BaseModel):
    exercise: str
    set_number: int
    reps: int
    #: None for bodyweight work.
    weight: Decimal | None
    unit: str | None


class WorkoutOut(BaseModel):
    id: uuid.UUID
    performed_on: dt.date
    kind: str
    duration_minutes: Decimal | None
    notes: str | None
    sets: list[WorkoutSetOut]


class BodyWeightOut(BaseModel):
    id: int
    as_of: dt.date
    weight: Decimal
    unit: str


class TrainingListing(BaseModel):
    fixture_loaded: bool
    #: The unit every weight is entered and shown in.
    unit: str
    kinds: list[str]
    #: Every lift logged, so the form can offer the names the tool matches.
    exercises: list[str]
    #: The last few entered by hand, so a slip can be seen and removed.
    workouts: list[WorkoutOut]
    body_weights: list[BodyWeightOut]


class NewSet(BaseModel):
    exercise: str = Field(min_length=1, max_length=80)
    reps: int = Field(ge=0, le=1000)
    #: As typed, in pounds. Empty or null for bodyweight work.
    weight: str | None = Field(default=None, max_length=16)


class NewWorkout(BaseModel):
    #: Required, with no default (CLAUDE.md: a date never defaults to today).
    performed_on: dt.date
    kind: str = "strength"
    duration_minutes: Decimal | None = Field(default=None, max_digits=8, decimal_places=2)
    notes: str | None = Field(default=None, max_length=training.NOTES_LIMIT)
    sets: list[NewSet] = Field(default_factory=list, max_length=100)


class NewBodyWeight(BaseModel):
    as_of: dt.date
    #: As typed, in pounds: "181.4".
    weight: str = Field(min_length=1, max_length=16)


def _workout(view: training.WorkoutView) -> WorkoutOut:
    return WorkoutOut(
        id=view.id,
        performed_on=view.performed_on,
        kind=view.kind,
        duration_minutes=view.duration_minutes,
        notes=view.notes,
        sets=[WorkoutSetOut(**vars(s)) for s in view.sets],
    )


def _weight(cell: str, where: str) -> Decimal:
    try:
        return parse_weight(cell)
    except ValueFormatError as error:
        raise Refused(f"{where}: {error}.") from None


@router.get("/training", response_model=TrainingListing, summary="Training entered by hand")
def list_training() -> TrainingListing:
    with readonly_connection() as conn:
        return TrainingListing(
            fixture_loaded=fixture_loaded(conn),
            unit=training.WEIGHT_UNIT,
            kinds=list(training.KINDS),
            exercises=training.exercise_names(conn),
            workouts=[_workout(w) for w in training.recent_workouts(conn)],
            body_weights=[BodyWeightOut(**vars(b)) for b in training.recent_body_weights(conn)],
        )


@router.post(
    "/workouts",
    response_model=WorkoutOut,
    status_code=201,
    responses=REFUSALS,
    summary="Record a workout by hand",
)
def record_workout(request: NewWorkout) -> WorkoutOut:
    sets = [
        training.SetIn(
            exercise=s.exercise,
            reps=s.reps,
            weight=_weight(s.weight, f"Set {i}") if s.weight and s.weight.strip() else None,
        )
        for i, s in enumerate(request.sets, start=1)
    ]
    with writer_connection() as conn:
        view = training.record_workout(
            conn,
            performed_on=request.performed_on,
            kind=request.kind,
            duration_minutes=request.duration_minutes,
            notes=request.notes,
            sets=sets,
        )
    return _workout(view)


@router.delete(
    "/workouts/{workout_id}",
    status_code=204,
    responses=REFUSALS,
    summary="Remove a workout entered by hand",
)
def delete_workout(workout_id: uuid.UUID) -> Response:
    with writer_connection() as conn:
        training.remove_workout(conn, workout_id)
    return Response(status_code=204)


@router.post(
    "/body-weights",
    response_model=BodyWeightOut,
    status_code=201,
    responses=REFUSALS,
    summary="Record a body weight by hand",
)
def record_body_weight(request: NewBodyWeight) -> BodyWeightOut:
    weight = _weight(request.weight, "Body weight")
    with writer_connection() as conn:
        entry = training.record_body_weight(conn, as_of=request.as_of, weight=weight)
    return BodyWeightOut(**vars(entry))


@router.delete(
    "/body-weights/{entry_id}",
    status_code=204,
    responses=REFUSALS,
    summary="Remove a body weight entered by hand",
)
def delete_body_weight(entry_id: int) -> Response:
    with writer_connection() as conn:
        training.remove_body_weight(conn, entry_id)
    return Response(status_code=204)
