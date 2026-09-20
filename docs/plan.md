# Hearth — Development Plan

Effort in evenings. Least reliable part of this document — treat as relative sizing.

The LLM arrives at Phase 4, not Phase 1. Data layer, ingestion and query tools are ordinary
software with ordinary tests, and that is where correctness lives. A weak local model
narrating trustworthy results is fine. No model rescues a bad data layer.

UI reference: the Design canvas — seven screens (Chat, Data & imports, Manual entry, Egress
audit, Model log, Connections, Settings). Both side panels collapse; in the real app that
state is one localStorage key read on mount, not per-route.

Five of those seven are scheduled: Chat (5, 6, 8), Data & imports and Manual entry (2),
Egress audit (9), Connections (12). Model log and Settings are in the backlog with their
reasons. There is no UI phase — each screen lands with the capability behind it, because a
screen built before its backend is a mock with a router in front of it.

---

## Phase 0 — Scaffold (1)

Repo layout, docker-compose (Postgres+pgvector, SearXNG), `.env.example`, ruff/mypy/pytest,
`make dev`.

**Exit:** `docker compose up` works. FastAPI serves `/health`. Vite dev server runs.

## Phase 1 — Data layer (2–3)

Alembic migrations. Read-only Postgres role for tools. `snapshot_coverage` view. Golden
fixture seed script — the evals reuse it, so make it deterministic.

Tables: `import_batch`, `import_row`, `account`, `balance_snapshot`, `holding_snapshot`,
`body_metric`, `workout`, `workout_set`, `thread`, `message`, `search_audit`.
GIN index on `to_tsvector('english', message.content)`.

**Exit:** migrations up and down cleanly. Read-only role provably cannot write.

## Phase 2 — Ingestion (3–4) — BLOCKED on real CSV headers

`Normalizer` protocol, one institution, SHA-256 idempotency, raw rows stored before
normalization, loud failure on unknown headers. Manual entry form in React.

**Exit:** importing the same file twice makes the second a no-op. A malformed file fails
with no partial writes.

## Phase 3 — Query and compute tools (2)

`get_balance_history`, `get_net_worth_trend` (with coverage), `get_allocation`,
`get_lift_progression`. Pure Python. Pytest against the fixture.

**Exit:** all tools tested including partial-coverage cases. No LLM involved yet — and the
project is already useful.

## Phase 4 — Model connection (1) — DONE

`ChatOpenAI` against LM Studio. Structured-output smoke test.

**Exit:** a constrained-JSON call returns schema-valid output 10/10 times. **Met** on the
host against `nvidia/nemotron-3-nano-4b` at 8,192 context — 10/10 on each of three runs,
30/30 overall.

One thing that measurement surfaced, for Phase 6 rather than here: all 30 responses were
schema-valid and all 30 routed a net-worth question to `forge`. Schema validity is what
Phase 4 asked for and it holds. Routing accuracy is Phase 6's exit criterion and, on this
single example, it is 0/30 — so expect the fallback the phase already anticipates
(keyword rules plus embedding similarity) rather than prompt tweaking.

## Phase 5 — Tally, end to end (3) — DONE

Finance agent only, using Phase 3 tools. SSE streaming to a minimal React chat. Single
thread, no history UI.

**Exit:** "how has my net worth moved this year" returns a correct, tool-derived answer
that states coverage caveats. **Met**, 3/3 runs: calls `net_worth_trend` with the right
dates, reports `$38,250.00` — the figure the tool returned, to the cent — and states the
July gap before describing the trend.

The fixture's year was the catch and is now fixed. `make seed` builds the dataset in the
current year by default, so "this year" reaches data on the day you ask it; month ends are
computed rather than listed, because February is not 29 days outside a leap year.
`HEARTH_FIXTURE_YEAR` pins it when a result has to be comparable across time rather than
merely across machines. The figures never move — opening balances and monthly steps are
fixed, so an eval asserting an exact amount holds in any year. What moves is the dates, and
the uuid5 ids derived from them.

Tool selection was measured before the agent was written rather than assumed: 18/18 across
six questions and three runs, with parseable dates 12/12. That is a better result than
Phase 4's routing check predicted, and the likely reason is that a tool carries a name and
a description while a router choice was a bare agent label — see Phase 6.

**This is a legitimate stopping point.** One agent over your own data is most of the value.

## Phase 6 — Steward and routing (3) — DONE

