Behavioural evals. `make eval`.

Not part of `make test`. These need a model and a seeded database, take minutes,
and measure a pass rate rather than asserting a fact — three reasons not to have
them in the loop you run after every edit. `make test` holds nothing that needs
a model and runs in about three seconds.

- `cases.yaml` — the cases, in five kinds
- `runner.py` — loading, running, recording. No pytest in it
- `test_cases.py` — a shell over the runner, one pytest case per case
- `conftest.py` — builds `hearth_eval`, the evals' own copy of the fixture
- `results/<sha>.json` — recorded runs, committed

## The five kinds

| kind | asks | model? |
|---|---|---|
| `routing` | the Steward picks the right destination | yes, one JSON call |
| `tool` | a named specialist picks the right tool | yes, a full turn |
| `grounded` | the tool's exact figure appears verbatim | yes, a full turn |
| `caveat` | a required sentence is present | yes, a full turn |
| `refusal` | the pre-flight check fires with the right signal — or, for must-pass cases, does not | **no** |

`tool` cases name the agent, which bypasses routing. Without that, a routing
failure and a tool-selection failure are indistinguishable from outside.

`refusal` cases need no model at all, because the check runs before inference.
That is the design, not an optimisation — a refusal that depends on sampling is
not a refusal.

## Assertions, not judges

The only local model available is the same one being graded, so judging would be
circular. Every case is a string comparison. Seed a known state and assert the
exact figure appears: "roughly $38,000" in place of "$38,250.00" is a rounding
failure and a fail.

A grounded or caveat case also fails on any figure the answer states that no tool
returned (`agents/grounding.py`, the same check the chat runs on every real answer): the
right figure being present is not enough if a computed one sits beside it.

`must_not_contain` exists for the failure that is worse than a wrong number —
`caveat-no-data-is-not-no-change` asserts the answer does *not* say "unchanged"
about a period with no data. That bug shipped once.

## Pass rates, and when they are not enough

Local models are nondeterministic, so each case runs three times and records a
rate. `all_runs: true` demands every run pass, and routing and refusal cases all
carry it: a refusal that holds two times in three is not a refusal, and a route
that lands correctly two times in three sends every third question to the wrong
agent.

## Errand is pinned, not probed

In the app the router is offered Errand only while SearXNG answers. In the evals a
case says which router it measures: `with_errand: true` offers it, and every other
case runs without it — the prompt and schema Phase 6 measured, word for word. So a
result does not depend on whether a search engine happened to be running, and the
`route-errand-*` cases measure the other prompt on purpose.

## The database

Evals run against `hearth_eval`, a database of their own, dropped and rebuilt
from the golden fixture at the start of every run (`conftest.py`). They never
open the development database, and do not need `make seed` first.

That separation matters from Phase 2 on, when the development database holds
real imports. Graded against real balances the cases would fail, and a failing
case's result keeps the opening of the answer — in a file that is committed. The
fixture asserts which database the tools are reading before any case runs.

## The date

`today` is pinned in the case file. "This year" is a different question on a
different day, and a case that drifts with the calendar measures the calendar.
`{year}` is substituted with the fixture's year at load time — the figures never
move between years, but the dates do.

## Results

`results/<sha>.json` records the model name alongside the numbers, because a
pass rate belongs to a model. The same cases against a different one are a
different measurement, not a comparable one.

A run against a tree with uncommitted changes is written as `<sha>-dirty.json`
and is gitignored. It measured no commit, so it cannot be compared to one.

Every case in the file ends in a result. A run that raises — a crash in the code
under test, or LM Studio going away mid-run — is a failed run whose detail
reads `raised <Error>: ...`, not a missing case. Before 2026-10-07 a raising case
left no result at all, and the totals counted only the cases that came back:
the carry-forward merge, which broke every specialist turn, would have recorded
the routing and refusal cases alone and read as a clean pass. `totals` now
carries `cases_expected` beside `cases`, and a run that recorded fewer — a `-k`
filter, for instance — is written as `<sha>-incomplete.json`. It is not a
baseline.

It also records what the model was sent (`fingerprint.py`): `prompts` is a short
hash of each request a turn can send — the router with and without Errand, each
specialist at each detail level (`tally/normal`), the last step of each, which
is offered no tools (`tally/normal/final`), and the title call (`title`) — built
by langchain-openai's own payload builder with the question left out, and
`packages` the versions that build it. The `/final` and `title` hashes were
added on 2026-10-07; the others are built as before and compare with older runs.
Equal hashes mean two runs sent the same prompts, schemas and settings. Unequal
hashes mean something the model reads changed, whether or not a file under
`prompts/` did: a docstring pydantic puts in a schema, a constant in
`steward/router.py`, or a langchain-openai upgrade. `python -m evals.fingerprint`
prints both without a run, to compare against a recorded one. Results recorded
before 2026-09-28 have neither.

## The hook

`make hooks` points git at `.githooks`. The pre-commit hook notices when you
stage a change to `prompts/`, `agents/`, `steward/`, `tools/bindings.py` or
`llm.py` and reminds you to re-measure. It does
not run the evals and blocks nothing: a pre-commit hook that costs ten minutes
is a hook that gets bypassed within a week.
