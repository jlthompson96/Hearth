"""What an eval run records about the requests it measured.

A pass rate belongs to a model, and also to exactly what the model was sent. A
prompt can change without a file under prompts/ changing: pydantic sends an
enum's docstring inside the JSON schema, so editing a docstring once changed a
measured routing prompt, and a langchain-openai release could reshape the
request body without any file in this repository changing at all. The
fingerprint hashes the request body the client itself builds, so any of those
shows up as a different hash beside the numbers.

No model is called: the bodies are built by langchain-openai's own payload
builder and never sent.
"""

import datetime as dt
from collections.abc import Iterator

import pytest

from agents import tally
from agents.loop import DETAILS, load_prompt
from config import get_model_settings
from evals import fingerprint
from steward import router
from tools import bindings

TODAY = dt.date(2026, 9, 21)


@pytest.fixture(autouse=True)
def _model_named(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A model name, so a request body can be built on a machine with no .env."""
    monkeypatch.setenv("CHAT_MODEL", "a-model")
    monkeypatch.setenv("EMBEDDING_MODEL", "an-embedding-model")
    get_model_settings.cache_clear()
    yield
    get_model_settings.cache_clear()


def _changed(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {name for name in before if before[name] != after[name]}


def test_every_request_the_evals_measure_is_hashed() -> None:
    hashes = fingerprint.prompt_hashes(TODAY)

    assert set(hashes) == {
        "steward",
        "steward+errand",
        *(f"{agent}/{level}" for agent in ("tally", "forge") for level in DETAILS),
    }
    # Distinct requests, distinct hashes.
    assert len(set(hashes.values())) == len(hashes)


def test_the_same_code_hashes_the_same() -> None:
    assert fingerprint.prompt_hashes(TODAY) == fingerprint.prompt_hashes(TODAY)


def test_a_prompt_edit_changes_only_that_specialist_s_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint.prompt_hashes(TODAY)

    def edited(name: str) -> str:
        return load_prompt(name) + ("\nOne more sentence." if name == "tally" else "")

    monkeypatch.setattr(tally, "load_prompt", edited)

    assert _changed(before, fingerprint.prompt_hashes(TODAY)) == {
        f"tally/{level}" for level in DETAILS
    }


def test_a_tool_description_is_part_of_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool descriptions are prompt text the model reads (rule 11)."""
    before = fingerprint.prompt_hashes(TODAY)

    monkeypatch.setattr(bindings.allocation, "description", "Holdings on one date.")

    assert _changed(before, fingerprint.prompt_hashes(TODAY)) == {
        f"tally/{level}" for level in DETAILS
    }


def test_a_router_setting_in_code_changes_the_router_s_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kind of change no file under prompts/ shows: how much the router
    reasons is a constant in steward/router.py."""
    before = fingerprint.prompt_hashes(TODAY)

    monkeypatch.setattr(router, "REASONING_EFFORT", "low")

    assert _changed(before, fingerprint.prompt_hashes(TODAY)) == {"steward", "steward+errand"}


@pytest.mark.parametrize("with_errand", [False, True])
def test_the_schema_docstring_is_inside_what_is_hashed(with_errand: bool) -> None:
    """The incident this exists for: pydantic sends an enum's docstring inside
    the JSON schema, so editing it changed a measured prompt. It is fixed when
    the class is built, so the test is that it is in the body at all â€” edited
    in the source, the next run's hash differs."""
    import json

    from modellog import request_body

    model, asked = router.routing_request(fingerprint.PLACEHOLDER, None, with_errand=with_errand)

    assert "is a destination rather than a failure mode" in json.dumps(request_body(model, asked))


def test_the_model_s_name_is_recorded_apart_from_the_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The result file names the model already. Kept out of the hash, the same
    prompts against another model hash the same â€” which is what they are."""
    before = fingerprint.prompt_hashes(TODAY)

    monkeypatch.setenv("CHAT_MODEL", "another-model")
    get_model_settings.cache_clear()

    assert fingerprint.prompt_hashes(TODAY) == before


def test_the_packages_that_build_the_request_are_recorded() -> None:
    versions = fingerprint.package_versions()

    assert set(versions) == set(fingerprint.PACKAGES)
    assert all(versions.values())
