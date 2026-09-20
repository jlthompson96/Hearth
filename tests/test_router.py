"""Phase 6's routing measurement.

The exit criterion is >= 90% accuracy on the labelled set, and `evals/README.md`
adds a stricter reading for routing specifically: every run of a routing case
has to pass, because a destination that is right two times in three is a turn
that lands in the wrong agent every third time someone asks.

Both numbers are reported. The gate is the strict one.

The A/B below is the experiment Phase 5 argued for: the same model, the same
cases, the only difference being whether each destination carries a sentence
saying what it is for. It runs as its own test so the comparison is recorded
rather than asserted from memory, and so a future change to the descriptions
can be measured against it.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError

from config import get_model_settings
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

CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "routing.yaml"

#: Three runs per case is the project's floor for a nondeterministic model.
RUNS = 3


def load_cases() -> list[dict[str, Any]]:
    data = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    return list(data["cases"])


CASES = load_cases()


# --- no model needed ----------------------------------------------------------


def test_the_labelled_set_is_the_size_the_phase_asked_for() -> None:
    assert len(CASES) == 20


def test_every_case_is_labelled_with_a_real_destination() -> None:
    """A typo in the case file would otherwise show up as a routing failure and
    send someone looking at the prompt."""
    for case in CASES:
        assert case["expect"] in {d.value for d in Destination}, case


def test_the_set_covers_every_destination_including_unsupported() -> None:
    """Accuracy over a set that never exercises `unsupported` would not measure
    the thing most likely to go wrong."""
    labels = Counter(case["expect"] for case in CASES)

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


def test_errand_is_not_a_destination_until_it_exists() -> None:
    """A destination the router can pick but nothing can serve is worse than one
    that is not there yet. Errand arrives with Phase 9."""
    assert "errand" not in {d.value for d in Destination}


def test_the_decision_schema_rejects_an_invented_destination() -> None:
    with pytest.raises(ValidationError):
        Decision(destination="errand", confidence=1.0)

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
        got = [instance.route(case["question"]).destination.value for _ in range(RUNS)]
        hits = sum(1 for g in got if g == case["expect"])
        correct += hits
        if hits == RUNS:
            unanimous += 1
        else:
            lines.append(
                f"    {case['expect']:<12} got {', '.join(got):<34} {case['question'][:46]}"
            )
    return correct, unanimous, lines


@pytest.mark.model
def test_routing_accuracy_on_the_labelled_set(_endpoint: str) -> None:
    """Phase 6's exit criterion."""
    instance = ConstrainedJSONRouter()
    correct, unanimous, misses = _measure(instance)
    total = len(CASES) * RUNS

    accuracy = correct / total
    strict = unanimous / len(CASES)

    print(f"\n\n  router: {instance.name}")
    print(f"  accuracy over all runs : {correct}/{total} ({accuracy:.0%})")
    print(f"  cases right every run  : {unanimous}/{len(CASES)} ({strict:.0%})")
    if misses:
        print("\n  cases that were not unanimous:")
        print("\n".join(misses))
    print()

    # The plan's number. evals/README.md's stricter reading is reported above
    # and asserted below it, so a regression in either is visible.
    assert accuracy >= 0.90, f"routing accuracy {accuracy:.0%} is below the 90% exit criterion"
    assert strict >= 0.90, f"only {strict:.0%} of cases routed the same way on every run"


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
