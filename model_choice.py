"""Which chat model answers: chosen from what LM Studio has, and what this card can run.

`.env`'s `CHAT_MODEL` is the default — what the app starts with, what `make eval`
measures, and what "use .env's model" goes back to. The Settings screen can
choose another. The choice is stored as a preference and in effect from the
next question, with no restart (`llm.use_chat_model`). A model name is never
written in code: it comes from `.env` or from LM Studio's own list.

## The card decides, in code

CLAUDE.md's binding constraint is the RTX 4060 Ti's 8GB: nothing larger than
about 8B at Q4. LM Studio on this machine also holds 20B, 27B, 32B, 70B and 80B
models, which it would run partly from system memory at a fraction of the speed.
So a model is offered only when:

- it is a chat model LM Studio can serve (`type == "llm"`);
- it was trained for tool use — Tally and Forge answer by calling tools;
- its weights fit `MAX_MODEL_BYTES`. An 8B model at Q4_K_M is about 4.9GB, and
  the rest of the card holds the KV cache for 8,192 tokens, the embedding model
  and the desktop. The model in use when this was written is 2.64 GiB;
- it can switch reasoning off. The router and the title call need it off, and
  that was measured, not assumed: on qwen3-4b-thinking, which cannot, routing
  took 10.9s and 25.8s a call against 0.4s, the title call spent all its tokens
  reasoning and returned nothing, and a whole question had not finished after
  five minutes (2026-09-21).

Anything else is listed with the reason it is not offered, and refused if it is
asked for anyway. The screen shows the rule; this module enforces it.

## Switching loads, then unloads

LM Studio loads a model it is asked for on first use, at a context length of its
own choosing, and every budget here is measured against 8,192 tokens. So a
switch loads the chosen model explicitly at that length before it unloads the
model it replaces. If the load fails, nothing has changed.

LM Studio also unloads a model left idle, and loads it again on its next use
with the settings it has saved for that model — 8,192 for nemotron-3-nano-4b,
checked. Another model's saved length may differ, so the Settings screen shows
the length each loaded model has, and choosing the model already in use
reloads it at 8,192.

## Every turn checks the window too

A switch is not the only way a model comes to be loaded: LM Studio loads one on
first use and reloads an idled one, each at its saved length. So before every
turn `window_problem` asks — at most once a minute — and loads a model that is
not loaded at all, or refuses the turn when it is loaded only at another
length (added 2026-10-07).

## What a switch cannot promise

A pass rate belongs to a model. `make eval` has measured the models its result
files name, and no other, and the screen says which.
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

import llm
from config import get_model_settings
from db.models import Preference
from ingest.errors import Refused

log = logging.getLogger("hearth")

#: About an 8B model at Q4 with room to spare, on an 8GB card that also holds
#: the KV cache, the embedding model and the desktop. See the module docstring.
#: Raised to 7.5 GiB on 2026-09-28 so gemma-4-12b (7.04 GiB) could be tried,
#: and put back on 2026-10-07: past CLAUDE.md's ~8B-at-Q4 budget, contrary to
#: the README and the tests, and nothing recorded that it earned its place.
MAX_MODEL_BYTES = 6 * 2**30

#: The window every budget in Hearth is measured against (CLAUDE.md).
CONTEXT_TOKENS = 8192

#: Stored in the `preference` table under this key. Absent means `.env`'s.
PREFERENCE_KEY = "chat_model"

#: Reading the list is quick. Loading moves gigabytes from disk into VRAM.
LIST_TIMEOUT = httpx.Timeout(5.0)
LOAD_TIMEOUT = httpx.Timeout(180.0, connect=5.0)


class ModelRefusedError(Refused):
    """Not a model this card should run, or one LM Studio would not load. The
    message is written to be shown; the API answers 422 with it."""

    kind = "model_refused"


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    params: str | None
    size_bytes: int
    tool_use: bool
    #: The context length of each loaded instance; empty when not loaded.
    loaded_contexts: tuple[int, ...]
    #: Why it is not offered, or None when it is.
    refused: str | None


def _root() -> str:
    """LM Studio's own API sits beside its OpenAI-compatible one."""
    return get_model_settings().lm_studio_base_url.rstrip("/").removesuffix("/v1")


