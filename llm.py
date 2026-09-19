"""The connection to the local model.

LM Studio speaks the OpenAI protocol, so `ChatOpenAI` reaches it with a
`base_url` override and a dummy key it ignores. Nothing here knows the name of
a model: both come from the environment, because they change and a default in
code is a hardcoded model name by another route.

Structured output goes through `json_schema`, not tool calling. LM Studio parses
tool calls out of model text against a chat template, and at 8B that is
unreliable enough to be load-bearing risk — the router depends on getting valid
JSON back, so it asks for JSON directly rather than for a function call that
happens to contain some.
"""

import os
from typing import cast

from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from config import get_model_settings

#: Every environment variable that would send a trace off this machine. Newer
#: LangChain reads the LANGSMITH_ names, older the LANGCHAIN_ ones.
TRACING_VARIABLES = (
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGSMITH_TRACING",
)
TRUTHY = {"1", "true", "yes", "on"}


class TelemetryEnabledError(RuntimeError):
    """Raised when a tracing variable is switched on.

    Rule 6 admits no third-party telemetry anywhere, and rule 7 says a rule that
    exists only as prose is not implemented. This is the implementation: the
    model connection refuses to be built rather than quietly shipping a
    conversation about someone's finances to a SaaS backend.
    """


def _refuse_if_tracing_enabled() -> None:
    enabled = [
        name for name in TRACING_VARIABLES if os.environ.get(name, "").strip().lower() in TRUTHY
    ]
    if enabled:
        raise TelemetryEnabledError(
            f"{', '.join(enabled)} is enabled. Hearth sends nothing to a third "
            "party. Unset it, or set it to false, before using the model."
        )


def chat_model(
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> ChatOpenAI:
    """The chat model, configured from the environment.

    Temperature defaults to zero. Local models are nondeterministic regardless
    — which is why the evals record a pass rate rather than pass/fail — but
    there is no reason to add sampling noise to a routing decision on purpose.
    """
    _refuse_if_tracing_enabled()
    settings = get_model_settings()

    return ChatOpenAI(
        model=settings.chat_model,
        base_url=settings.lm_studio_base_url,
        api_key=SecretStr(settings.lm_studio_api_key),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        # One retry. A local model that failed twice is not going to succeed on
        # the third attempt, and the turn should fail visibly instead.
        max_retries=1,
    )


def structured_model[SchemaT: BaseModel](
    schema: type[SchemaT],
    *,
    strict: bool | None = None,
    **kwargs: object,
) -> Runnable[LanguageModelInput, SchemaT]:
    """A model constrained to return `schema`.

    `strict` is left to LangChain's default unless given. LM Studio's support
    for the strict flag depends on the loaded model's grammar backend, so it is
    a knob rather than an assumption.
    """
    model = chat_model(**kwargs)  # type: ignore[arg-type]
    # with_structured_output is overloaded and widens to dict when the schema
    # is not a literal; it returns instances of `schema` at runtime.
    return cast(
        Runnable[LanguageModelInput, SchemaT],
        model.with_structured_output(schema, method="json_schema", strict=strict),
    )
