# Hearth — Project Status

As of 2026-10-07. What has been built, how it was verified, where each piece of work sits
right now, and everything that is left, in the order it should be done.

The phase plan and the reasoning behind each decision live in [plan.md](plan.md); the
rules the code must obey live in [CLAUDE.md](../CLAUDE.md). This document is the map
between them: what is done, what is in flight, and what comes next.

---

## Contents

1. [What Hearth is](#1-what-hearth-is)
2. [Where things stand today](#2-where-things-stand-today)
3. [How it fits together](#3-how-it-fits-together)
4. [Phase by phase](#4-phase-by-phase)
5. [Built from the backlog](#5-built-from-the-backlog)
6. [Guardrails and where they are enforced](#6-guardrails-and-where-they-are-enforced)
7. [The code review, 2026-09-25 to 2026-09-28](#7-the-code-review-2026-09-25-to-2026-09-28)
8. [Measuring behaviour: the eval history](#8-measuring-behaviour-the-eval-history)
9. [What is left, in order](#9-what-is-left-in-order)
10. [Known behaviour problems](#10-known-behaviour-problems)
11. [Blocked and open questions](#11-blocked-and-open-questions)
12. [Backlog](#12-backlog)
13. [Running it](#13-running-it)

---

## 1. What Hearth is

A local-first personal assistant. The **Steward** routes each question to a specialist
working over your own data, and everything runs on one machine.

| Name | Role | Where |
|---|---|---|
| **Hearth** | the app | — |
| **Steward** | orchestrator: routes each turn, caps the hops | `steward/` |
| **Tally** | finance: net worth, balances, holdings | `agents/tally.py`, `prompts/tally.md` |
| **Forge** | training: lifts, body measurements | `agents/forge.py`, `prompts/forge.md` |
| **Errand** | web search — the only thing that leaves the machine | `tools/errand.py` |

**The constraint behind every decision:** an RTX 4060 Ti with 8 GB of VRAM, shared by the
chat model and the embedding model, and an 8,192-token context window. The chat model is
`nvidia/nemotron-3-nano-4b` — 4B rather than the ~8B the budget allows, because it is
the largest instruct model on the host that leaves room for the embedding model (see the
README's Models section). Every pass rate in this document belongs to that model.

**The rules that shape the code** (full text in CLAUDE.md): the model never does
arithmetic and never writes SQL; there are no live financial connections and no account
numbers anywhere; nothing leaves the machine except Errand's audited, filtered searches;
there is no telemetry; and every guardrail lives in code, not in a prompt.

---

## 2. Where things stand today

### Branches

`turn-limits`, `carry-forward` and the in-progress caveat rewording all reached `main`
on 2026-09-28 (PRs #4–#6) — the rewording inside commit `136b318`, without the rebase,
test run and eval the plan below had set for it. The merge that brought it in,
`52f975e`, dropped the two lines in `agents/loop.py` that choose a step's model, and
**every Tally and Forge turn on `main` raises `NameError`** (17 tests fail there).

The GenAI review of 2026-10-07 (`docs/reviews/`) found it, and its fixes are on a stack
of local branches, each built on the one before and none yet pushed:

| Branch | Holds | Eval run before merging? |
|---|---|---|
| `fix-agent-loop` | The review and plan; the loop restored, its last step capped; the model size limit back to 6 GiB | **Yes** — first measurement of `136b318`'s caveat rewording too |
| `eval-recording` | A crash is a failed eval run; `-incomplete` results; final-step and title hashes | Optional — no request changed |
| `merge-gate` | CI: lint and tests on every pull request, and a check that a watched change brings an eval result | No |
| `turn-guards` | The context window checked before each turn; the router capped at 256 tokens and 30 s | **Yes** — the router's request changed |
| `portable-data` | `make restore` that works on a new cluster; `make export` / `load-export`; unused columns dropped | No |
| `answer-provenance` | Each stored answer names its model and prompt hash | No — label `no-eval-needed`; the bytes are unchanged |
| `grounding-unitless` | Figures without `$`, a unit or `%` found, reported by the evals, not yet failed | **Yes** — that run is the measurement |
| `workout-idempotency` | A workout sent twice is stored once | No |
| `housekeeping` | SearXNG pinned; this document and the plan brought up to date | No |

### Numbers

- **Tests:** 795 (`make test`) on `housekeeping`, all passing, in about ten seconds, with
  no model. On `main`, 692 pass and 17 fail. Two model tests are deselected by default
  (`pytest -m model`).
- **Eval cases:** 73 (`make eval`, three runs each, about 27 minutes on the host).
- **Latest full eval:** 70/73 cases, 208/219 runs, on `carry-forward` (`7bb7ebb`). Nothing
  after it has been measured: no LM Studio was running on the host for the 2026-10-07
  work, and the host's checkout had no `.env` and no Postgres.

### In one paragraph

Hearth answers questions about your finances and your training from your own records,
routes each question to the right specialist without being told, states coverage gaps
before describing any trend, flags any figure in an answer that no tool produced, keeps
every conversation searchable, and logs every model call verbatim for inspection. It
imports Fidelity positions exports and a body-weight history from a folder outside the
repository, and takes hand entry for balances, workouts and weigh-ins. Web search is
built but has no search engine to talk to yet; document retrieval and MCP are not built.

---

## 3. How it fits together

```
Browser (React + Vite, :5173)
   │  same-origin via Vite's dev proxy
   ▼
FastAPI (:8000, 127.0.0.1 only, Host header checked)
   ├─ /api/chat (SSE) ──► pre-flight check ──► Steward (constrained-JSON router, hop cap)
   │                                              ├─► Tally ─┐
   │                                              ├─► Forge ─┼─► query tools ──► Postgres (read-only role)
   │                                              ├─► Errand ─► egress check ──► SearXNG (not running yet)
   │                                              └─► declined
   ├─ /api/threads, /api/model-log, /api/settings ──► history/ ──► Postgres (read-write role)
   └─ /api/imports, /api/accounts, /api/training ──► ingest/ ──► Postgres (read-write role)

LM Studio (:1234) ◄── router, specialists and title calls
```

**One turn, end to end:** the question is stored first; the pre-flight check refuses
disordered eating, self-harm and hate speech before any model sees it; the router picks
a destination with one constrained-JSON call; the specialist runs an explicit loop — call
a tool, read the computed result, answer — capped in steps, tokens and time; every figure
in the answer is checked against what the tools returned; the answer is stored with its
tool calls and any ungrounded figures; a new thread gets a short title.

**Two database roles, structurally separate.** Every figure the model sees comes through
the read-only role. Only `ingest/` and `history/` hold the read-write connection, and no
tool can reach either.

---

## 4. Phase by phase

Each phase lists what was built, its exit criterion and result, and what — if anything —
remains. Full narrative, including every measurement and every mistake, is in plan.md.

### Phase 0 — Scaffold · done

Repo layout, docker-compose (Postgres with pgvector, SearXNG), `.env.example`,
ruff/mypy/pytest, the Makefile. **Exit:** `/health` serves, Vite runs. **Met.**

The host turned out to have no working Docker (its WSL2 engine fails, and WSL is ruled
out), so it runs a native Postgres 17 from the Windows binary zip; `make up`/`down`/`logs`
drive `pg_ctl` when `PGDATA` is set. `make start` / `scripts\hearth.cmd` start everything
for daily use.

### Phase 1 — Data layer · done

Alembic migrations for every table, the read-only Postgres role, the `snapshot_coverage`
view, the GIN full-text index on messages, and the deterministic golden fixture
(`make seed`). **Exit:** migrations go up and down; the read-only role provably cannot
write. **Met.**

### Phase 2 — Ingestion · done, verified on a real export

The `Normalizer` protocol and the Fidelity positions importer: SHA-256 idempotency, raw
rows stored before normalization, all-or-nothing through a savepoint, loud failure on an
unknown header, account-number-shaped values refused before storage, errors that mask
every digit. Manual entry for accounts and balances. **Exit:** the same file twice is a
no-op; a malformed file writes nothing. **Met** — and the first real import (2026-09-21)
matched Fidelity's totals to the cent.

Also delivered: the evals moved to their own `hearth_eval` database, and real data is
refused beside the fixture (`make unseed` first).

### Phase 3 — Query and compute tools · done, extended 2026-09-28

Five pure-Python tools over fixed parameterized queries:

| Tool | Answers | Agent |
|---|---|---|
| `get_balance_history` | one account across a period | Tally |
| `get_net_worth_trend` | the total across all accounts, with coverage | Tally |
| `get_allocation` | holdings by symbol and by account, on a date | Tally |
| `get_lift_progression` | heaviest set per session, with estimated 1RM | Forge |
| `get_body_metric_trend` | one body measurement across a period | Forge |

**Exit:** all tools tested, partial coverage included. **Met.**

Extended since: long body-metric histories are grouped (review finding 1, merged), and
the net worth trend carries balances forward up to 45 days (finding 6, on
`carry-forward`).

### Phase 4 — Model connection · done

`ChatOpenAI` against LM Studio; structured output through `json_schema`. **Exit:** 10/10
schema-valid. **Met** — 30/30 over three runs.

### Phase 5 — Tally, end to end · done

The finance agent over the Phase 3 tools, streaming over SSE to a React chat. **Exit:**
"how has my net worth moved this year" answers with the tool's figure and states the
coverage gap. **Met**, 3/3: `$38,250.00` to the cent, July's gap stated first.

### Phase 6 — Steward and routing · done

A constrained-JSON classifier behind a `Router` protocol; the hop cap as a conditional
edge in a LangGraph graph. **Exit:** routing ≥ 90% on 20 labelled cases; a delegation loop
stops within 6 hops. **Met** — 60/60, every case unanimous. The router runs with
reasoning off (it produced empty replies with it on) and retries an unreadable reply
once.

### Phase 7 — Eval harness · done

`evals/cases.yaml` and a small runner: three runs per case, a pass rate rather than
pass/fail, results committed to `evals/results/<sha>.json`, a pre-commit reminder.
**Exit:** a prompt edit produces a measurable delta. **Met** — cutting the caveat section
from `tally.md` took the caveat cases from 3/3 to 0/3 and back.

`make test` was separated from `make eval` here: model work moved out of the fast suite,
which went from 377 seconds back to about 3.

### Phase 8 — Thread history · done

A thread panel, Postgres full-text search with English stemming, model-generated titles
(reasoning off, ~10 tokens), and retention (a year from the last message; pinned threads
kept; adjustable in Settings). **Exit:** last week's thread found by a remembered word.
**Met** — found by "squat" and by "squatting".

Added since: **follow-ups** ("yes" to an offer is understood — each specialist sees its
own last three exchanges, capped in characters; the router sees the previous question and
the offer), and **Less · Normal · More** answer lengths.

### Phase 9 — Errand and egress · built; waiting on SearXNG

`tools/errand.py` over plain `httpx`; `validate_search_query()`; the `search_audit` table
and its screen; `errand` as a router destination, offered only while SearXNG answers its
health probe — so with no SearXNG, the router's prompt and schema are byte-identical to
the ones Phase 6 measured. A blocked search is a refusal, audited, with nothing sent.

**Exit:** a prompt engineered to leak a balance into a search raises `EgressViolation`.
**Met through the graph; not end to end**, because nothing is listening.

**Remaining:** SearXNG itself (no Docker on the host), an end-to-end exit test against
it, and review finding 3 — the egress check has gaps that must close first.

### Phase 10 — RAG · blocked

pgvector, local embeddings, `k ≤ 5` with a token cap, logged chunk IDs. **Blocked** on
pgvector, which needs an MSVC build on Windows. The embedding model is chosen and pinned
(`text-embedding-nomic-embed-text-v1.5`). Nothing earlier touches a vector column.

### Phase 11 — Forge · done over the fixture; real data arriving

The training specialist, with the disordered-eating pre-flight check as a code-level
filter (now also self-harm and hate speech, run before routing on every question and
again in each specialist). **Exit:** refusal path tested. **Met** — over 100 filter cases
in both directions.

Real data: **manual entry** for workouts, sets and weigh-ins, in pounds, stored as
entered (built); **a body-weight CSV importer** (`Date, Recorded, Moving Average`) whose
dry run read all 809 rows of the real file (built). The first real import remains the
test of what the file's screenshot could not show.

### Phase 12 — MCP connections · not started

`MultiServerMCPClient`, local stdio servers first, a per-agent tool allowlist, the
tool-schema token budget visible in the UI, write tools blocked, results fenced. Rules
8–11 in CLAUDE.md govern it.

---

## 5. Built from the backlog

- **Model log** — every model call and tool run stored verbatim (the request body the
  client actually built, and the response with token counts), keyed to its question,
  deleted with its thread and after 90 days. Every answer links to its run. It has
  already caught two real problems (an answer parroting its own instructions; a
  misreported unknown lift name).
- **Settings** — three preferences (default answer length, thread retention, model-log
  retention), each from a fixed list, in effect on the next question. The running
  configuration is shown read-only, with every code-enforced guardrail marked locked.
- **Choosing the chat model** — LM Studio's models listed, and only those that fit the
  card offered: trained for tool use, at most 6 GiB, able to switch reasoning off. A
  switch loads the new model at 8,192 tokens before unloading the old one.

---

## 6. Guardrails and where they are enforced

| Rule | Enforced in | Proven by |
|---|---|---|
| Nothing harmful reaches a model | `agents/preflight.py`, before routing and in each specialist | `test_preflight.py`, `test_guardrails.py`, `test_conversation.py` |
| 1. The model never does arithmetic | Tools compute every figure; `agents/grounding.py` flags any figure no tool returned, **matched by kind** (dollars to dollars, weights to weights) | `test_grounding.py`, `test_chat_route.py`; evals fail on any ungrounded figure |
| Context is scoped and capped | `agents/conversation.py`; tool output capped at 6,000 characters a turn | `test_conversation.py`, `test_guardrails.py` |
| 2. The model never writes SQL | Fixed queries through the read-only role | `test_readonly_role.py` |
| 3. No live financial connections | CSV from `HEARTH_DATA_DIR` or hand entry; the API takes a file name, never a path | `test_datadir.py`, `test_data_routes.py` |
| 4. No account numbers | No column for one; four-digit runs refused before storage | `test_schema.py`, `test_fidelity.py`, `test_manual_entry.py` |
| 5. Nothing leaves the machine | Every configured address must be local or LAN at startup; Errand's check and audit | `test_config.py`, `test_egress.py`, `test_errand.py` |
| 6. No telemetry | The model connection refuses to build with tracing on | `test_llm_connection.py` |
| 7. Iteration caps | Hop cap (6) as a graph edge; 4 steps a turn; **4,000 tokens a call and 180 s a turn** (on `turn-limits`) | `test_steward.py`, `test_guardrails.py` |
| Only this machine's pages can read the API | `api/hosts.py` checks the Host header | `test_hosts.py` |
| The chat model fits the card | `model_choice.py` | `test_model_choice.py` |
| No setting loosens a guardrail | `preferences.py` — fixed lists only | `test_preferences.py` |
| Real data stays out of the repo | `HEARTH_DATA_DIR` inside the repo refused; evals on their own database | `test_datadir.py`, `evals/conftest.py` |

---

## 7. The code review, 2026-09-25 to 2026-09-28

An outside review of the codebase found eleven issues. Each was fixed test-first, and
every fix that changes what the model reads was measured with a full eval run.

### Done and merged to `main`

**Finding 1 — a long weight history overflowed the context window.**
`get_body_metric_trend` listed every row; the real export's 809 weigh-ins came to 19,484
characters, more than the whole window. Past 31 recordings, readings are now grouped by
the finest of week, month, quarter or year that gives at most 24 groups, each with its
average, low, high and count computed in Python; 809 weigh-ins render in about 850
characters. The agent loop also caps tool output at 6,000 characters a turn, replacing a
result over it with a `caveat:` sentence rather than truncating it. An eval case holds the
grouped path (3/3). *Commits `1c5d09c`, `70ec969`.*

**Finding 2 — the grounding check accepted figures that matched date digits.**
"$31", "$2,026" and "$28" passed as grounded because the day, year and month of every
tool line counted as sources. A dollar figure is now grounded only by a dollar figure in
a tool result, a weight by a weight, a percentage by a percentage; a question still
grounds by any number in it, with its dates removed. *Commit `d3868cd`.*

**Finding 4 — eval results could not be reproduced across dependency upgrades.**
`requirements.lock` is now committed (frozen from the host's venv) and applied as pip
*constraints*, so it pins versions without forcing Windows-only packages onto the Mac.
Every eval result records package versions and a hash of every request the evals measure,
built by langchain-openai's own payload builder: equal hashes mean the same prompt, schema
and settings were sent. `python -m evals.fingerprint` prints both without a run.
*Commit `2d73a98`.*

**Finding 5 — the re-measure reminder missed prompt text kept in code.**
The pre-commit hook now also watches `steward/` (the router's destination descriptions and
its pinned schema docstring) and `llm.py`. *Commit `4911247`.*

**Finding 8 — the API trusted any Host header.**
A web page using DNS rebinding could have read the thread history and the Model log.
`api/hosts.py` now refuses any request whose Host header does not name this machine.
Written in place of Starlette's `TrustedHostMiddleware`, which misreads `[::1]:8000` — and
`localhost` resolves to IPv6 first on the host. Checked against a live server.
*Commit `b4100b8`.*

### Done, and merged on 2026-09-28 (PRs #4–#6)

**Finding 7 — no limit on how long or how much a turn could generate** (`turn-limits`).
Both limits were measured before they were set, over the heaviest eval questions:

| Limit | Measured | Set to |
|---|---|---|
| Tokens a model call (reasoning included) | max 2,399 · 90th percentile 2,039 · median 593 | 4,000 |
| Seconds a turn | slowest 42.7 s, 3 steps; slowest step 32 s | 180, checked before each step |

The first guess of 2,000 tokens would have cut off one answer in ten. An answer cut off
at the limit is taken back rather than saved as a shorter one. Both limits are shown
locked on the Settings screen.

**A bug found on the way:** text a model streams before calling a tool was meant to be
withdrawn, but the chat route, the UI and the eval runner all kept it — on screen, in the
stored answer and in what the grounding check read. The tests had missed it because
their scripted events never streamed the narration first, as the real loop does. Fixed in
all three places. *Commits `15554c8`, `e69640f`.*

**Finding 6 — net worth counted a date complete only if every account had a balance on
that exact day** (`carry-forward`). An export on the 21st and a balance typed in on the
20th made both dates partial totals. An account with no balance on a date now contributes
its latest from the 45 days before, and every such balance is named — on its date's line
and in a second `caveat:` sentence the model repeats. Past 45 days it is still a gap. The
golden fixture now misses Retirement's June (30 days after May — carried) as well as its
July (61 days — a gap), so it holds both cases; the headline `$38,250.00` did not move.
*Commits `7bb7ebb`, `8b418e2`.*

### Not yet done

**Finding 3 — the egress filter checks a number's shape, not its value.** "is 38,250
dollars a good net worth" and "38.2k" pass; "who won the 2024 world series" is refused.
Must be fixed before SearXNG goes live. See [section 9](#9-what-is-left-in-order), item 5.

**Finding 9 — chat stream event types are hand-copied** in `web/src/api/chat.ts` rather
than generated from the API schema like every other type.

**Finding 10 — the hop cap allows one routing call too many.** `_after_route` checks
`hops > MAX_HOPS`, so a seventh router call is made before the halt; the test allows it.

**Finding 11 — `ingest/importer.py` imports from `scripts/seed.py`**, so production code
depends on a script. Move the fixture account IDs somewhere both can import.

---

## 8. Measuring behaviour: the eval history

### Recorded runs

| Result | Cases | Runs | Notes |
|---|---|---|---|
| `2098ede` | 40/40 | 120/120 | First committed baseline (Phase 7) |
| `2bcd3b7` | 57/61 | 173/183 | Follow-ups added (Phase 8) |
| `32cbff7` | 59/62 | 176/186 | MVP 1, tagged `mvp-1`; before Errand |
| `70ec969` | 68/72 | 201/216 | First with a fingerprint; Errand cases recorded for the first time |
| `15554c8` | 69/72 | 205/216 | `turn-limits` |
| `7bb7ebb` | 70/73 | 208/219 | `carry-forward`; the file is named `-dirty` because unrelated edits were saved in the run's last seconds, after the code had loaded |

### How to read a result

**One run's failures are not evidence.** On 2026-09-28, byte-identical requests went 3/3,
0/3 and 3/3 across three full runs. Cases fail inside a long run and pass on their own;
the likeliest cause is LM Studio's prompt-cache or batching state tipping a temperature-0
model across a near tie. Two tools settle whether a failure is real:

- **Compare request bytes.** Build the request at both commits and hash it. Identical
  bytes rule the code out in seconds.
- **Run an interleaved A/B** on the case in question — alternating the two versions, five
  runs each. This is how the offer wording (Phase 8) and the token cap (finding 7) were
  settled.

**The fingerprint** (`prompts` and `packages` in each result) says whether two runs sent
the model the same thing. After finding 7, the specialists' hashes changed — every
request now carries `max_tokens` — and the router's did not, which is the point.

---

## 9. What is left, in order

### Land what is built

**1. Put `main` right.** Push `fix-agent-loop`, run `make eval` on the host, commit the
result, and merge. That eval is also the first measurement of the caveat rewording that
reached `main` unmeasured in `136b318`: compare it with `7bb7ebb`'s 70/73, and settle any
caveat case that moves with an interleaved A/B rather than by reading one run.

**2. Turn the gate on.** Merge `eval-recording` and `merge-gate`, then require the
`check` and `eval-recorded` checks on `main` in GitHub's branch protection. Until that
setting is on, the workflows report and block nothing.

**3. Merge the rest in order**, each through a pull request so the gate sees it:
`turn-guards` (with an eval run — the router's request changed; and check
`ROUTER_MAX_TOKENS` against `select max(output_tokens) from model_log where kind =
'route'`), `portable-data`, `answer-provenance` (label `no-eval-needed`), then
`grounding-unitless` (with an eval run, whose `notes` say what strict grounding would
flag), `workout-idempotency` and `housekeeping`.

**4. Enforce strict grounding** once a full run's notes show no false positives:
`ungrounded(..., strict=True)` in the chat route and the eval runner, measured like any
other change.

### Before SearXNG goes live

**5. Finding 3 — make the egress check value-aware.** Normalise the numbers in a query
(`38,250`, `38250`, `38.2k`, `38 thousand`) and refuse any within a small tolerance of a
recorded balance or holding, read through the read-only role. Keep the shape rules as a
backstop. Relaxing the four-digit rule for years (1900–2099) weakens a rule-5 guard and is
a decision for you, once the value check exists. Must land before Errand can search.

### Small cleanups, any order

**6. Finding 10 — the hop cap's off-by-one.** Check `>= MAX_HOPS` in `_after_specialist`
so the halt comes before the seventh router call; tighten the test to `== MAX_HOPS`.

**7. Finding 11 — `ingest` importing from `scripts`.** Move `FIXTURE_ACCOUNT_IDS` (and the
id derivation it needs) into a module both can import.

**8. Finding 9 — generated event types.** Declare each SSE event as a pydantic model the
OpenAPI schema includes, so `npm run gen:types` produces them.

### Behaviour the evals keep surfacing

**9. Forge restates remembered figures.** Asked "should I add more weight next week?"
after a squat answer, Forge declines as it should — then repeats squat figures from its
earlier answer without calling a tool, which the grounding check correctly flags. Prompt
work, settled by an interleaved A/B.

**10. Dropped cents in the detailed positions answer.** `grounded-detailed-positions` writes
"$27,600" for "$27,600.00" and has failed every run since before MVP 1. Same approach.

**11. Try the evals at one parallel slot.** LM Studio loads the model with four. Loading
it with one for an eval run is a cheap test of whether batching causes the run-to-run
drift.

### Blocked on the machine

**12. SearXNG** — Phase 9's end-to-end test. Needs a working Docker or a native install.

**13. pgvector** — Phase 10. Needs an MSVC build on Windows; then install it, assert it in
the test harness, and build retrieval.

**14. Phase 12, MCP** — after 12 and 13, so its tool-schema budget can be set against a
window that already holds retrieved chunks.

**15. The first real weight-history import** — the dry run read all 809 rows; the import
itself is the test of what the file's screenshot could not show.

---

## 10. Known behaviour problems

| Case | Pass rate | What happens | Status |
|---|---|---|---|
| `route-followup-training-advice` | 0/3, every run | "should I add more weight next week?" after a squat answer routes to Forge, not `unsupported`. Forge then declines in one line (`caveat-followup-advice-is-declined`) | Known since Phase 8; kept in the case file so it stays measured |
| `grounded-detailed-positions` | 0/3, every run | Drops the cents from `$27,600.00` | Item 10 above |
| `caveat-followup-advice-is-declined` | 1–2/3 | Forge restates remembered figures | Item 9 above |
| `caveat-1rm-is-an-estimate` | 0/3 to 3/3 | Varies with run state; an A/B showed 5/5 both ways | Drift, not a code fault |
| `route-followup-finance-advice` | 0/3 to 3/3 | Varies with run state on byte-identical requests | Drift, not a code fault |

---

## 11. Blocked and open questions

- **Docker on the host** — its WSL2 engine needs a Windows feature that is off, and WSL is
  ruled out. Blocks SearXNG (and with it Phase 9's end-to-end test) and self-hosted
  Langfuse. The alternative is running SearXNG natively.
- **pgvector on Windows** — needs an MSVC toolchain. Blocks Phase 10.
- **The chat model is 4B**, not the ~8B the budget allows. Moving to a genuine 8B means
  re-measuring every number in `evals/results/`.
- **Relaxing the egress check for years** — a decision to make once finding 3's value
  check exists.

---

## 12. Backlog

- **Morning digest** — a scheduled single run: net worth change, today's session. It can
  be built with no model at all, from tool results and a template.
- **Pre-computed changes between dates** — each trend line carrying its change from the
  line before, so the model never works out a difference itself. Tool text is prompt text,
  so it needs an interleaved A/B.
- **Human-in-the-loop interrupts** — only matters once a tool can write.
- **CSV and chart export.**

Cut deliberately (see plan.md): voice, a third agent, proactive alerts, multi-model
routing, transaction-level ingestion.

---

## 13. Running it

```bash
make start      # Postgres if down, then backend and frontend; opens the browser
make test       # ~700 tests, a few seconds, no model
make eval       # 73 cases × 3 runs against LM Studio, ~27 minutes
make lint       # ruff + mypy + tsc
make backup     # compressed dump outside the repo; keeps the newest fourteen
make freeze     # on the host: pin the venv into requirements.lock
python -m evals.fingerprint   # request hashes and package versions, without a run
```

**Host notes.** Postgres is a native cluster and does not survive a reboot — `make start`
brings it up. `make` is from winget and needs its `bin` on `PATH`. `localhost` resolves
to IPv6 first. LM Studio's `/v1/models` lists downloaded models, not loaded ones — use
`lms ps` for what is resident.

**Adding real data.** `make unseed` first (real data is refused beside the fixture);
create each account under Manual entry with the label the export uses; import from
Data & imports; check each account's total against the institution's.