Constrained-JSON classifier behind a `Router` protocol. Iteration cap in a conditional
edge. 20 labeled routing cases. Agent attribution in the UI.

**Exit:** routing accuracy >= 90% on the labeled set. An adversarial delegation-loop prompt
terminates within 6 hops. **Both met** — 60/60 (100%), every one of the 20 cases routed the
same way on all three runs, which is the stricter reading `evals/README.md` asks for. The
loop test drives the graph with a specialist that hands every turn back and a router that
keeps accepting it, and the edge stops it at six.

**The fallback was not needed, and the experiment is why.** Phase 5 guessed that the model
was not bad at routing but bad at choosing between bare labels. Measured: descriptions
100%, bare labels 85%. Bare labels would have failed the exit criterion; a sentence per
destination cleared it. `tests/test_router.py::test_descriptions_beat_bare_labels` keeps
the comparison so a future edit to the descriptions can be measured against it rather than
argued about.

The first measurement came in at exactly 90% — passing, but on the boundary — and both
failures were the same failure: `unsupported` routed to `tally` for "what is a good price
for a squat rack" and "should I move my savings into an index fund". The classifier could
tell money from training but not *money* from *the money you have recorded*. Sharpening the
descriptions to say REPORTING already-recorded data, and adding one question to the prompt
— can this be answered by reading back what they have recorded? — took it to 100%.

LangGraph arrives here rather than earlier, because this is the first thing in Hearth with
more than one path through it. The hop cap is `_after_route`, evaluated before any
specialist runs, so an over-budget turn costs no further model calls.

## Phase 7 — Eval harness (2) — DONE

Extend Phase 6's 20 cases into `evals/cases.yaml`. Thirty-line pytest runner, 3 runs per
case, results to `evals/results/<sha>.json`, git hook scoped to `prompts/` and `agents/`.

Cases: routing, tool selection, grounded numbers (exact figure present), required caveats,
refusals. Forty cases — 20 routing, 5 tool, 4 grounded, 4 caveat, 7 refusal.

**Exit:** editing a prompt produces a measurable delta rather than a vibe. **Met, and
demonstrated rather than assumed.** Baseline is 40/40 cases and 120/120 runs. A suite that
passes everything on its first run proves nothing until it is shown to fail, so the caveat
section was cut out of `prompts/tally.md` and the cases re-run: both caveat cases went
3/3 to 0/3, and back to 3/3 when it was restored. That is the delta the phase asked for.

What the failure showed is worth keeping. The degraded prompt did not make the model drop
the caveat — it made it paraphrase, "complete only from Aug 31 onward" in place of the
sentence the tool handed it. The cases assert the ISO date verbatim and so caught it. That
strictness is the point: `Coverage.caveat()` writes the qualification in full precisely so
a 4B model never has to compose one, and a paraphrase is the first step toward a summary.

`make test` was the other half of this phase, unplanned. Behavioural cases had accumulated
inside it — 180 model calls by Phase 6 — and it had grown from 3 seconds to 377. Model
work now lives in `evals/` behind `make eval`, `make test` is back to about 3 seconds and
holds nothing that needs a model, and the two model-marked tests that remain (the Phase 4
connection smoke test and the router A/B) are deselected by default and run with
`pytest -m model`.

The pre-commit hook warns and blocks nothing. A hook that costs ten minutes against a
local model is a hook that gets bypassed with `--no-verify` inside a week.

## Phase 8 — Thread history (2)

Thread list panel, full-text search, locally generated titles (one short call after turn 1,
cap output ~10 tokens). Retention decision, not "forever" by default.

**Exit:** a thread from last week is findable by a word you remember typing.

## Phase 9 — Errand and egress (2)

SearXNG with JSON format enabled and limiter relaxed. `SearxSearchWrapper`.
`validate_search_query()` rejecting currency amounts, 4+ digit numbers and configured
identifiers. `search_audit` table. Audit view in the UI.

**Exit:** a prompt engineered to leak a balance into a search query raises
`EgressViolation`.

Known gotchas: SearXNG returns HTML by default — enable JSON in `settings.yml` and restart,
or every request 403s. Its bot limiter throttles agents; relax it in `limiter.toml`, safe
because the instance is private.

## Phase 10 — RAG (3) — BLOCKED on pgvector on the host

pgvector, local embeddings via LM Studio `/v1/embeddings`, chunking, `k<=5` with token cap,
retrieved chunk IDs logged.

