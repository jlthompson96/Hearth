"""The agent loop both specialists run.

Extracted when Forge arrived and would otherwise have been a second copy of
Tally's sixty lines. What differs between specialists is a prompt file and a
tool list; the loop that drives them is the same, and two copies of it would
have drifted by Phase 6.

An explicit loop rather than a prebuilt agent, for two reasons. The iteration
cap is a countable thing in code, which is what rule 7 asks for — a cap that
lives in a framework's defaults is a cap nobody can point at. And every message
that enters the context window is appended here, in the open, because the window
is 8,192 tokens and a few hundred are gone to tool schemas before the user types.

The loop streams each step. In the ordinary two-step shape — call a tool, read
the result, answer — the first step carries no text, so nothing provisional
reaches the UI. A model that narrated before calling a tool would briefly stream
that narration; a provisional `TokenEvent` then takes it back, so it is not left
on screen, or stored, as though it were the answer. An answer cut off at the
token limit is taken back the same way.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Literal, get_args

from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from agents.conversation import Exchange
from llm import chat_model
from modellog import Clock, LogEntry, request_body, response_body, tokens

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

#: Rule 7: the cap is a number in code, not a sentence in a prompt. Two steps is
#: the ordinary shape (call a tool, answer from it). Four leaves room for a
#: second lookup — a question needing a third is one this agent should give up
#: on rather than grind at, on a model this size and a window this small.
#:
#: The last step is sent with no tools bound, so it can only answer. Before
#: that, "which account grew the most?" spent all four steps fetching one
#: account at a time and the turn ended with nothing on screen, 3 runs in 3.
#: The cap still binds at the same number of model calls; it now ends in an
#: answer from what was fetched rather than in silence.
MAX_STEPS = 4

#: Rule 7 again, for what comes back rather than how often: the most tool
#: output, in characters, one turn may put in front of the model. Measured
#: against the window on 2026-09-21 (`agents.conversation`): a turn peaks near
#: 3,300 of 8,192 tokens with a small tool result, a follow-up adds up to
#: 1,750, and figures run about 2.3 characters to a token — so 6,000 characters,
#: about 2,600 tokens, is what fits beside the rest. A tool should summarise
#: long before this (`tools.fitness.MAX_GROUPS`); this is the backstop for one
#: that does not.
MAX_RESULT_CHARS = 6000

#: What the model reads in place of a result over the budget. Not the result
#: cut short: a list with its end missing reads as complete. A `caveat:` line,
#: so the prompts' rule to repeat such a line in full carries it to the answer.
TOO_LONG = (
    "caveat: There are too many records in that period to go through in one answer "
    "— a shorter period would work."
)

#: Rule 7 for a model that will not stop: the most a specialist's step may
#: generate, reasoning included — LM Studio counts reasoning against
#: `max_tokens`. Measured on the host on 2026-09-28 over the heaviest eval
#: questions, detailed answers included, 28 steps: at most 2,399 tokens, 90th
#: percentile 2,039, median 593. 2,000 — the first guess — would have cut off
#: one step in ten. 4,000 leaves room above the largest, and stops a model
#: caught repeating itself before it fills the window.
MAX_OUTPUT_TOKENS = 4000

#: And the longest a turn may run, in seconds, checked before each step: the
#: step cap bounds how many calls, this bounds how long. The same probe: turns
#: of at most 42.7s and 3 steps, the slowest step 32s — so four such steps, the
#: most MAX_STEPS allows, still finish. A step already under way finishes too,
#: bounded by MAX_OUTPUT_TOKENS and the client's timeout. Before this, a turn
#: stalled on every step could run four steps of two 120-second attempts each.
TURN_SECONDS = 180


@dataclass(frozen=True)
class TokenEvent:
    """Text of the answer as it streams.

    `provisional` takes text back: the `text` most recently streamed was not
    the answer after all — narration before a tool call, or an answer cut off
    at the token limit — and a consumer removes it (`take_back`). The text
    has already been shown by then, so skipping this event is not enough."""

    text: str
    provisional: bool = False


@dataclass(frozen=True)
class ToolEvent:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResultEvent:
    name: str
    result: str


@dataclass(frozen=True)
class RefusedEvent:
    """The turn stopped before inference. Distinct from a DoneEvent so the UI
    and the evals can both tell a refusal from an answer, and so Phase 7 can
    assert that the refusal path was taken rather than that some text came
    back that happened to read like one."""

    signal: str
    message: str


@dataclass(frozen=True)
class RoutedEvent:
    """Which specialist the Steward chose, and how sure it was.

    Emitted before the specialist runs so the UI can attribute an answer while
    it is still streaming, rather than labelling it after the fact. `router`
    names the implementation that decided, because routing will not always be
    one mechanism — a keyword rule and a model call should not be
    indistinguishable in a transcript.
    """

    destination: str
    confidence: float
    router: str


@dataclass(frozen=True)
class DoneEvent:
    reason: str = "complete"


@dataclass(frozen=True)
class LogEvent:
    """One entry for the Model log: a model call or a tool run, verbatim. Not
    streamed to the browser — the chat route stores it with the turn, and the
    Model log screen reads it back."""

    entry: LogEntry


Event = TokenEvent | ToolEvent | ToolResultEvent | RefusedEvent | RoutedEvent | DoneEvent | LogEvent


@lru_cache
def load_prompt(name: str) -> str:
    """Read once. Prompts are version-controlled files, never inline string
    literals, because Phase 7 measures the delta when one changes and a prompt
    you cannot diff is a prompt you cannot measure."""
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


#: How much a specialist says. One prompt fragment each, in prompts/detail/,
#: slotted into the specialist's own prompt: the voice is the specialist's, the
#: length is the person's choice — and every level keeps every rule.
Detail = Literal["brief", "normal", "detailed"]
DETAILS: tuple[Detail, ...] = get_args(Detail)


def detail_prompt(detail: Detail) -> str:
    if detail not in DETAILS:
        raise ValueError(f"unknown detail level {detail!r}; one of {DETAILS}")
    return load_prompt(f"detail/{detail}").strip()


def take_back(answer: str, text: str) -> str:
    """`answer` without `text`, when `text` is what it ends with — how every
    consumer applies a provisional `TokenEvent`."""
    return answer[: -len(text)] if text and answer.endswith(text) else answer


def bound(tools: list[BaseTool]) -> Any:
    """The chat model a specialist calls, capped and with its tools bound."""
    return chat_model(max_tokens=MAX_OUTPUT_TOKENS).bind_tools(tools)


def answer_only() -> Any:
    """The chat model with no tools, for a turn's last step. Bound to nothing
    rather than used bare, so the Model log still records the request body the
    client built rather than a reconstruction of it."""
    return chat_model().bind()


def opening(
    system: str, question: str, history: Sequence[Exchange] = ()
) -> list[tuple[str, str] | BaseMessage]:
    """What the model reads on a turn's first step: its prompt, the earlier
    exchanges it is shown, and the question. `run` starts from this, and
    `evals.fingerprint` hashes it, so the two cannot describe different
    requests."""
    conversation: list[tuple[str, str] | BaseMessage] = [("system", system)]
    for exchange in history:
        conversation += [("human", exchange.question), ("ai", exchange.answer)]
    conversation.append(("human", question))
    return conversation


def run(
    *,
    caller: str,
    system: str,
    question: str,
    tools: list[BaseTool],
    history: Sequence[Exchange] = (),
) -> Iterator[Event]:
    """Drive one turn to an answer, yielding events as they happen.

    `history` is the earlier exchanges this specialist is shown, already chosen
    by `conversation.window`. They go in as the questions and answers they were
    — not their tool results, which are the bulk of a turn and which the model
    is told to fetch again rather than remember.

    Every model call and tool run is also yielded as a `LogEvent`, named for
    `caller`, including a model call that failed — the failed one is the entry
    most worth reading.
    """
    with_tools = bound(tools)
    by_name = {t.name: t for t in tools}

    conversation = opening(system, question, history)
    #: Tool output passed to the model so far this turn, against MAX_RESULT_CHARS.
    carried = 0
    deadline = monotonic() + TURN_SECONDS

    for step in range(MAX_STEPS):
        if step and monotonic() > deadline:
            yield DoneEvent(f"stopped after {TURN_SECONDS} seconds without a final answer")
            return
        gathered: AIMessageChunk | None = None
        emitted: list[str] = []
        body = request_body(model, conversation, stream=True)
        clock = Clock()

        try:
            for chunk in model.stream(conversation):
                assert isinstance(chunk, AIMessageChunk)
                gathered = chunk if gathered is None else gathered + chunk
                if chunk.text:
                    emitted.append(chunk.text)
                    yield TokenEvent(chunk.text)
        except Exception as error:
            yield LogEvent(
                _step_entry(caller, body, clock, gathered, f"{type(error).__name__}: {error}")
            )
            raise
        yield LogEvent(_step_entry(caller, body, clock, gathered))

        if gathered is None:
            yield DoneEvent("the model returned nothing")
            return

        if gathered.response_metadata.get("finish_reason") == "length":
            # Cut off mid-answer, or mid-tool-call. Neither is usable, and a
            # truncated answer reads as a shorter one, so it is taken back.
            if emitted:
                yield TokenEvent("".join(emitted), provisional=True)
            yield DoneEvent(
                f"stopped at the {MAX_OUTPUT_TOKENS}-token limit before the answer was finished"
            )
            return

        calls = gathered.tool_calls or []
        if not calls:
            yield DoneEvent()
            return

        # Text streamed during a step that turned out to be a tool call is not
        # the answer. Say so rather than leaving it on screen.
        if emitted:
            yield TokenEvent("".join(emitted), provisional=True)

        if last:
            # Offered no tools, it called one anyway. Nothing is run: the cap
            # is the guardrail, and a call it was not offered is not a step.
            break

        conversation.append(gathered)
        for call in calls:
            name = call["name"]
            args = dict(call.get("args") or {})
            yield ToolEvent(name, args)

            tool = by_name.get(name)
            clock = Clock()
            failed: str | None = None
            if tool is None:
                # Only the bound tools can be called. A name that is not one of
                # them is the model inventing a capability, and it is told so
                # rather than the turn dying on a KeyError.
                result = f"No tool named {name!r}. Available: {', '.join(sorted(by_name))}."
                failed = "no such tool"
            else:
                try:
                    result = str(tool.invoke(args))
                except Exception as error:  # noqa: BLE001 - surfaced to the model, not swallowed
                    result = failed = f"{type(error).__name__}: {error}"

            # Counted over the turn, not the call: two results that fit alone
            # can overflow the window together.
            if carried + len(result) > MAX_RESULT_CHARS:
                failed = (
                    f"withheld: {len(result)} characters, over the {MAX_RESULT_CHARS} "
                    f"a turn may carry ({carried} already carried)"
                )
                result = TOO_LONG
            carried += len(result)

            yield LogEvent(
                LogEntry(
                    kind="tool",
                    caller=caller,
                    request={"name": name, "args": args},
                    response={"result": result},
                    started_at=clock.started_at,
                    duration_ms=clock.ms,
                    error=failed,
                )
            )
            yield ToolResultEvent(name, result)
            conversation.append(
                ToolMessage(content=result, tool_call_id=call.get("id") or name, name=name)
            )

    # Falling out of the loop is the cap doing its job. The conditional is the
    # guardrail; this message is only how it explains itself.
    yield DoneEvent(f"stopped after {MAX_STEPS} steps without a final answer")


def _step_entry(
    caller: str,
    body: dict[str, Any],
    clock: Clock,
    reply: AIMessageChunk | None,
    error: str | None = None,
) -> LogEntry:
    spent_in, spent_out = tokens(reply)
    return LogEntry(
        kind="step",
        caller=caller,
        request=body,
        response=response_body(reply),
        started_at=clock.started_at,
        duration_ms=clock.ms,
        model=body.get("model"),
        error=error,
        input_tokens=spent_in,
        output_tokens=spent_out,
    )
