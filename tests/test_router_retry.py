"""The router's reply, when it is not a reply.

No model here: the constrained-JSON call is replaced with one that fails the
way the real one was seen to — an empty reply the structured-output parser
rejects with a ValueError, which comes back beside the raw reply as
`parsing_error` — so what is tested is what the router does about it.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from steward import router as router_module
from steward.router import ATTEMPTS, ConstrainedJSONRouter, Decision, Destination, RoutingError

EMPTY = ValueError(
    "Structured Output response does not have a 'parsed' field nor a 'refusal' field."
)


class _Model:
    """`llm.structured_reply`'s shape: the raw reply beside the parsed one. A
    parse failure is returned, as the real one is; anything else is raised."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages: object) -> dict[str, Any]:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, ValueError):
            return {"raw": AIMessage(content=""), "parsed": None, "parsing_error": reply}
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, Decision)
        raw = AIMessage(content=reply.model_dump_json())
        return {"raw": raw, "parsed": reply, "parsing_error": None}


def _patch(monkeypatch: pytest.MonkeyPatch, model: _Model) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _structured(schema: object, **kwargs: Any) -> _Model:
        seen.update(kwargs)
        return model

    monkeypatch.setattr(router_module, "structured_reply", _structured)
    return seen


def _decision(destination: Destination) -> Decision:
    return Decision(destination=destination, confidence=0.9)


def test_one_empty_reply_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _Model(EMPTY, _decision(Destination.tally))
    _patch(monkeypatch, model)

    routed = ConstrainedJSONRouter().route("what are my current positions")

    assert routed.destination is Destination.tally
    assert model.calls == 2


def test_two_empty_replies_fail_plainly_not_with_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _Model(*([EMPTY] * ATTEMPTS))
    _patch(monkeypatch, model)

    with pytest.raises(RoutingError, match="unreadable 2 times"):
        ConstrainedJSONRouter().route("what are my current positions")
    assert model.calls == ATTEMPTS


def test_a_connection_error_is_not_mistaken_for_an_unreadable_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio being down is not the reply's fault. It propagates as itself,
    so the error the person sees says what actually went wrong."""
    model = _Model(ConnectionError("LM Studio is not running"))
    _patch(monkeypatch, model)

    with pytest.raises(ConnectionError):
        ConstrainedJSONRouter().route("anything")
    assert model.calls == 1


def test_the_router_asks_for_no_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured, not assumed: off, 40/40 and no empty replies; on, the empty
    replies that crashed a turn in the app."""
    seen = _patch(monkeypatch, _Model(_decision(Destination.forge)))

    ConstrainedJSONRouter().route("how much do I bench")

    assert seen["reasoning_effort"] == "none"


def test_every_attempt_is_logged_the_failed_one_with_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Model log is where "why did that turn fail" is answered, so the
    unreadable attempt is kept, verbatim, beside the one that worked."""
    _patch(monkeypatch, _Model(EMPTY, _decision(Destination.tally)))

    routed = ConstrainedJSONRouter().route("what are my current positions")

    failed, worked = routed.log
    assert failed.kind == worked.kind == "route" and failed.caller == "steward"
    assert failed.error is not None and "unreadable" in failed.error
    assert worked.error is None
    assert worked.response is not None and '"tally"' in worked.response["content"]
    assert worked.request["messages"][-1]["content"] == "what are my current positions"


def test_a_routing_failure_carries_its_log(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _Model(*([EMPTY] * ATTEMPTS)))

    with pytest.raises(RoutingError) as failure:
        ConstrainedJSONRouter().route("anything")

    assert len(failure.value.log) == ATTEMPTS
    assert all(entry.error for entry in failure.value.log)