def _why_not(entry: dict[str, Any]) -> str | None:
    capabilities = entry.get("capabilities") or {}
    if not capabilities.get("trained_for_tool_use"):
        return "not trained for tool use, and Tally and Forge answer by calling tools"
    size = int(entry.get("size_bytes") or 0)
    if size > MAX_MODEL_BYTES:
        return (
            f"{size / 2**30:.2f} GiB — more than the {MAX_MODEL_BYTES / 2**30:.0f} GiB "
            "this card can give a chat model"
        )
    reasoning = capabilities.get("reasoning")
    if reasoning and not {"off", "none"} & set(reasoning.get("allowed_options") or []):
        return (
            "it cannot switch reasoning off, and the router and titles need it off — "
            "a model like it took up to 26 seconds to route one question"
        )
    return None


def _entries(http: httpx.Client) -> list[dict[str, Any]]:
    response = http.get(f"{_root()}/api/v1/models")
    response.raise_for_status()
    return [e for e in response.json().get("models", []) if e.get("type") == "llm"]


def catalog(client: httpx.Client | None = None) -> list[Model]:
    """Every chat model LM Studio has, offered or not, smallest first."""
    http = client or httpx.Client(timeout=LIST_TIMEOUT)
    out = []
    for entry in _entries(http):
        capabilities = entry.get("capabilities") or {}
        out.append(
            Model(
                key=entry["key"],
                name=entry.get("display_name") or entry["key"],
                params=entry.get("params_string"),
                size_bytes=int(entry.get("size_bytes") or 0),
                tool_use=bool(capabilities.get("trained_for_tool_use")),
                loaded_contexts=tuple(
                    int((i.get("config") or {}).get("context_length") or 0)
                    for i in entry.get("loaded_instances") or []
                ),
                refused=_why_not(entry),
            )
        )
    return sorted(out, key=lambda m: m.size_bytes)


def chosen(conn: sa.Connection) -> str | None:
    """The model chosen on the Settings screen, or None for `.env`'s."""
    value = conn.execute(
        sa.select(Preference.value).where(Preference.key == PREFERENCE_KEY)
    ).scalar()
    return value if isinstance(value, str) and value else None


def _instances(http: httpx.Client, key: str) -> list[dict[str, Any]]:
    for entry in _entries(http):
        if entry.get("key") == key:
            return list(entry.get("loaded_instances") or [])
    return []


def _unload(http: httpx.Client, instance: dict[str, Any]) -> None:
    http.post(f"{_root()}/api/v1/models/unload", json={"instance_id": instance["id"]})


def _load(http: httpx.Client, model: Model) -> None:
    """Load `model` at `CONTEXT_TOKENS`, or say why LM Studio would not."""
    response = http.post(
        f"{_root()}/api/v1/models/load",
        json={"model": model.key, "context_length": CONTEXT_TOKENS},
    )
    if response.status_code >= 400:
        try:
            detail = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = f"HTTP {response.status_code}"
        raise ModelRefusedError(f"LM Studio could not load {model.name}: {detail}")


# --- the window a turn runs at --------------------------------------------------------

#: How long an answer about the window is believed, either way: once a minute at
#: most, like Errand's probe, so a turn rarely pays for asking.
WINDOW_TTL = 60.0

#: (when it was asked, which model, what was wrong or None).
_window: tuple[float, str, str | None] | None = None


def forget_window() -> None:
    """Ask again on the next turn — after a switch, or in a test."""
    global _window
    _window = None


def window_problem(client: httpx.Client | None = None) -> str | None:
    """Why the chat model is not ready to answer at the window every budget
    assumes, said to be shown — or None when it is ready.

    Every budget here was measured against `CONTEXT_TOKENS`, and LM Studio loads
    a model on first use, and reloads an idled one, at whatever length it saved
    for it. `switch` loads at the right length; this covers every other way a
    model comes to be loaded. Checked before each turn, believed for a minute.

    - Loaded at `CONTEXT_TOKENS`: ready.
    - Not loaded: loaded now at `CONTEXT_TOKENS`, as `switch` does, rather than
      left for LM Studio to load at its saved length on the first call.
    - Loaded only at another length: not ready. Reloading would unload an
      instance someone may have loaded on purpose, so the turn is refused with
      the way out — the Settings screen reloads it.
    - LM Studio's own API not answering: ready as far as anyone can tell. A
      server that speaks only the OpenAI protocol has no such API, and a guard
      that cannot see must not stop every question. Logged, so it is not silent.
    """
    global _window
    key = llm.active_chat_model()
    now = time.monotonic()
    if _window is not None and _window[1] == key and now - _window[0] < WINDOW_TTL:
        return _window[2]
    problem = _check_window(key, client)
    _window = (time.monotonic(), key, problem)
    return problem


