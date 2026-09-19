"""Fitness query and compute functions.

Same contract as the finance tools: the model picks the function and its
arguments, Python does the arithmetic, and the result that reaches a prompt is
already computed and already rounded.

Weights are reported exactly as recorded, never re-rounded: turning a logged
83.750 kg into 83.8 is a quiet loss of precision in the one place a lifter
would notice it.

Estimated one-rep max uses Epley (`weight * (1 + reps / 30)`). It is an
estimate and is labelled as one — the point of computing it here rather than
letting the model do it is not precision, it is that the same input always
produces the same number.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

TENTHS = Decimal("0.1")


class UnknownExerciseError(LookupError):
    """Raised rather than returning an empty progression: "no sessions" and
    "you have never logged this lift" are different answers, and the second one
    is usually a typo in the exercise name."""


@dataclass(frozen=True)
class LiftSession:
    performed_on: dt.date
    best_weight: Decimal
    reps_at_best: int
    estimated_1rm: Decimal


@dataclass(frozen=True)
class LiftProgression:
    exercise: str
    unit: str | None
    start: dt.date
    end: dt.date
    sessions: tuple[LiftSession, ...]
    change: Decimal | None


def _estimated_1rm(weight: Decimal, reps: int) -> Decimal:
    return (weight * (1 + Decimal(reps) / Decimal(30))).quantize(TENTHS)


def get_lift_progression(
    conn: sa.Connection, exercise: str, start: dt.date, end: dt.date
) -> LiftProgression:
    """The heaviest working set per session for one lift, oldest first."""
    known = conn.execute(
        sa.text("select count(*) from workout_set where exercise = :exercise"),
        {"exercise": exercise},
    ).scalar_one()
    if not known:
        raise UnknownExerciseError(exercise)

    # The heaviest set of each session, and the reps achieved at that weight.
    rows = conn.execute(
        sa.text(
            """
            select distinct on (w.performed_on)
                   w.performed_on, ws.weight, ws.reps, ws.weight_unit
            from workout_set ws
            join workout w on w.id = ws.workout_id
            where ws.exercise = :exercise
              and w.performed_on between :start and :end
              and ws.weight is not null
            order by w.performed_on, ws.weight desc, ws.reps desc
            """
        ),
        {"exercise": exercise, "start": start, "end": end},
    ).all()

    sessions = tuple(
        LiftSession(
            performed_on=r.performed_on,
            best_weight=r.weight,
            reps_at_best=r.reps,
            estimated_1rm=_estimated_1rm(r.weight, r.reps),
        )
        for r in rows
    )

    change = sessions[-1].best_weight - sessions[0].best_weight if len(sessions) >= 2 else None
    return LiftProgression(
        exercise=exercise,
        unit=rows[0].weight_unit if rows else None,
        start=start,
        end=end,
        sessions=sessions,
        change=change,
    )
