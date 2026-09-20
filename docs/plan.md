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

**With one asterisk.** The golden fixture is a 2024 dataset and it is now 2026, so "this
year" only reaches data when the day is pinned. The eval seam exists (`today` on the chat
request, required by the agent, never defaulted in code) but the underlying question is
unanswered: either the fixture gets rebased to the current year on seed, or every eval
that says "this year" pins the date and stops testing the phrase a person would type.
Phase 7 has to decide, because it is the phase that turns this question into cases.

Tool selection was measured before the agent was written rather than assumed: 18/18 across
six questions and three runs, with parseable dates 12/12. That is a better result than
Phase 4's routing check predicted, and the likely reason is that a tool carries a name and
a description while a router choice was a bare agent label — see Phase 6.

**This is a legitimate stopping point.** One agent over your own data is most of the value.

## Phase 6 — Steward and routing (3)

Constrained-JSON classifier behind a `Router` protocol. Iteration cap in a conditional
edge. 20 labeled routing cases. Agent attribution in the UI.

**Exit:** routing accuracy >= 90% on the labeled set. An adversarial delegation-loop prompt
terminates within 6 hops.

*If accuracy lands near 70%, fall back to keyword rules plus embedding similarity rather
than grinding on prompt tweaks.*

Phase 5 produced evidence worth spending first. The same model that routed a net-worth
question to `forge` 30 times out of 30 — choosing between the bare labels `tally`, `forge`
and `errand` — picked the right tool 18 times out of 18 when the options carried names and
descriptions. Before concluding the model cannot route, try giving the classifier what the
tool schemas gave it: a sentence per destination saying what it is for. That is a cheaper
experiment than the fallback and it may remove the need for it.

## Phase 7 — Eval harness (2)

Extend Phase 6's 20 cases into `evals/cases.yaml`. Thirty-line pytest runner, 3 runs per
case, results to `evals/results/<sha>.json`, git hook scoped to `prompts/` and `agents/`.

Cases: routing, tool selection, grounded numbers (exact figure present), required caveats,
refusals.

**Exit:** editing a prompt produces a measurable delta rather than a vibe.

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

## Phase 11 — Forge (2) — BLOCKED on fitness data source

Second specialist. Disordered-eating pre-flight check as a code-level input filter, not
prompt text.

**Exit:** refusal path tested and passing.

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
