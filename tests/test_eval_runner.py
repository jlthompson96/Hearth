"""The eval runner records every case it was given, a crash included.

A case that raised used to leave no result behind: `runner.run` let the
exception through, the pytest shell appended nothing, and the committed totals
counted only the cases that came back. When the carry-forward merge broke every
specialist turn, a full run would have recorded the routing and refusal cases
alone and read as a clean pass. So a crash is a failed run, named for what it
raised, and a result that covers fewer cases than the file holds says so in
its name.

No model and no database: the specialist is replaced, and results are written
to a temporary folder.
"""

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from evals import runner
from evals.runner import Case, Result

TODAY = dt.date(2026, 9, 20)


def _grounded_case() -> Case:
    return Case(id="c", kind="grounded", question="q", agent="tally", must_contain=["$1.00"])


def _results(count: int) -> list[Result]:
    return [
        Result(id=f"c{i}", kind="routing", runs=3, passed=3, required="all runs", ok=True)
        for i in range(count)
    ]


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Results go to a temporary folder, under a fixed commit."""
    monkeypatch.setattr(runner, "RESULTS", tmp_path)
    monkeypatch.setattr(runner, "head_sha", lambda: "abc1234")
    yield tmp_path


def test_a_case_that_raises_is_a_failed_run_that_names_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object, **__: object) -> object:
        raise NameError("name 'model' is not defined")

    monkeypatch.setitem(runner.SPECIALISTS, "tally", broken)

    result = runner.run(_grounded_case(), TODAY, runs=3)

    assert (result.passed, result.runs, result.ok) == (0, 3, False)
    assert result.detail == ["raised NameError: name 'model' is not defined"]


def test_a_run_that_raises_once_still_counts_the_runs_that_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One crash is one failed run, not a lost case: the other runs stand."""
    planned: list[tuple[bool, str] | Exception] = [
        (True, ""),
        RuntimeError("LM Studio went away"),
        (True, ""),
    ]
    outcomes = iter(planned)

    def once(case: Case, today: dt.date, notes: list[str] | None = None) -> tuple[bool, str]:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "run_once", once)

    result = runner.run(_grounded_case(), TODAY, runs=3)

    assert (result.passed, result.ok) == (2, True)
    assert result.detail == ["raised RuntimeError: LM Studio went away"]


def test_a_run_that_covers_every_case_is_named_for_its_commit(recorded: Path) -> None:
    path = runner.record(_results(2), model="m", dirty=False, expected=2)

    assert path.name == "abc1234.json"
    totals = json.loads(path.read_text(encoding="utf-8"))["totals"]
    assert (totals["cases"], totals["cases_expected"]) == (2, 2)


def test_a_run_missing_cases_says_so_in_its_name(recorded: Path) -> None:
    """A run filtered with `-k`, or one that lost cases some other way, is not
    a baseline, and its file name must not let it pass for one."""
    path = runner.record(_results(1), model="m", dirty=False, expected=3)

    assert path.name == "abc1234-incomplete.json"
    totals = json.loads(path.read_text(encoding="utf-8"))["totals"]
    assert (totals["cases"], totals["cases_expected"]) == (1, 3)


def test_incomplete_and_dirty_are_both_named(recorded: Path) -> None:
    """Still ending `-dirty.json`, so .gitignore keeps it out of a commit."""
    path = runner.record(_results(1), model="m", dirty=True, expected=3)

    assert path.name == "abc1234-incomplete-dirty.json"


def test_a_figure_without_its_sign_is_noted_and_does_not_fail_the_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report-only until a measured run shows what enforcing it would catch."""
    from agents.loop import DoneEvent, Event, TokenEvent, ToolResultEvent

    def answers(*_: object, **__: object) -> Iterator[Event]:
        yield ToolResultEvent("net_worth_trend", "change over the period: +$1.00")
        yield TokenEvent("It rose $1.00, about 38 thousand by another reckoning.")
        yield DoneEvent()

    monkeypatch.setitem(runner.SPECIALISTS, "tally", answers)

    result = runner.run(_grounded_case(), TODAY, runs=2)

    assert (result.passed, result.ok) == (2, True)
    assert result.notes == ["would flag without a sign: ['38 thousand']"]
