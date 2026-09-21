"""What the Model log keeps: every exchange with the model, and every tool run.

An entry is made where the call is made — the agent loop, the router, the title
call — and travels to the chat route as a `LogEvent` like everything else the
turn produces; the route stores it with the question it belongs to. Nothing is
global and nothing is written from inside a model call, so evals and probes,
which never store a turn, never write a log either.

The request is the body langchain-openai sends LM Studio, built by the same
code that sends it, not a paraphrase of it. The response is what came back as
LangChain received it: text, tool calls, finish reason and token counts. One
thing is missing and the screen says so: the model's reasoning. LM Studio
returns it as `reasoning_content`, which LangChain's OpenAI client drops, so a
log can say how many tokens were spent reasoning but not what they said.
"""

import datetime as dt
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import BaseMessage, convert_to_openai_messages
from pydantic import BaseModel

Kind = Literal["route", "step", "tool", "title"]


@dataclass(frozen=True)
class LogEntry:
    kind: Kind
    #: steward, tally, forge or titles.
    caller: str
    request: dict[str, Any]
    response: dict[str, Any] | None
    started_at: dt.datetime
    duration_ms: int
    model: str | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Clock:
    """When a call started, and how long it took once it stops."""

    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    _t0: float = field(default_factory=time.perf_counter)

    @property
    def ms(self) -> int:
        return round((time.perf_counter() - self._t0) * 1000)


def request_body(model: Any, messages: Sequence[Any], **extra: Any) -> dict[str, Any]:
    """The JSON body langchain-openai will POST for `messages` on `model`.

    `model` is what the caller invokes: a chat model with tools bound, or a
    structured-output chain whose first step is the bound model. The body comes
    from the client's own payload builder, so it is what is sent rather than a
    reconstruction; `extra` records what the call adds itself, such as
    `stream`. Anything that is not a LangChain OpenAI model — a test's fake —
    is logged as its messages alone.
    """
    binding = _binding(model)
    if binding is None:
        return {"messages": convert_to_openai_messages(list(messages)), **extra}
    kwargs = {k: v for k, v in binding.kwargs.items() if not k.startswith("ls_")}
    body: dict[str, Any] = binding.bound._get_request_payload(list(messages), **kwargs)
    schema = body.get("response_format")
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        # The client turns the class into this on the way out; the log shows
        # what went, not a Python class name.
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
    return {**body, **extra}


def _binding(runnable: Any) -> Any | None:
    """The chat model with its bound arguments, wherever the chain put it: the
    thing itself for `bind_tools`, the first step of a structured-output chain,
    or inside that step's map when the raw reply is kept beside the parsed one."""
    if hasattr(runnable, "kwargs") and hasattr(
        getattr(runnable, "bound", None), "_get_request_payload"
    ):
        return runnable
    first = getattr(runnable, "first", None)
    if first is not None:
        return _binding(first)
    for step in (getattr(runnable, "steps__", None) or {}).values():
        found = _binding(step)
        if found is not None:
            return found
    return None


def response_body(message: BaseMessage | None) -> dict[str, Any] | None:
    """What came back, as LangChain received it."""
    if message is None:
        return None
    usage = getattr(message, "usage_metadata", None)
    return {
        "content": message.text,
        "tool_calls": [
            {"name": c["name"], "args": c["args"], "id": c.get("id")}
            for c in getattr(message, "tool_calls", None) or []
        ],
        "finish_reason": message.response_metadata.get("finish_reason"),
        "model": message.response_metadata.get("model_name"),
        "usage": dict(usage) if usage else None,
    }


def tokens(message: BaseMessage | None) -> tuple[int | None, int | None]:
    usage = getattr(message, "usage_metadata", None) if message is not None else None
    if not usage:
        return None, None
    return usage.get("input_tokens"), usage.get("output_tokens")
