"""Loading and running the behavioural cases.

Kept apart from the pytest file so the runner can be read on its own and so
nothing here depends on pytest — the recording half is ordinary code, and the
test file is a thin shell over it.

Every case runs `runs` times and produces a pass rate. Nothing in here judges
prose: a case is a string comparison, because the only local model available is
the same one being graded and asking it to mark its own work would be circular.
"""

import datetime as dt
import json
import subprocess
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agents import forge, preflight, tally
from agents.conversation import Exchange, previous, window
from agents.grounding import ungrounded
from agents.loop import (
    Detail,
    Event,
    RefusedEvent,
    RoutedEvent,
    TokenEvent,
    ToolEvent,
    ToolResultEvent,
)
from scripts.seed import YEAR
from steward import graph as steward

EVALS = Path(__file__).resolve().parent
CASES_PATH = EVALS / "cases.yaml"
RESULTS = EVALS / "results"

SPECIALISTS = {"tally": tally.answer, "forge": forge.answer}


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    question: str
    expect: str | None = None
    expect_tool: str | None = None
    expect_signal: str | None = None
    agent: str | None = None
    must_contain: list[str] = field(default_factory=list)
    must_contain_any: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    all_runs: bool = False
    #: How much the specialist is asked to say. Cases default to "normal", the
    #: level every question starts at; a few pin another, because a longer
    #: answer walks through more figures and so has more ways to get one wrong.
    detail: Detail = "normal"
    #: Earlier exchanges in the thread, scripted rather than generated: a
    #: follow-up case measures the follow-up, not the turn before it as well.
    history: tuple[Exchange, ...] = ()
    #: Present in the file for a reader; carried so it reaches the results.
    note: str | None = None


@dataclass
class Result:
    id: str
    kind: str
    runs: int
    passed: int
    required: str
    ok: bool
    detail: list[str] = field(default_factory=list)
    #: Mean wall-clock seconds per run. A pass rate says whether an answer is
    #: right; this says whether anyone would wait for it.
    seconds: float = 0.0

    @property
    def rate(self) -> float:
        return self.passed / self.runs if self.runs else 0.0


def _year(value: Any) -> Any:
    """`{year}` in the case file becomes the fixture's year.

    The figures never move between years; the dates do. Writing them literally
    would make every date-bearing case fail on 1 January.
    """
    if isinstance(value, str):
        return value.replace("{year}", str(YEAR))
    if isinstance(value, list):
        return [_year(v) for v in value]
    if isinstance(value, dict):
        return {k: _year(v) for k, v in value.items()}
    return value


def load() -> tuple[list[Case], dt.date, int]:
    raw = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    today = dt.date.fromisoformat(_year(raw["today"]))
    runs = int(raw.get("runs", 3))

    cases = []
    for entry in raw["cases"]:
        fields = {k: _year(v) for k, v in entry.items()}
        fields["history"] = tuple(Exchange(**e) for e in fields.get("history", []))
        cases.append(Case(**fields))
    return cases, today, runs


# --- one attempt at one case --------------------------------------------------


def _answer(case: Case, today: dt.date) -> Iterator[Event]:
    """Route, or go straight to a named specialist.

    Naming the agent is what keeps a tool-selection failure and a routing
    failure from looking identical from outside.
    """
    if case.agent is None:
        return steward.answer(case.question, today=today, detail=case.detail, history=case.history)
    return SPECIALISTS[case.agent](
        case.question, today=today, detail=case.detail, history=case.history
    )


def _text_and_tools(
    case: Case, today: dt.date
) -> tuple[str, list[str], str | None, list[str], str | None]:
    text: list[str] = []
    tools: list[str] = []
    signal: str | None = None
    results: list[str] = []
    answered_by = case.agent

    for event in _answer(case, today):
        match event:
            case TokenEvent(text=chunk, provisional=False):
                text.append(chunk)
            case ToolEvent(name=name):
                tools.append(name)
            case RefusedEvent(signal=fired):
                signal = fired
            case ToolResultEvent(result=result):
                results.append(result)
            case RoutedEvent(destination=destination):
                answered_by = destination
            case _:
                pass
    return "".join(text), tools, signal, results, answered_by


