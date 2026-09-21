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

## Phase 2 — Ingestion (3–4) — DONE, verified on a real export

`Normalizer` protocol, one institution, SHA-256 idempotency, raw rows stored before
normalization, loud failure on unknown headers. Manual entry form in React.

**Exit:** importing the same file twice makes the second a no-op. A malformed file fails
with no partial writes. **Met** over invented rows under the real header:
`tests/test_importer.py` holds both, and "no partial writes" is taken at its hard end — a
file whose raw rows are already stored when row 5 fails to normalize leaves nothing
behind. That test was shown to fail with the savepoint removed before it was trusted.

The institution is Fidelity, and the one thing ever seen of its export is the header row —
fifteen columns, no account number among them. Everything under it in the tests is a guess
at the shape: the cash line with no quantity, a pending-activity line, a disclaimer
footer. So the importer is strict rather than tolerant, and the first real import is the
real test. What it gets wrong it refuses, naming the row, the column and the cell's shape
with its digits masked, so the error can be read out without the balance. The balance is
the sum of an account's `Current value` rows — the export has no balance column — and the
first real import should be checked against the total Fidelity shows.

Three things the phase needed that the plan did not list:

- **The evals moved to their own database first.** `make eval` read the development
  database, which was about to hold real balances, and a failing case writes the opening
  of the answer into a committed file. `hearth_eval` is rebuilt from the fixture on every
  run.
- **Real data is refused beside the fixture.** A net worth summed across invented accounts
  and real ones is neither. `make unseed` empties the database for a first import, and
  both it and `make seed` refuse to delete real data without `--force`.
- **Rule 4 is enforced before storage, not after.** Raw rows are stored whole, so a column
  or account name that looks like an account number — four digits in a row — is refused
  before the first insert, as is a filename carrying one.

**The first real import, 2026-09-21: no refusals, and both account totals matched
Fidelity's to the cent.** Seventeen rows, all stored raw and all normalized. What it
settled, checked from counts and shapes so no real figure entered the session: the
money-market line is a value with no quantity or price, as guessed; the footer fits the
one-cell-per-line rule; there was no pending-activity line; and one position had a
negative quantity and value — a short or a written option — which the fixture had never
had, and now does. The balance is a sum the importer computes because the export has no
balance column, and this was the check that the sum is the right one.

Screens: Data & imports and Manual entry, both from the Design canvas. Manual entry
creates accounts (an import never does: the export cannot say what kind each is) and
records balances as US currency. Imports and hand-entered balances can be removed; an
imported balance only with its import.

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

**Addendum, 2026-09-21: the router reasons no more.** In daily use "what are my current
positions" failed with a raw `ValueError`: the router, reasoning first, had produced twenty
reasoning tokens and an empty reply, which the structured-output parser rejects. A probe
reproduced it in 3 of 18 calls; the labelled cases never had. With reasoning off the
router scored 40/40 on the labelled cases with no empty replies, at 0.41s a call against
1.29s, and routed "what do I own right now" to Tally 5 times in 5 where reasoning on had
declined it 5 times in 5. So `ConstrainedJSONRouter` now asks for none, retries an
unreadable reply once, and if the second is unreadable too ends the turn with a sentence
rather than a traceback. The specialists keep reasoning: switched off, they stopped calling
tools and answered from nothing — "I have no data for that period" with data present —
and "low" saved about 0.2s of an 11s turn. Most of a turn's time is the specialist.

LangGraph arrives here rather than earlier, because this is the first thing in Hearth with
more than one path through it. The hop cap is `_after_route`, evaluated before any
specialist runs, so an over-budget turn costs no further model calls.

## Phase 7 — Eval harness (2) — DONE

Extend Phase 6's 20 cases into `evals/cases.yaml`. Thirty-line pytest runner, 3 runs per
case, results to `evals/results/<sha>.json`, git hook scoped to `prompts/` and `agents/`.

Cases: routing, tool selection, grounded numbers (exact figure present), required caveats,
refusals. Forty cases — 20 routing, 5 tool, 4 grounded, 4 caveat, 7 refusal.

**Exit:** editing a prompt produces a measurable delta rather than a vibe. **Met, and
demonstrated rather than assumed.** The first committed baseline is
`evals/results/2098ede.json`: 40/40 cases, 120/120 runs, every case 3/3, on a clean tree.

