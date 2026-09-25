import pytest

from tools.errand import EgressViolation, validate_search_query


@pytest.mark.parametrize(
    "query",
    [
        "latest local transit news",
        "best squat rack for a small gym",
        "how to compare mortgage rates",
    ],
)
def test_ordinary_search_queries_are_allowed(query: str) -> None:
    validate_search_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "what can I buy with $38,250",
        "what can I buy with $10000",
        "compare returns on 10000 dollars",
        "account ending in 1234",
    ],
)
def test_private_values_are_rejected(query: str) -> None:
    with pytest.raises(EgressViolation):
        validate_search_query(query)


def test_configured_identifiers_are_rejected() -> None:
    with pytest.raises(EgressViolation, match="configured identifier"):
        validate_search_query("look up the household vault", identifiers=("vault",))


def test_empty_identifiers_are_ignored() -> None:
    validate_search_query("ordinary search", identifiers=("",))


def test_identifiers_match_whole_words_only() -> None:
    """A filter that refuses ordinary searches gets switched off by the person
    it protects."""
    validate_search_query("pole vaulting results", identifiers=("vault",))
    with pytest.raises(EgressViolation):
        validate_search_query("Vault opening hours", identifiers=("vault",))


@pytest.mark.parametrize(
    ("query", "what"),
    [
        ("what can I buy with $38,250", "an amount of money"),
        ("compare returns on 10000 dollars", "a number four or more digits long"),
    ],
)
def test_a_violation_says_what_kind_of_thing_without_the_value(query: str, what: str) -> None:
    with pytest.raises(EgressViolation) as refused:
        validate_search_query(query)

    assert refused.value.what == what
    assert "38" not in refused.value.what and "10000" not in refused.value.what
