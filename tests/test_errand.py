from collections.abc import Callable
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import tools.errand as errand


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

    monkeypatch.setattr(errand, "get_settings", lambda: SimpleNamespace(searxng_url="http://search"))
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
    monkeypatch.setattr(errand, "get_settings", lambda: SimpleNamespace(searxng_url="http://search"))
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    with pytest.raises(httpx.HTTPStatusError), _client(
        lambda request: httpx.Response(502, request=request)
    ) as client:
        errand.search("local transit news", client=client)

    assert audit == [
        {"query": "local transit news", "allowed": True, "thread_id": None}
    ]


def test_malformed_results_are_rejected_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    audit: list[dict[str, object]] = []
    monkeypatch.setattr(errand, "get_settings", lambda: SimpleNamespace(searxng_url="http://search"))
    monkeypatch.setattr(errand, "_audit", lambda **values: audit.append(values))

    with pytest.raises(ValueError, match="results must be a list"), _client(
        lambda request: httpx.Response(200, json={"results": {}})
    ) as client:
        errand.search("local transit news", client=client)

    assert audit == [{"query": "local transit news", "allowed": True, "thread_id": None}]