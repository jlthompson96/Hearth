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


class MixedUnitsError(ValueError):
    """A lift or measurement recorded in more than one unit over the period
    asked about. Its values are subtracted from each other, and 127.500 kg to
    275 lb is not a change of +147.5. Manual entry and imports refuse a second
    unit; this is the tools' own guard, for whatever got in another way."""

    def __init__(self, name: str, units: tuple[str, ...]) -> None:
        super().__init__(f"{name} is recorded in {' and '.join(units)}")
        self.name = name
        self.units = units


def spelling(name: str) -> str:
    """How a name is compared: case, runs of spaces, underscores and hyphens
    ignored. "Bench_Press" and "bench press" are one lift; "bench" is not."""
    return " ".join(name.replace("_", " ").replace("-", " ").lower().split())


def _one_unit(conn: sa.Connection, name: str, query: str, params: dict[str, object]) -> None:
    units = tuple(r[0] for r in conn.execute(sa.text(query), params))
    if len(units) > 1:
        raise MixedUnitsError(name, units)


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

    _one_unit(
        conn,
        exercise,
        """
        select distinct ws.weight_unit
        from workout_set ws
        join workout w on w.id = ws.workout_id
        where ws.exercise = :exercise
          and w.performed_on between :start and :end
          and ws.weight_unit is not null
        order by 1
        """,
        {"exercise": exercise, "start": start, "end": end},
    )

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


class UnknownMetricError(LookupError):
    """Same reasoning as UnknownExerciseError: "you have not recorded this"
    and "this did not move" are different answers."""


@dataclass(frozen=True)
class MetricPoint:
    as_of: dt.date
    value: Decimal


@dataclass(frozen=True)
class MetricTrend:
    metric: str
    unit: str | None
    start: dt.date
    end: dt.date
    points: tuple[MetricPoint, ...]
    change: Decimal | None


def get_body_metric_trend(
    conn: sa.Connection, metric: str, start: dt.date, end: dt.date
) -> MetricTrend:
    """One recorded body measurement across a period, oldest first.

    Values are returned exactly as recorded, like lifted weights and for the
    same reason: quietly rounding 82.500 kg to 82.5 loses precision in the one
    place the person tracking it would notice.
    """
    known = conn.execute(
        sa.text("select count(*) from body_metric where metric = :metric"),
        {"metric": metric},
    ).scalar_one()
    if not known:
        raise UnknownMetricError(metric)

    _one_unit(
        conn,
        metric,
        "select distinct unit from body_metric "
        "where metric = :metric and as_of between :start and :end order by 1",
        {"metric": metric, "start": start, "end": end},
    )

    rows = conn.execute(
        sa.text(
            "select as_of, value, unit from body_metric "
            "where metric = :metric and as_of between :start and :end order by as_of"
        ),
        {"metric": metric, "start": start, "end": end},
    ).all()

    points = tuple(MetricPoint(as_of=r.as_of, value=r.value) for r in rows)
    return MetricTrend(
        metric=metric,
        unit=rows[0].unit if rows else None,
        start=start,
        end=end,
        # One observation is not a change of nothing; it is not enough to say.
        change=(points[-1].value - points[0].value) if len(points) >= 2 else None,
        points=points,
    )
