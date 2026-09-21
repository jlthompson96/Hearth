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

## What a switch cannot promise

A pass rate belongs to a model. `make eval` has measured the models its result
files name, and no other, and the screen says which.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from config import get_model_settings
from db.models import Preference
from ingest.errors import Refused

#: About an 8B model at Q4 with room to spare, on an 8GB card that also holds
#: the KV cache, the embedding model and the desktop. See the module docstring.
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

    if CONTEXT_TOKENS not in model.loaded_contexts:
        # Loaded at another length is loaded wrong: every budget assumes this one.
        for instance in _instances(http, target):
            _unload(http, instance)
        response = http.post(
            f"{_root()}/api/v1/models/load",
            json={"model": target, "context_length": CONTEXT_TOKENS},
        )
        if response.status_code >= 400:
            try:
                detail = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = f"HTTP {response.status_code}"
            raise ModelRefusedError(f"LM Studio could not load {model.name}: {detail}")

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
