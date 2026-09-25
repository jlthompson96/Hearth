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