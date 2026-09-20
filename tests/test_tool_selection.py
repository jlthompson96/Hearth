"""Can this model be trusted to pick a tool? A measurement, not a gate.

Phase 4's commit rejected tool calling for the router, on the grounds that LM
Studio parses tool calls out of model text against a chat template and that this
is unreliable enough at 8B to be load-bearing risk. Tally faces the same
question with three tools instead of a routing decision, and the model that
actually landed is 4B rather than 8B — and it routed a net-worth question to the
wrong specialist 30 times out of 30 during the Phase 4 check.

So this measures before Tally is built on the assumption. It records three
numbers, because they fail independently and the fix for each is different:

  - **emitted**   — did a tool call come back at all, or did the model answer in
                    prose? A miss here is the chat-template risk, and no prompt
                    fixes it.
  - **correct**   — was it the right tool? A miss here is a description problem.
  - **dates**     — were the arguments parseable as ISO dates? A miss here is
                    the model doing date arithmetic badly, and it argues for
                    resolving the period in code before the model sees it.

Local models are nondeterministic, so every case runs `RUNS` times and the
result is a rate (CLAUDE.md, Evals). Nothing here asserts a quality bar: the
point is to produce a number to decide on, and a threshold invented before the
first measurement would only be a guess wearing a test's clothes.
"""

import datetime as dt
from dataclasses import dataclass

import httpx
import pytest
from pydantic import ValidationError

from config import get_model_settings
from llm import chat_model
from tools.bindings import TALLY_TOOLS, schema_cost

#: Three runs per case is the floor the project sets for a local model.
RUNS = 3

#: A fixed date, so the measurement does not drift with the calendar and a rerun
#: next month is comparable to this one.
TODAY = dt.date(2024, 9, 20)


@dataclass(frozen=True)
class Case:
    question: str
    expected: str
    needs_dates: bool


CASES = [
    Case("How has my net worth moved this year?", "net_worth_trend", True),
    Case("What is my total wealth doing over the last six months?", "net_worth_trend", True),
    Case("Show me the balance of my Emergency Savings account this year.", "balance_history", True),
    Case("How did my Brokerage account do between March and June?", "balance_history", True),
    Case("What am I invested in right now?", "allocation", False),
    Case("Break down my holdings by symbol as of today.", "allocation", False),
]

SYSTEM = (
    f"Today is {TODAY.isoformat()}. You answer questions about the user's own "
    "finances by calling exactly one tool. Never answer from memory and never "
    "do arithmetic yourself. All dates are ISO yyyy-mm-dd."
)


@pytest.fixture(scope="session")
def _endpoint() -> str:
    try:
        settings = get_model_settings()
    except ValidationError:
        pytest.skip("CHAT_MODEL is unset — see .env.example")
    try:
        httpx.get(f"{settings.lm_studio_base_url}/models", timeout=3.0)
    except httpx.HTTPError:
        pytest.skip(f"no model server at {settings.lm_studio_base_url}")
    return settings.lm_studio_base_url


@pytest.mark.model
def test_measure_tool_selection(_endpoint: str) -> None:
    model = chat_model().bind_tools(TALLY_TOOLS)

    emitted = correct = dated = date_attempts = 0
    total = len(CASES) * RUNS
    rows: list[str] = []

    for case in CASES:
        got: list[str] = []
        for _ in range(RUNS):
            response = model.invoke([("system", SYSTEM), ("human", case.question)])
            calls = getattr(response, "tool_calls", []) or []
            if not calls:
                got.append("—")
                continue
            emitted += 1
            name = calls[0]["name"]
            got.append(name if name != case.expected else "ok")
            if name == case.expected:
                correct += 1
            if case.needs_dates:
                date_attempts += 1
                args = calls[0].get("args", {})
                try:
                    for key in ("start", "end"):
                        if key in args:
                            dt.date.fromisoformat(str(args[key]))
                    dated += 1
                except (ValueError, TypeError):
                    pass
        rows.append(f"  {case.expected:<17} {', '.join(got):<34} {case.question[:44]}")

    print(f"\n\n  tool schemas: ~{schema_cost(TALLY_TOOLS)} tokens\n")
    print("\n".join(rows))
    print(
        f"\n  emitted a tool call : {emitted}/{total}"
        f"\n  correct tool        : {correct}/{total}"
        f"\n  parseable dates     : {dated}/{date_attempts}\n"
    )

    # The only hard assertion: the measurement actually ran. Everything above is
    # evidence for a design decision, and turning it into a pass/fail bar before
    # anyone has seen the number would be inventing the answer.
    assert total > 0
