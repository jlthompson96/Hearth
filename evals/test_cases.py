"""`make eval`.

A shell over `runner.py`. Each case is one pytest case so a failure names
itself, and the recording happens once at the end of the session.

This does not run under `make test`. Behavioural cases need a model and a
seeded database, take minutes, and measure a pass rate rather than asserting a
fact — three good reasons not to have them in the loop you run after every
edit. `pyproject.toml` points testpaths at `tests/`; `make eval` points here.
"""

import httpx
import pytest
from pydantic import ValidationError

from config import get_model_settings
from evals import runner
from evals.runner import Case, Result

CASES, TODAY, RUNS = runner.load()

#: Filled as cases run, written once at the end.
_RESULTS: list[Result] = []


@pytest.fixture(scope="session", autouse=True)
def _model_available() -> str:
    try:
        settings = get_model_settings()
    except ValidationError:
        pytest.skip("CHAT_MODEL is unset — see .env.example")
    try:
        httpx.get(f"{settings.lm_studio_base_url}/models", timeout=3.0)
    except httpx.HTTPError:
        pytest.skip(
            f"no model server at {settings.lm_studio_base_url}. Eval numbers only "
            "mean anything measured on the host against the real model."
        )
    return settings.chat_model


@pytest.fixture(scope="session", autouse=True)
def _record(_model_available: str) -> object:
    yield
    if not _RESULTS:
        return
    path = runner.record(_RESULTS, model=_model_available, dirty=runner.working_tree_dirty())
    passing = sum(1 for r in _RESULTS if r.ok)
    runs_passed = sum(r.passed for r in _RESULTS)
    runs = sum(r.runs for r in _RESULTS)
    print(
        f"\n\n  {passing}/{len(_RESULTS)} cases passing, "
        f"{runs_passed}/{runs} runs ({runs_passed / runs:.0%})"
        f"\n  recorded to {path.relative_to(path.parents[2])}\n"
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case(case: Case) -> None:
    result = runner.run(case, TODAY, RUNS)
    _RESULTS.append(result)

    if not result.ok:
        pytest.fail(
            f"{case.id} [{case.kind}] {result.passed}/{result.runs} "
            f"(needs {result.required})\n  " + "\n  ".join(result.detail)
        )
