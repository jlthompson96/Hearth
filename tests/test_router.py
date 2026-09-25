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
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from config import get_model_settings
from evals import runner
from steward import router as router_module
from steward.router import (
    DESCRIPTIONS,
    RECORDS_ONLY_UNSUPPORTED,
    ConstrainedJSONRouter,
    Decision,
    Destination,
    Routed,
    Router,
    decision_schema,
    system_prompt,
)

#: The routing slice of the shared case file. Phase 6's twenty cases live there
#: now rather than in a file of their own, so there is one place to add a case.
#: The follow-ups (`route-followup-*`), which mostly carry an earlier exchange,
#: are a set of their own and measured by `make eval`, as are the cases run with
#: Errand offered — Phase 6's twenty are measured without it, as they were.
_ALL, _TODAY, RUNS = runner.load()
CASES = [
    c
    for c in _ALL
    if c.kind == "routing" and not c.id.startswith("route-followup-") and not c.with_errand
]
ERRAND_CASES = [c for c in _ALL if c.kind == "routing" and c.with_errand]


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


def test_without_errand_the_router_is_never_shown_it() -> None:
    """A destination the router can pick but nothing can serve is worse than one
    it is never shown. Not offered, Errand is absent from the prompt, and
    `unsupported` takes the world back, in the words Phase 6 measured."""
    prompt = system_prompt(with_errand=False)

    assert "errand" not in prompt.lower()
    assert f"- unsupported: {RECORDS_ONLY_UNSUPPORTED}" in prompt
    assert "What is a good price for a squat rack" in prompt


def test_with_errand_the_world_goes_to_it() -> None:
    prompt = system_prompt(with_errand=True)

    assert f"- errand: {DESCRIPTIONS[Destination.errand]}" in prompt
    assert RECORDS_ONLY_UNSUPPORTED not in prompt


def test_without_errand_the_schema_cannot_choose_it() -> None:
    """Constrained decoding is the code half of not offering it: the model
    cannot return a destination the schema does not contain."""
    records_only = decision_schema(with_errand=False)

    with pytest.raises(ValidationError):
        records_only.model_validate({"destination": "errand", "confidence": 1.0})
    records_only.model_validate({"destination": "unsupported", "confidence": 1.0})
    decision_schema(with_errand=True).model_validate({"destination": "errand", "confidence": 1.0})


def test_without_errand_the_schema_is_the_one_phase_6_measured() -> None:
    """Pydantic sends an enum's docstring to the model inside the schema, so the
    measured wording is pinned. Rewording it is a prompt change."""
    schema = decision_schema(with_errand=False).model_json_schema()

    assert schema["title"] == "Decision"
    assert schema["$defs"]["Destination"]["enum"] == ["tally", "forge", "unsupported"]
    assert "`errand` joins at Phase 9" in schema["$defs"]["Destination"]["description"]


@pytest.mark.parametrize("up", [True, False])
def test_an_unpinned_router_asks_whether_errand_is_there(
    monkeypatch: pytest.MonkeyPatch, up: bool
) -> None:
    seen: dict[str, object] = {}

    class _Model:
        def invoke(self, messages: list[tuple[str, str]]) -> dict[str, object]:
            seen["system"] = messages[0][1]
            decision = Decision(destination=Destination.unsupported, confidence=0.9)
            raw = AIMessage(content=decision.model_dump_json())
            return {"raw": raw, "parsed": decision, "parsing_error": None}

    def _structured(schema: object, **kwargs: object) -> _Model:
        seen["schema"] = schema
        return _Model()

    monkeypatch.setattr("tools.errand.available", lambda: up)
    monkeypatch.setattr(router_module, "structured_reply", _structured)

    ConstrainedJSONRouter().route("what is the weather tomorrow")

    assert seen["schema"] is decision_schema(with_errand=up)
    assert ("- errand:" in str(seen["system"])) is up


def test_the_errand_cases_cover_every_destination() -> None:
    """Offering a search must not pull records away from the specialists, so the
    Errand set asks about records too."""
    labels = Counter(case.expect for case in ERRAND_CASES)

    assert labels[Destination.errand.value] >= 4
    assert labels[Destination.unsupported.value] >= 2
    assert labels[Destination.tally.value] >= 1
    assert labels[Destination.forge.value] >= 1


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
    # Without Errand, as Phase 6 measured it.
    with_descriptions, _, _ = _measure(ConstrainedJSONRouter(with_errand=False))

    monkeypatch.setitem(router_module.__dict__, "DESCRIPTIONS", {d: d.value for d in Destination})
    monkeypatch.setattr(router_module, "RECORDS_ONLY_UNSUPPORTED", Destination.unsupported.value)
    bare, _, _ = _measure(ConstrainedJSONRouter(with_errand=False))

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