def _check_window(key: str, client: httpx.Client | None) -> str | None:
    http = client or httpx.Client(timeout=LOAD_TIMEOUT)
    try:
        model = next((m for m in catalog(http) if m.key == key), None)
        if model is None:
            return (
                f"LM Studio has no chat model called {key!r}, so there is nothing to answer "
                "with. Choose one on the Settings screen."
            )
        if CONTEXT_TOKENS in model.loaded_contexts:
            return None
        if model.loaded_contexts:
            lengths = " and ".join(f"{n:,}" for n in sorted(set(model.loaded_contexts)))
            return (
                f"The chat model is loaded with a {lengths}-token window, and every budget in "
                f"Hearth assumes {CONTEXT_TOKENS:,}. Reload it at {CONTEXT_TOKENS:,} from the "
                "Settings screen, then ask again."
            )
        _load(http, model)
        return None
    except ModelRefusedError as refused:
        return str(refused)
    except (httpx.HTTPError, ValueError) as error:
        log.warning(
            "context window not checked: LM Studio's API did not answer (%s)",
            type(error).__name__,
        )
        return None
    finally:
        if client is None:
            http.close()


def usable(key: str | None, client: httpx.Client | None = None) -> str | None:
    """A stored choice, if LM Studio still lists it — None, `.env`'s model,
    when it answers and does not. Kept when LM Studio cannot be asked: at
    startup it may simply not be running yet."""
    if key is None:
        return None
    http = client or httpx.Client(timeout=LIST_TIMEOUT)
    try:
        listed = {m.key for m in catalog(http)}
    except (httpx.HTTPError, ValueError):
        return key
    finally:
        if client is None:
            http.close()
    if key in listed:
        return key
    log.warning("the chosen chat model %r is not in LM Studio's list; using .env's CHAT_MODEL", key)
    return None


def switch(conn: sa.Connection, key: str | None, *, client: httpx.Client | None = None) -> str:
    """Make `key` the chat model — None for `.env`'s — and return the key now in
    effect. Refused unless it is offered. Loaded at `CONTEXT_TOKENS` before the
    model it replaces is unloaded, so a failed load changes nothing."""
    http = client or httpx.Client(timeout=LOAD_TIMEOUT)
    default = get_model_settings().chat_model
    target = key or default
    previous = chosen(conn) or default

    model = next((m for m in catalog(http) if m.key == target), None)
    if model is None:
        raise ModelRefusedError(f"LM Studio has no chat model called {target!r}.")
    if model.refused:
        raise ModelRefusedError(f"{model.name} is not offered: {model.refused}.")

    # Whatever happens next, a minute-old answer about the window no longer holds:
    # a reload on the Settings screen is how a refused turn is put right.
    forget_window()

    if CONTEXT_TOKENS not in model.loaded_contexts:
        # Loaded at another length is loaded wrong: every budget assumes this one.
        for instance in _instances(http, target):
            _unload(http, instance)
        _load(http, model)

    if previous != target:
        for instance in _instances(http, previous):
            _unload(http, instance)

    if key is None:
        conn.execute(sa.delete(Preference).where(Preference.key == PREFERENCE_KEY))
    else:
        conn.execute(
            insert(Preference)
            .values(key=PREFERENCE_KEY, value=key)
            .on_conflict_do_update(
                index_elements=[Preference.key], set_={"value": key, "updated_at": sa.func.now()}
            )
        )
    return target


def measured(results: Path) -> set[str]:
    """Every model a recorded `make eval` run has measured."""
    names: set[str] = set()
    for path in results.glob("*.json"):
        try:
            model = json.loads(path.read_text(encoding="utf-8")).get("model")
        except (OSError, ValueError):
            continue
        if isinstance(model, str):
            names.add(model)
    return names