It is not the first 40/40 this phase reported, and the difference matters. That one was
measured on a dirty tree and got lucky: a re-run against the same commit failed
`caveat-no-data-is-not-no-change` 0/3, and the fault was the case, not the answer. It
forbade the word "flat", which appears in the tool's own denial — "not a flat balance" —
so a model that quoted the tool failed and one that paraphrased passed. The case now
forbids claim-shaped phrases ("has not changed", "remained flat") and was checked in both
directions: it still catches the answer that shipped in Phase 5, and passes the right one.
A forbidden substring that occurs inside its own denial fails a correct answer
intermittently, which is the worst way for a check to be wrong.

A suite that
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

## Phase 8 — Thread history (2) — DONE

Thread list panel, full-text search, locally generated titles (one short call after turn 1,
cap output ~10 tokens). Retention decision, not "forever" by default.

**Exit:** a thread from last week is findable by a word you remember typing. **Met** —
`tests/test_history.py` ages a thread by seven days and finds it by "squat", and by
"squatting", a word that appears nowhere in it. That second search is the one that
matters: it proves English stemming over the Phase 1 GIN index rather than a substring
match that would pass the first. Through the live model on a throwaway database, four
questions became four titled threads, and the search found the squat one with the matched
word marked in its snippet.

**Retention: a year from a thread's last message; pinned threads are kept.** Decided
2026-09-21. A sweep runs at startup and whenever a thread is started, so it holds without a
restart. Unpinning a thread idle for more than a year asks first — it would go at the next
sweep.

**The ~10-token cap needed reasoning switched off, and that was measured before it was
written.** The model reasons for about 300 tokens before every answer, and `max_tokens`
counts them: capped at 20 or at 32, every title call came back empty. With
`reasoning_effort="none"`: no reasoning tokens, 9 completion tokens, 0.2s instead of 6.3s,
and 24/24 schema-valid across eight questions and three runs, identical titles run to run.
Titles are made from the question alone — the answer is where the figures are.

The same switch is untried on the router and the specialists, each of which currently
spends a few hundred reasoning tokens per call. Whether turning it off there costs accuracy
is a question for `make eval`, not a guess.

**A thread is a record, not context.** The model still answers each question on its own;
earlier turns are not sent to it. Sending them is a decision about the 8,192-token window
and about how the Steward routes a follow-up, and it is not this phase's. The chat says so
under the composer rather than letting a follow-up look understood.

**Found while testing, and deliberately not fixed here: the UI's route around the
pre-flight check.** The UI never names an agent, so every turn goes through the Steward,
and "how do I make myself sick after dinner" routed to `unsupported`. The pre-flight check
lives inside Forge and never saw it: the Steward declined generically, and the thread was
titled by the model. Phase 11's end-to-end test named `agent=forge` directly, and so never
took the path the UI takes. Moving the check ahead of routing is the obvious fix and not a
safe one as the filter stands — it refuses "should I purge my old credit card accounts" as
purging, and reads "cut 500 calories a day" as a 500-calorie intake. Resolved the same
day, patterns first — see the answered questions below.

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

1. Fitness data: app export or manual? — unblocks Phase 11
2. SearXNG without Docker — blocks Phase 9. See the Postgres answer below: that machine
   has no working Docker, and SearXNG is a compose service. Either Docker gets fixed or
   SearXNG runs natively.
3. pgvector on the host — blocks Phase 10 alongside nothing else now that the embedding
   model is chosen. It needs an MSVC build on Windows. Nothing earlier touches a vector
   column, so it can wait, but Phase 10 must install it and assert it rather than
   inheriting the test harness's warning.

**Answered (2026-09-21):** where the pre-flight check runs — before routing, on every
question, and again in each specialist. The patterns were tightened first, so it no longer
refuses "purge my old accounts", "stop eating out", "rose fast for 3 days", "a 500 calorie
deficit" or "throwing up after leg day", and it now catches "fast for 2 weeks" and "a 900
calorie diet", which it had missed. Its scope grew on the owner's instruction: **self-harm
and hate speech are refused too**, each with its own reply. `tests/test_guardrails.py`
proves every entry point refuses with every model constructor rigged to fail, and was
shown to fail with the Steward's check removed. See the README's Guardrails table for
where every rule is enforced.

**Answered:** the CSV header — Fidelity's positions export, `Portfolio_Positions_<Mon>-<DD>-<YYYY>.csv`:
`Account name, Symbol, Description, Quantity, Last price, Last price change, Current value,
Today's gain/loss dollar, Today's gain/loss percent, Total gain/loss dollar, Total gain/loss
percent, Percent of account, Cost basis total, Average cost basis, Type`. No account-number
column. Phase 2 is built on it; the rows beneath it have not been seen.

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
