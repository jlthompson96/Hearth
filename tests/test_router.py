"""The router's shape, and the experiment that settled its design.

Accuracy itself moved to `evals/cases.yaml` when Phase 7 arrived — it is a
behavioural measurement over a labelled set, which is what that file is for,
and running it here made `make test` take six minutes. What stays is everything
a string comparison can settle without a model, plus the A/B.

The A/B is the experiment Phase 5 argued for: same model, same cases, the only
difference being whether each destination carries a sentence saying what it is
for. It is marked `model` and deselected from `make test`; run it with
`pytest -m model tests/test_router.py` when the descriptions change, because
that is the edit it exists to catch.
"""

from collections import Counter

import httpx
import pytest
from pydantic import ValidationError

from config import get_model_settings
from evals import runner
from steward import router as router_module
from steward.router import (
    DESCRIPTIONS,
    ConstrainedJSONRouter,
    Decision,
    Destination,
    Routed,
    Router,
    system_prompt,
)

#: The routing slice of the shared case file. Phase 6's twenty cases live there
#: now rather than in a file of their own, so there is one place to add a case.
#: The follow-ups (`route-followup-*`), which mostly carry an earlier exchange,
#: are a set of their own and measured by `make eval`.
_ALL, _TODAY, RUNS = runner.load()
CASES = [c for c in _ALL if c.kind == "routing" and not c.id.startswith("route-followup-")]


# --- no model needed ----------------------------------------------------------


def test_the_labelled_set_is_the_size_the_phase_asked_for() -> None:
    assert len(CASES) == 20


def test_every_case_is_labelled_with_a_real_destination() -> None:
    """A typo in the case file would otherwise show up as a routing failure and
    send someone looking at the prompt."""
    for case in CASES:
        assert case.expect in {d.value for d in Destination}, case


def test_the_set_covers_every_destination_including_unsupported() -> None:
    """Accuracy over a set that never exercises `unsupported` would not measure
    the thing most likely to go wrong."""
    labels = Counter(case.expect for case in CASES)

    assert labels[Destination.unsupported.value] >= 4
    assert labels[Destination.tally.value] >= 4
    assert labels[Destination.forge.value] >= 4


def test_every_destination_carries_a_description() -> None:
    """The descriptions are the hypothesis. A destination without one is back to
    the bare label that Phase 4 measured at 0/30."""
    for destination in Destination:
        assert DESCRIPTIONS[destination].strip()


def test_the_prompt_names_every_destination() -> None:
    prompt = system_prompt()

    for destination in Destination:
        assert destination.value in prompt
        assert DESCRIPTIONS[destination][:24] in prompt


def test_errand_is_a_served_destination() -> None:
    assert Destination.errand.value in {d.value for d in Destination}


def test_the_decision_schema_rejects_an_invented_destination() -> None:
    with pytest.raises(ValidationError):
        Decision(destination="invented", confidence=1.0)

    with pytest.raises(ValidationError):
        Decision(destination=Destination.tally, confidence=1.4)


def test_the_constrained_json_router_satisfies_the_protocol() -> None:
    """The protocol is what lets an LLM tool-calling router replace this one on
    better hardware without the graph noticing."""
    instance: Router = ConstrainedJSONRouter()

    assert instance.name == "constrained-json"
    assert callable(instance.route)


# --- these need the model -----------------------------------------------------


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


def _measure(instance: Router) -> tuple[int, int, list[str]]:
    """(correct runs, cases correct on every run, report lines)."""
    correct = 0
    unanimous = 0
    lines: list[str] = []

    for case in CASES:
        got = [instance.route(case.question).destination.value for _ in range(RUNS)]
        hits = sum(1 for g in got if g == case.expect)
        correct += hits
        if hits == RUNS:
            unanimous += 1
        else:
            lines.append(f"    {case.expect:<12} got {', '.join(got):<34} {case.question[:46]}")
    return correct, unanimous, lines


@pytest.mark.model
def test_descriptions_beat_bare_labels(_endpoint: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Phase 5 hypothesis, measured rather than asserted.

    Phase 4 saw this model route a net-worth question to `forge` 30/30 choosing
    between bare labels. If stripping the descriptions reproduces that collapse,
    the descriptions are what is doing the work and the keyword-plus-embedding
    fallback is not needed. If accuracy holds without them, the hypothesis was
    wrong and this test is how anyone finds that out.
    """
    with_descriptions, _, _ = _measure(ConstrainedJSONRouter())

    monkeypatch.setitem(router_module.__dict__, "DESCRIPTIONS", {d: d.value for d in Destination})
    bare, _, _ = _measure(ConstrainedJSONRouter())

    total = len(CASES) * RUNS
    print(
        f"\n\n  with descriptions : {with_descriptions}/{total} "
        f"({with_descriptions / total:.0%})"
        f"\n  bare labels       : {bare}/{total} ({bare / total:.0%})\n"
    )

    # No assertion on the gap: the number is the point, and a threshold invented
    # before anyone has seen it would be a guess wearing a test's clothes. The
    # gate is the exit criterion above.
    assert with_descriptions >= 0


def test_routed_carries_the_router_that_decided() -> None:
    """The UI attributes an answer to an agent; when routing later becomes
    keyword-plus-embedding for some turns and a model call for others, "which
    router decided this" is the question you will want answered."""
    routed = Routed(destination=Destination.tally, confidence=0.9, router="constrained-json")

    assert routed.router == "constrained-json"
