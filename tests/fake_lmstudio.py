"""A stand-in for LM Studio's own API, for tests that must not touch the real one.

An `httpx.MockTransport` that holds a model list and records every load and
unload it is asked for, so nothing using it needs LM Studio running and nothing
using it can load a model onto the card.
"""

import json
from typing import Any

import httpx

GiB = 2**30


def _entry(
    key: str,
    size_gib: float,
    *,
    tools: bool = True,
    reasoning: list[str] | None = None,
    loaded: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "type": "llm",
        "key": key,
        "display_name": key.split("/")[-1],
        "params_string": "4B",
        "size_bytes": int(size_gib * GiB),
        "capabilities": {
            "trained_for_tool_use": tools,
            "reasoning": {"allowed_options": reasoning} if reasoning else None,
        },
        "loaded_instances": [
            {"id": f"{key}:{i}", "config": {"context_length": ctx}}
            for i, ctx in enumerate(loaded or [])
        ],
    }


class FakeLMStudio:
    """A model list, and a record of every load and unload it was asked for."""

    def __init__(self, *entries: dict[str, Any], fail_load: str | None = None) -> None:
        self.models = {e["key"]: e for e in entries}
        self.calls: list[tuple[str, str]] = []
        self.fail_load = fail_load

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/models":
            embedding = {"type": "embeddings", "key": "nomic-embed", "size_bytes": 1}
            return httpx.Response(200, json={"models": [*self.models.values(), embedding]})
        body = json.loads(request.content or b"{}")
        if path == "/api/v1/models/load":
            key = body["model"]
            self.calls.append(("load", f"{key}@{body.get('context_length')}"))
            if key == self.fail_load:
                return httpx.Response(500, json={"error": {"message": "out of memory"}})
            self.models[key]["loaded_instances"].append(
                {"id": f"{key}:new", "config": {"context_length": body["context_length"]}}
            )
            return httpx.Response(200, json={"instance_id": f"{key}:new"})
        if path == "/api/v1/models/unload":
            instance = body["instance_id"]
            self.calls.append(("unload", instance))
            for entry in self.models.values():
                entry["loaded_instances"] = [
                    i for i in entry["loaded_instances"] if i["id"] != instance
                ]
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))