def run_once(case: Case, today: dt.date) -> tuple[bool, str]:
    """(passed, why not)."""
    if case.kind == "routing":
        from steward.router import ConstrainedJSONRouter

        routed = ConstrainedJSONRouter().route(case.question, previous(case.history))
        got = routed.destination.value
        return got == case.expect, f"routed to {got}, wanted {case.expect}"

    if case.kind == "refusal":
        # No model: the pre-flight check is ordinary code and runs before
        # inference, which is the whole reason this path can be asserted.
        # As the Steward checks it: the new question with the one before it.
        last = previous(case.history)
        refusal = preflight.check_conversation([last.question] if last else [], case.question)
        fired = refusal.signal if refusal else None
        return fired == case.expect_signal, f"signal {fired!r}, wanted {case.expect_signal!r}"

    text, tools, _, results, answered_by = _text_and_tools(case, today)

    if case.kind == "tool":
        ok = case.expect_tool in tools
        return ok, f"called {tools or 'nothing'}, wanted {case.expect_tool}"

    # grounded and caveat are both "is the right string present", which is the
    # only thing a non-circular check can ask of prose.
    missing = [s for s in case.must_contain if s not in text]
    if missing:
        return False, f"missing {missing} from: {text[:160]!r}"

    if case.must_contain_any and not any(s in text.lower() for s in case.must_contain_any):
        return False, f"none of {case.must_contain_any} in: {text[:160]!r}"

    forbidden = [s for s in case.must_not_contain if s in text.lower()]
    if forbidden:
        return False, f"said {forbidden} — that is no-data reported as no-change: {text[:160]!r}"

    # Rule 1: the right figure being present is not enough if a wrong one is
    # present beside it. The same check the chat runs on every real answer.
    # Earlier turns count through their questions and tool results, as in the
    # chat route — never through their answers.
    shown = window(case.history, answered_by) if answered_by else []
    earlier = [t for e in shown for t in (e.question, *e.results)]
    flags = ungrounded(text, [*results, case.question, *earlier])
    if flags:
        return False, f"figures no tool returned: {flags} in: {text[:160]!r}"

    return True, ""


def run(case: Case, today: dt.date, runs: int) -> Result:
    passed = 0
    detail: list[str] = []
    started = time.perf_counter()
    for _ in range(runs):
        ok, why = run_once(case, today)
        passed += ok
        if not ok and why not in detail:
            detail.append(why)
    seconds = round((time.perf_counter() - started) / runs, 2) if runs else 0.0

    required = "all runs" if case.all_runs else "majority"
    ok = passed == runs if case.all_runs else passed > runs // 2
    return Result(case.id, case.kind, runs, passed, required, ok, detail, seconds)


# --- recording ----------------------------------------------------------------


def head_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=EVALS.parent,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def record(
    results: list[Result], *, model: str, dirty: bool, reasoning_effort: str | None = None
) -> Path:
    """Write `results/<sha>.json`.

    Committed, so a prompt change produces a diff in a number rather than a
    memory of a number. The model name is recorded with them because a pass rate
    belongs to a model — the same cases against a different one are a different
    measurement, not a comparable one. The same goes for how much it reasons:
    a run with reasoning off gets its own file rather than overwriting the one
    with it on.
    """
    RESULTS.mkdir(exist_ok=True)
    sha = head_sha()
    payload = {
        "sha": sha,
        "dirty": dirty,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "fixture_year": YEAR,
        "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
        "totals": {
            "cases": len(results),
            "passing": sum(1 for r in results if r.ok),
            "runs": sum(r.runs for r in results),
            "runs_passed": sum(r.passed for r in results),
            "seconds": round(sum(r.seconds * r.runs for r in results), 1),
        },
        "results": [asdict(r) for r in results],
    }
    effort = f"-reasoning-{reasoning_effort}" if reasoning_effort else ""
    path = RESULTS / f"{sha}{effort}{'-dirty' if dirty else ''}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def working_tree_dirty() -> bool:
    """A result recorded against a sha whose tree has uncommitted changes did
    not measure that sha. Say so in the filename rather than quietly filing it."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            cwd=EVALS.parent,
        )
        return bool(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
