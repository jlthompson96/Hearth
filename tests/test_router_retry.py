"""The router's reply, when it is not a reply.

No model here: the constrained-JSON call is replaced with one that fails the
way the real one was seen to — an empty reply the structured-output parser
rejects with a ValueError — so what is tested is what the router does about it.
"""

from typing import Any

import pytest

from steward import router as router_module
from steward.router import ATTEMPTS, ConstrainedJSONRouter, Decision, Destination, RoutingError

EMPTY = ValueError(
    "Structured Output response does not have a 'parsed' field nor a 'refusal' field."
)


class _Model:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages: object) -> Decision:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, Decision)
        return reply


def _patch(monkeypatch: pytest.MonkeyPatch, model: _Model) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _structured(schema: object, **kwargs: Any) -> _Model:
        seen.update(kwargs)
        return model

    monkeypatch.setattr(router_module, "structured_model", _structured)
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
