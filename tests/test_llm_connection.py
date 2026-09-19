"""Phase 4: the model connection.

The exit criterion — a constrained-JSON call returning schema-valid output ten
times out of ten — can only be met where the model actually runs, which is the
Windows GPU host, not a development Mac. That test skips when the endpoint is
unreachable and says why.

The rest of this file does not need a model server and runs anywhere: that the
model name comes from the environment, and that the connection refuses to exist
while a tracing variable is switched on.
"""

from collections.abc import Iterator
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, Field, ValidationError

from config import get_model_settings
from llm import TelemetryEnabledError, chat_model, structured_model

#: The exit criterion is ten for ten.
RUNS = 10


class Route(BaseModel):
    """Roughly the shape Phase 6's router will need — a constrained choice, not
    prose. Smoke-testing with it means the thing being proven is the thing that
    will actually be load-bearing."""

    agent: Literal["tally", "forge", "errand"]
    confidence: float = Field(ge=0.0, le=1.0)


@pytest.fixture
def _clean_model_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHAT_MODEL", "a-model-id-from-the-environment")
    monkeypatch.setenv("EMBEDDING_MODEL", "an-embedding-model-id")
    for name in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING"):
        monkeypatch.delenv(name, raising=False)
    get_model_settings.cache_clear()
    yield
    get_model_settings.cache_clear()


@pytest.fixture(scope="session")
def model_endpoint() -> str:
    """The LM Studio base URL, or a skip explaining why there isn't one."""
    try:
        settings = get_model_settings()
    except ValidationError:
        pytest.skip("CHAT_MODEL and EMBEDDING_MODEL are unset — see .env.example")

    url = settings.lm_studio_base_url
    try:
        httpx.get(f"{url}/models", timeout=3.0)
    except httpx.HTTPError:
        pytest.skip(
            f"no model server at {url}. This is expected on a development "
            "machine; the Phase 4 exit criterion is measured on the GPU host."
        )
    return url


# --- these need no model server -----------------------------------------------


def test_model_name_comes_from_the_environment(_clean_model_settings: None) -> None:
    """Never hardcoded. Assume it changes."""
    assert chat_model().model_name == "a-model-id-from-the-environment"


def test_base_url_points_at_the_configured_endpoint(_clean_model_settings: None) -> None:
    model = chat_model()
    assert str(model.openai_api_base).startswith("http://")


@pytest.mark.parametrize(
    "variable", ["LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING"]
)
def test_refuses_to_connect_while_tracing_is_enabled(
    _clean_model_settings: None, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """Rule 6 in code rather than in prose. A conversation about someone's
    finances does not leave this machine because an environment variable was
    left switched on."""
    monkeypatch.setenv(variable, "true")

    with pytest.raises(TelemetryEnabledError) as raised:
        chat_model()

    assert variable in str(raised.value)


def test_tracing_disabled_is_not_treated_as_enabled(
    _clean_model_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert chat_model() is not None


# --- this one needs the model -------------------------------------------------


@pytest.mark.model
def test_constrained_json_is_schema_valid_ten_times(model_endpoint: str) -> None:
    """Phase 4's exit criterion.

    Schema validity is what is asserted, not routing correctness — whether the
    model picks the right agent is Phase 6's problem, measured over a labelled
    set. What matters here is that the JSON comes back parseable every time,
    because everything downstream assumes it does.
    """
    model = structured_model(Route)
    prompt = (
        "Classify which specialist should answer this question. "
        "Question: how has my net worth moved this year?"
    )

    results = [model.invoke(prompt) for _ in range(RUNS)]

    assert len(results) == RUNS
    assert all(isinstance(result, Route) for result in results)

    chosen = [result.agent for result in results]
    print(f"\n  schema-valid {len(results)}/{RUNS}; routes chosen: {set(chosen)}")
