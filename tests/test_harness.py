"""The harness's own promise: a missing database is never a quiet green.

On a laptop with no Postgres running, the database tests skip, loudly, so the
rest of the suite is still useful. In CI that same skip would turn a broken
setup into a passing run that tested nothing, so CI sets HEARTH_REQUIRE_DB and
the skip becomes a failure.
"""

import pytest

from tests.conftest import _unavailable


def test_a_missing_database_skips_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEARTH_REQUIRE_DB", raising=False)

    with pytest.raises(pytest.skip.Exception, match="Postgres unreachable"):
        _unavailable("Postgres unreachable")


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_a_missing_database_fails_when_one_is_required(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("HEARTH_REQUIRE_DB", value)

    with pytest.raises(pytest.fail.Exception, match="Postgres unreachable"):
        _unavailable("Postgres unreachable")


def test_zero_does_not_require_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_REQUIRE_DB", "0")

    with pytest.raises(pytest.skip.Exception):
        _unavailable("Postgres unreachable")
