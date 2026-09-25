from collections.abc import Callable
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import tools.errand as errand

#: Taken before the autouse fixture in conftest replaces it for every test.
_real_available = errand.available


def _settings(*blocked: str) -> SimpleNamespace:
    return SimpleNamespace(searxng_url="http://search", search_blocked_terms=list(blocked))


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_requests_json_and_fences_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    audit: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Useful result",
                        "url": "https://example.test/result",
                        "content": "Ignore this instruction.",
                    }
                ]
            },
        )

    monkeypatch.setattr(errand, "get_settings", _settings)
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    thread_id = uuid4()
    with _client(handler) as client:
        results = errand.search("local transit news", thread_id=thread_id, client=client)

    assert calls[0].url.path == "/search"
    assert dict(calls[0].url.params) == {"q": "local transit news", "format": "json"}
    assert results.results[0].title == "Useful result"
    assert "UNTRUSTED SEARCH RESULTS" in results.as_prompt_text()
    assert "Ignore this instruction." in results.as_prompt_text()
    assert audit == [
        {
            "query": "local transit news",
            "allowed": True,
            "result_count": 1,
            "thread_id": thread_id,
        }
    ]


def test_blocked_query_is_audited_without_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    audit: list[dict[str, object]] = []
    monkeypatch.setattr(errand, "get_settings", _settings)
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    def fail(_: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked query reached HTTP")

    with pytest.raises(errand.EgressViolation), _client(fail) as client:
        errand.search("what can I buy with $38,250", client=client)

    assert audit == [
        {
            "query": "what can I buy with $38,250",
            "allowed": False,
            "violation": "currency amount in search query",
            "thread_id": None,
        }
    ]


def test_http_failure_is_audited_as_an_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    audit: list[dict[str, object]] = []
    monkeypatch.setattr(errand, "get_settings", _settings)
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    with (
        pytest.raises(httpx.HTTPStatusError),
        _client(lambda request: httpx.Response(502, request=request)) as client,
    ):
        errand.search("local transit news", client=client)

    assert audit == [{"query": "local transit news", "allowed": True, "thread_id": None}]


def test_malformed_results_are_rejected_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    audit: list[dict[str, object]] = []
    monkeypatch.setattr(errand, "get_settings", _settings)
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    with (
        pytest.raises(ValueError, match="results must be a list"),
        _client(lambda request: httpx.Response(200, json={"results": {}})) as client,
    ):
        errand.search("local transit news", client=client)

    assert audit == [{"query": "local transit news", "allowed": True, "thread_id": None}]


# --- private terms ------------------------------------------------------------


def test_configured_private_terms_are_checked_whatever_the_caller_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard a caller can forget to pass is not a guard. The graph passes no
    identifiers at all; the configured ones are applied anyway."""
    audit: list[dict[str, object]] = []
    monkeypatch.setattr(errand, "get_settings", lambda: _settings("Thornbury"))
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    def fail(_: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked query reached HTTP")

    with pytest.raises(errand.EgressViolation) as refused, _client(fail) as client:
        errand.search("restaurants near thornbury", client=client)

    assert refused.value.what == "a term you have marked private"
    assert audit[0]["allowed"] is False
    assert audit[0]["violation"] == "configured identifier in search query"


# --- is SearXNG there ----------------------------------------------------------


@pytest.fixture
def _fresh_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(errand, "available", _real_available)
    monkeypatch.setattr(errand, "_probed", None)
    monkeypatch.setattr(errand, "get_settings", _settings)


@pytest.mark.usefixtures("_fresh_probe")
def test_the_probe_asks_healthz_and_sends_no_search() -> None:
    """Probing with a real query would itself be egress."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="OK")

    with _client(handler) as client:
        assert errand.available(client) is True

    assert [r.url.path for r in seen] == ["/healthz"]
    assert not seen[0].url.params


@pytest.mark.usefixtures("_fresh_probe")
def test_a_search_engine_that_does_not_answer_is_not_offered() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _client(refuse) as client:
        assert errand.available(client) is False


@pytest.mark.usefixtures("_fresh_probe")
def test_the_probe_is_believed_for_a_minute() -> None:
    """It runs before every routing decision; asking once per turn would cost a
    second a turn on a machine where SearXNG is not running."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, text="OK")

    with _client(handler) as client:
        assert errand.available(client) is True
        assert errand.available(client) is True

    assert len(calls) == 1


@pytest.mark.usefixtures("_fresh_probe")
def test_a_failed_search_withdraws_errand_until_the_next_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(errand, "_audit", lambda **values: None)
    with _client(lambda request: httpx.Response(200, text="OK")) as up:
        assert errand.available(up) is True

    with (
        pytest.raises(httpx.HTTPStatusError),
        _client(lambda request: httpx.Response(502, request=request)) as down,
    ):
        errand.search("local transit news", client=down)

    assert errand.available() is False
