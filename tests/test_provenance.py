"""The prompt hash a stored answer carries.

It has to be three things at once: the same every day for the same prompt,
different whenever what the specialist is told changes, and equal to the hash
an eval run records for that prompt — or a stored answer could not be matched
to the runs that measured it.
"""

import datetime as dt
from collections.abc import Iterator

import pytest

from agents import provenance, tally
from agents.loop import DETAILS, load_prompt
from config import get_model_settings
from evals import fingerprint
from modellog import digest, request_body


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A model name, so a request can be built anywhere; and no hash left over
    from another test's prompt."""
    monkeypatch.setenv("CHAT_MODEL", "a-model")
    monkeypatch.setenv("EMBEDDING_MODEL", "an-embedding-model")
    get_model_settings.cache_clear()
    provenance.answer_hash.cache_clear()
    yield
    provenance.answer_hash.cache_clear()
    get_model_settings.cache_clear()


def _hash_on(day: dt.date) -> str:
    model, conversation = provenance.specialist_request("tally", "normal", day)
    return digest(request_body(model, conversation, stream=True))


def test_the_day_is_pinned_so_the_hash_does_not_move_daily() -> None:
    """The prompt states today's date; hashed on the day, the same prompt would
    have a new hash every morning."""
    assert _hash_on(dt.date(2026, 10, 7)) != _hash_on(dt.date(2026, 10, 8))
    assert provenance.answer_hash("tally", "normal") == _hash_on(provenance.HASH_DAY)


def test_every_specialist_and_level_has_its_own() -> None:
    hashes = provenance.answer_hashes()

    assert set(hashes) == {f"{name}/{level}" for name in ("tally", "forge") for level in DETAILS}
    assert len(set(hashes.values())) == len(hashes)


def test_a_prompt_edit_changes_the_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    before = provenance.answer_hash("tally", "normal")

    def edited(name: str) -> str:
        return load_prompt(name) + ("\nOne more sentence." if name == "tally" else "")

    monkeypatch.setattr(tally, "load_prompt", edited)
    provenance.answer_hash.cache_clear()

    assert provenance.answer_hash("tally", "normal") != before


def test_an_eval_run_on_the_same_day_records_the_same_hash() -> None:
    """The fingerprint and the stored answer build the request with the same
    function; on the same day they must agree, or the lookup is meaningless."""
    measured = fingerprint.prompt_hashes(provenance.HASH_DAY)

    for name in ("tally", "forge"):
        for level in DETAILS:
            assert measured[f"{name}/{level}"] == provenance.answer_hash(name, level)


def test_the_model_s_name_is_not_part_of_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer stores the model beside the hash; the same prompt sent to
    another model is the same prompt."""
    before = provenance.answer_hash("forge", "brief")

    monkeypatch.setenv("CHAT_MODEL", "another-model")
    get_model_settings.cache_clear()
    provenance.answer_hash.cache_clear()

    assert provenance.answer_hash("forge", "brief") == before
