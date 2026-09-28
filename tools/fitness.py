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
from collections.abc import Callable, Sequence
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


#: At most this many recordings are listed one to a line. A month of daily
#: weigh-ins fits, and so does the fixture's year of month-ends, so every
#: answer measured before grouping existed reads what it read then.
MAX_POINTS = 31

#: Past that, they are grouped by the smallest of week, month, quarter and year
#: that comes to this many groups or fewer. A group's line is about seventy
#: characters, so 24 of them stay under 2,000 — where the real body-weight
#: history, 809 weigh-ins listed, was 19,484 characters: more than the whole
#: 8,192-token window before the prompt was counted.
MAX_GROUPS = 24


@dataclass(frozen=True)
class MetricPoint:
    as_of: dt.date
    value: Decimal


@dataclass(frozen=True)
class MetricGroup:
    """The recordings in one week, month, quarter or year, summarised here so
    the model is never handed a column of figures to average itself."""

    label: str
    first: dt.date
    last: dt.date
    count: int
    #: To tenths: an average is computed, not recorded.
    average: Decimal
    #: Recordings, so exactly as recorded.
    low: Decimal
    high: Decimal


@dataclass(frozen=True)
class MetricTrend:
    metric: str
    unit: str | None
    start: dt.date
    end: dt.date
    points: tuple[MetricPoint, ...]
    change: Decimal | None
    #: None when the points are few enough to list; otherwise the grain they
    #: were grouped by, and the groups, oldest first.
    grouped_by: str | None = None
    groups: tuple[MetricGroup, ...] = ()


def _week(day: dt.date) -> str:
    return f"week of {day - dt.timedelta(days=day.weekday()):%Y-%m-%d}"


def _quarter(day: dt.date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


#: Finest first. Each labels a date with the group it falls in; the labels of
#: one grain sort in date order, which the grouping below relies on.
_GRAINS: tuple[tuple[str, Callable[[dt.date], str]], ...] = (
    ("week", _week),
    ("month", lambda day: f"{day:%Y-%m}"),
    ("quarter", _quarter),
    ("year", lambda day: f"{day.year}"),
)


def group_points(points: Sequence[MetricPoint]) -> tuple[str, tuple[MetricGroup, ...]]:
    """`points`, oldest first, grouped by the finest grain that gives at most
    MAX_GROUPS groups — by year when none does."""
    for grain, label in _GRAINS:
        if len({label(p.as_of) for p in points}) <= MAX_GROUPS or grain == "year":
            break
    runs: dict[str, list[MetricPoint]] = {}
    for point in points:
        runs.setdefault(label(point.as_of), []).append(point)
    return grain, tuple(
        MetricGroup(
            label=name,
            first=run[0].as_of,
            last=run[-1].as_of,
            count=len(run),
            average=(sum((p.value for p in run), Decimal("0")) / len(run)).quantize(TENTHS),
            low=min(p.value for p in run),
            high=max(p.value for p in run),
        )
        for name, run in runs.items()
    )


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
    grouped_by, groups = group_points(points) if len(points) > MAX_POINTS else (None, ())
    return MetricTrend(
        metric=metric,
        unit=rows[0].unit if rows else None,
        start=start,
        end=end,
        # One observation is not a change of nothing; it is not enough to say.
        change=(points[-1].value - points[0].value) if len(points) >= 2 else None,
        points=points,
        grouped_by=grouped_by,
        groups=groups,
    )