**Exit:** retrieval traceable per answer. Context stays within budget.

## Phase 11 — Forge (2) — DONE over the fixture; real data source still open

Second specialist. Disordered-eating pre-flight check as a code-level input filter, not
prompt text.

**Exit:** refusal path tested and passing. **Met** — 32 tests over `agents/preflight.py`
plus the path end to end through the API.

Brought forward from its slot because Phase 6 has nothing to route between with one
specialist, and because the agent needed only a prompt and a tool list once Tally had
proved the shape. The block was always on a *real* fitness data source, and that is still
open: Forge answers over the golden fixture, same as Tally.

The filter is thirteen refusal cases and sixteen that must get through, and the second list
is the longer one deliberately. A filter that refuses a lifter asking about their squat has
not been made safer, it has been made useless, and a useless filter gets switched off by
the person it was written for. `agents/forge.py` calls it before `chat_model()` is ever
reached, so the refusal is a `return`, not something the model is asked to produce and
might not.

`get_body_metric_trend` was added to `tools/fitness.py` to go with it: `body_metric` was
seeded from Phase 1 and nothing read it, and a fitness specialist that cannot tell you your
body-mass trend is a strange thing to ship — particularly when that number is the reason
the pre-flight check exists.

## Phase 12 — MCP connections (2–3)

`MultiServerMCPClient`, local stdio servers first (filesystem scoped to one directory, a
notes vault). Per-agent tool allowlist. Tool-schema token budget surfaced in the UI. Write
tools blocked. Results fenced before entering context.

**Exit:** total tool-schema cost is visible and under a set ceiling; no server is remote.

---

## Backlog (not scheduled)

- **Morning digest** — cron'd single run: net worth delta, today's session. Probably the
  feature most likely to make the thing get used daily.
- Human-in-the-loop interrupts — only matters once a tool can write
- CSV / chart export
- Model log browser — the drawer from a chat message is the useful part; the standalone
  browser duplicates what a tracing tool does better
- Settings screen — in the Design canvas but never scheduled, and on inspection it has
  nothing to hold. Configuration is `.env`, read at startup: model names, database URLs,
  ports, the SearXNG endpoint. A screen that edits those either restarts the process or
  lies about being in effect, and one user editing their own `.env` in a text editor is
  not worse off. Revisit if something genuinely per-session appears — a retention window,
  a default agent — rather than building the screen and then looking for its contents.
- Langfuse — revisit only if pytest results files stop being enough

## Cut deliberately

Voice (no VRAM), a third agent (more tools beat more agents at this size), proactive
threshold alerts, multi-model routing (can't hold two models in 8GB — revisit on a 3090),
transaction-level ingestion (drags in merchant categorization).

## Open questions

1. CSV header row from one institution — unblocks Phase 2
2. Fitness data: app export or manual? — unblocks Phase 11
3. SearXNG without Docker — blocks Phase 9. See the Postgres answer below: that machine
   has no working Docker, and SearXNG is a compose service. Either Docker gets fixed or
   SearXNG runs natively.
4. pgvector on the host — blocks Phase 10 alongside nothing else now that the embedding
   model is chosen. It needs an MSVC build on Windows. Nothing earlier touches a vector
   column, so it can wait, but Phase 10 must install it and assert it rather than
   inheriting the test harness's warning.

**Answered:** LM Studio runs as the Windows app on the GPU host. No WSL anywhere — so the
host toolchain is Windows-native, and `make` as written is Unix-only.

**Answered:** the GPU host runs a **native Postgres 17.11**, not Docker. Docker Desktop is
installed there but its WSL2 engine fails with `Virtual Machine Platform not enabled`, and
enabling WSL2 would contradict the answer above. The PostgreSQL Windows binary zip needs
no installer and no admin rights, so `make up`/`down`/`logs` drive `pg_ctl` when `PGDATA`
is set and compose when it is not. The full chain runs there: 63 tests pass, none skipped.
See the README.

**Answered:** the embedding model is `text-embedding-nomic-embed-text-v1.5` — the
`nomic-embed-text` of the two candidates, and the only one on the host. Pinned in `.env`
and recorded in the README. Phase 10 is unblocked on this count.

**Answered:** the chat model is `nvidia/nemotron-3-nano-4b`. It is 4B, not the ~8B the
budget allows — see the README for why nothing in the 8B class is available here, and for
what that costs. Every eval pass rate from here belongs to that model.
