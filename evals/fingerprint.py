"""What an eval run measured, beyond the model's name.

A pass rate belongs to a model, and to exactly what that model was sent. Some of
what it is sent lives outside prompts/: the router's destination descriptions
and reasoning setting are in steward/router.py, pydantic puts an enum's
docstring into the JSON schema — which is how editing a docstring once changed a
measured routing prompt — and a langchain-openai release can reshape the request
body with no file in this repository changing. The pre-commit hook watches
paths; this watches the request.

So each run records two things beside its numbers:

  - a short hash of every request a turn can send — routing, each step of a
    specialist, the title — built by langchain-openai's
    own payload builder (`modellog.request_body`) exactly as a turn builds it,
    with the question left as a placeholder. Two runs with equal hashes sent
    the model the same prompt, schemas and settings; unequal hashes say
    something the model reads changed, whether or not a prompt file did.
  - the versions of the packages that build and send those requests.

The model's name is left out of the hash: the result file records it already,
and the same prompts against another model are the same prompts.

`python -m evals.fingerprint` prints both, to compare against a recorded run
without spending ten minutes on one.
"""

import datetime as dt
import hashlib
import json
from importlib.metadata import version
from typing import Any

from agents import forge, loop, tally
from agents.loop import DETAILS
from history import titles
from modellog import request_body
from steward.router import routing_request
from tools.bindings import FORGE_TOOLS, TALLY_TOOLS

#: Stands in for the question, which differs by case. What is hashed is
#: everything around it.
PLACEHOLDER = "<question>"

#: The packages between this code and the bytes LM Studio receives.
PACKAGES = ("langchain-core", "langchain-openai", "langgraph", "openai", "pydantic")


def _digest(body: dict[str, Any]) -> str:
    body = {k: v for k, v in body.items() if k != "model"}
    encoded = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def prompt_hashes(today: dt.date) -> dict[str, str]:
    """One hash per request a turn can send: the router with and without
    Errand; each specialist at each detail level, both the steps that may call
    a tool and the last step, which is offered none; and the title call.
    `today` is the evals' pinned day, because it is written into the
    specialists' prompts.

    The last step and the title were added on 2026-10-07. The keys recorded
    before then are built exactly as they were, so their values still compare
    with earlier results."""
    hashes: dict[str, str] = {}
    for with_errand in (False, True):
        model, asked = routing_request(PLACEHOLDER, None, with_errand=with_errand)
        hashes["steward+errand" if with_errand else "steward"] = _digest(request_body(model, asked))
    specialists = (
        ("tally", tally.system_prompt, TALLY_TOOLS),
        ("forge", forge.system_prompt, FORGE_TOOLS),
    )
    for name, system_prompt, tools in specialists:
        for detail in DETAILS:
            conversation = loop.opening(system_prompt(today, detail), PLACEHOLDER)
            hashes[f"{name}/{detail}"] = _digest(
                request_body(loop.bound(tools), conversation, stream=True)
            )
            hashes[f"{name}/{detail}/final"] = _digest(
                request_body(loop.answer_only(), conversation, stream=True)
            )
    # The fence strips the placeholder's brackets; what is hashed is everything
    # around the question, which is the point.
    title_model, title_asked = titles.title_request(PLACEHOLDER)
    hashes["title"] = _digest(request_body(title_model, title_asked))
    return hashes


def package_versions() -> dict[str, str]:
    return {package: version(package) for package in PACKAGES}


if __name__ == "__main__":
    from evals import runner

    today = runner.load()[1]
    print(json.dumps({"packages": package_versions(), "prompts": prompt_hashes(today)}, indent=2))
