# Hearth: GenAI architecture review

**Date:** 2026-10-07 · **Reviewed at:** `main` @ `4c21485` · **Scope:** whole repository

This was a read-only review. No tests, evals or model calls were run. The only command that touched code was one `ruff check` rule, which reads files. `.env` and `HEARTH_DATA_DIR` were not opened. Line numbers refer to `4c21485`.

---

## 1. Executive Summary

Hearth's architecture fits its priorities unusually well. Every figure comes from a tested Python tool, guardrails live in code, evals assert exact strings against a disposable fixture database, and all persisted data sits in one explicitly migrated Postgres schema.

`main` is broken, though. Merge `52f975e` dropped the two lines in `agents/loop.py` that pick the model for each step, so every Tally and Forge turn raises `NameError` before its first model call. Three PR merges brought that change to `main` without any test, lint or eval run, and the eval recorder would have hidden it, because a case that raises never reaches the results file.

**Top three actions:**

1. Restore the two lines, and give the tool-free final step the same 4,000-token cap as the other steps.
2. Add a merge gate that runs `ruff`, `mypy` and `pytest` against a fixture Postgres.
3. Make the eval runner record a raising case as a failed run.

---

## 2. System Map

### Components

| Component | Path | Role |
|---|---|---|
| API | `api/main.py`, `api/routes/*.py` | FastAPI app. Chat streams over SSE (`api/routes/chat.py`). A Host-header check keeps other origins out (`api/hosts.py`). |
| Steward | `steward/graph.py`, `steward/router.py` | A LangGraph `StateGraph` compiled with no checkpointer (`graph.py:247`). It routes with one constrained-JSON call and caps hops at 6 in a conditional edge. |
| Specialists | `agents/tally.py`, `agents/forge.py`, `agents/loop.py` | Each is a prompt plus a tool list over one hand-written loop. Limits: 4 steps, 4,000 tokens per step, 180 seconds per turn, 6,000 characters of tool output per turn. |
| Pre-flight filter | `agents/preflight.py` | Refuses disordered eating, self-harm and hate speech before routing, and again inside each specialist. |
| Grounding check | `agents/grounding.py` | Flags any dollar, weight or percent figure in an answer that no tool returned. |
| Context window | `agents/conversation.py` | Up to 3 earlier exchanges and 4,000 characters, each specialist seeing only its own answers. |
| Query tools | `tools/bindings.py`, `tools/finance.py`, `tools/fitness.py` | Five `@tool` wrappers over parameterized SQL, run as the read-only role. |
| Errand | `tools/errand.py` | `httpx` to SearXNG with an egress check and an audit row. Not live, because SearXNG is not running. |
| Ingestion | `ingest/` | Fidelity positions CSV, body-weight CSV, and manual entry. |
| History | `history/` | Threads, full-text search, titles, retention and the model log. |
| Model choice | `model_choice.py` | Uses LM Studio's native `/api/v1/models` API to list, load and unload models. |
| Evals | `evals/` | 73 cases, 3 runs each: 37 routing, 13 refusal, 9 grounded, 9 caveat and 5 tool. |
| Frontend | `web/` | React, TypeScript and Vite. Types are generated from OpenAPI, except the chat event types. |

### Dependencies

Versions come from `requirements.lock`, frozen on the GPU host and applied as pip constraints (`Makefile:91`):

| Package | Version |
|---|---|
| langchain | 1.4.2 |
| langchain-core | 1.6.3 |
| langchain-openai | 1.6.2 |
| langgraph | 1.2.11 |
| openai | 3.16.2 |
| pydantic | 2.13.5 |
| fastapi | 0.141.1 |
| SQLAlchemy | 2.0.54 |
| alembic | 1.20.0 |
| psycopg | 3.3.6 |
| httpx | 0.28.1 |

`requirements.txt` holds only version ranges. `langchain-mcp-adapters` is not present yet; it arrives with Phase 12.

### LLM call sites

| Call | Where | Mechanism | Limits |
|---|---|---|---|
| Factory | `llm.py:76-118`, `120-157` | `ChatOpenAI` with `base_url` and the model name from env, or from the Settings preference | Refuses to build while a tracing variable is on. 120-second timeout, `max_retries=1`. |
| Routing | `steward/router.py:241-317` | `with_structured_output(method="json_schema", include_raw=True)`, temperature 0, reasoning off | 2 attempts. **No `max_tokens`.** Runs outside the turn deadline. |
| Specialist steps | `agents/loop.py:208-336` | `bind_tools`, streamed | `max_tokens=4000` on steps 1 to 3. **The final step has no `max_tokens`** (`loop.py:187-191`). Deadline checked between steps. |
| Title | `history/titles.py:48-94` | JSON schema, reasoning off, `max_tokens=24` | Falls back to the question's first words. |

**Model identifiers.** `CHAT_MODEL` and `EMBEDDING_MODEL` come from env with no defaults (`config.py:108-109`). The Settings screen can override the chat model at runtime (`llm.py:63-66`). The README records `nvidia/nemotron-3-nano-4b`, which is an LM Studio key. The quantization behind that key is not recorded anywhere in the repo.

**Prompts.** These live in `prompts/*.md`: tally, forge, steward and its two fragments, title, and three detail levels. Model-facing text also lives in code:

- the router's destination descriptions and its pinned schema docstring (`steward/router.py:63-128`);
- tool docstrings and rendered tool output (`tools/bindings.py`);
- the coverage caveat sentences (`tools/finance.py:90-140`).

### Data stores

Section 5 has the full inventory. In short: one Postgres database (`hearth`) holds everything, plus two disposable copies for tests and evals. Real CSVs sit in `HEARTH_DATA_DIR`. Backups are `pg_dump` custom-format files. Eval results are JSON committed to git.

### Configuration and secrets

`.env` is gitignored and was not read. `.env.example` documents every key. `config.local_only` refuses any URL that is not loopback or LAN at startup (`config.py:31-60`).

**No secrets appear in git history.** I scanned for added `.env`, CSV, dump and key files, and for `PASSWORD`, `SECRET`, `API_KEY` and `TOKEN` assignments. Only placeholders and a regex turned up.

### Containers, deployment and tests

- `docker-compose.yml` defines `pgvector/pgvector:pg17` and `searxng/searxng:latest`. The GPU host runs a native Postgres 17 instead, because its Docker does not work.
- There is no CI: no `.github/` directory exists. The pre-commit hook warns and blocks nothing.
- `tests/` holds about 700 tests, per the status doc. They need a running Postgres and use a throwaway `hearth_test` database. `evals/` runs against `hearth_eval`.

### Not covered

- `web/src` was reviewed only for storage use and the hand-written chat types.
- Tests, lint and evals were not run. Only the one `ruff` rule above was.

---

## 3. Information Gaps

Ranked by how much each limits this review.

1. **Whether `make test` passes on `main`.** I did not run it, because the review rules say to ask first. The code predicts failures in two places:
   - the tests that drive `loop.run` (`tests/test_guardrails.py:189-300`, `tests/test_model_log.py:94-121`) should hit the undefined names;
   - `tests/test_model_choice.py:121` and `:168` expect a 7.04 GiB model to be refused, but `model_choice.py:66` now offers it.

   To check, run `make test`.
2. **Which commit the GPU host is running.** If it is `4c21485`, every finance and training question fails today. To check, run `git rev-parse --short HEAD` in the host's checkout.
3. **LM Studio settings, which live outside the repo.** These are the context-overflow policy, the GGUF quantization behind `CHAT_MODEL`, each model's saved context length, and the number of parallel slots. They decide whether an over-budget prompt fails loudly or is silently truncated, and whether a re-download reproduces the eval numbers. To check, use LM Studio's model settings panel, `lms ps`, or `GET /api/v1/models`.
4. **GitHub branch protection.** This cannot be read from the repo. Check the repo's Settings, under Branches.
5. **Data volumes.** Table sizes set what an export or backup costs. To check:
   ```sql
   select relname, pg_size_pretty(pg_total_relation_size(oid)) from pg_class
   where relkind = 'r' and relnamespace = 'public'::regnamespace;
   ```
6. **The values in `.env`.** They were deliberately not read, so the live `REASONING_EFFORT` and the read-only role's name are unverified.

---

## 4. Findings Table

Severity follows the rubric. Finding 1 is rated above its High definition, which covers frequent failures, because it is a total outage of the core feature on the default branch.

| # | Dim | Finding | Location | Evidence | Severity | Effort | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | B | **Every Tally and Forge turn raises `NameError` on `main`.** Merge `52f975e` dropped `last = step == MAX_STEPS - 1` and `model = answer_only() if last else with_tools`, and `run()` uses both names. The router, declines and refusals still work. Every question answered from records fails with an `error` event. | `agents/loop.py:227, 241, 245, 282` | Observed. `ruff check --select F821` reports `model` undefined at 241 and 245 and `last` at 282, and `with_tools` unused at 227. `git diff 136b318 52f975e -- agents/loop.py` shows the two lines removed. `4c21485` is identical to the merge for this file. | Critical | S | High |
| 2 | A | **Nothing stops a broken change from reaching `main`.** PRs #4 to #6 merged an undefined name, a rewording of the prompt and caveat text, and a model-size change. No CI exists. The hook only warns, and GitHub-side merges do not run it. No eval result exists for `136b318`, `52f975e` or `4c21485`. | `.github/` (absent); `.githooks/pre-commit:22-44`; `evals/results/` | Observed: no CI, the results list, and git log. Inferred: `make test` is red (Gap 1). | High | S–M | High |
| 3 | A | **The eval runner drops any case that raises.** `runner.run` does not catch exceptions, and `test_case` appends to `_RESULTS` only after `run` returns. A crash therefore removes the case from the committed totals instead of counting it as 0/3. With finding 1 in place, the JSON would hold only the 37 routing and 13 refusal cases, and could read 50/50. | `evals/runner.py:220-233`, `284-290`; `evals/test_cases.py:70-79` | Observed code path. Inferred outcome. | High | S | High |
| 4 | B | **The tool-free final step has no output cap.** `answer_only()` builds `chat_model()` without `max_tokens`, so rule 7's token cap does not bind the step meant to end a turn. Restoring finding 1's lines as they were in `136b318` keeps this gap. | `agents/loop.py:184`, `187-191` | Observed | Medium | S | High |
| 5 | B | **The model size limit was raised to 7.5 GiB as a "PoC" and merged.** The README, plan and status doc still say 6 GiB. Settings now offers a 7.04 GiB 12B model, against CLAUDE.md's limit of about 8B at Q4. | `model_choice.py:64-66`; `README.md:164`, `449`; `tests/test_model_choice.py:121`, `168` | Observed. Inferred that the two tests fail. | Medium | S | High |
| 6 | B | **The 8,192-token window is enforced only when a model is switched.** Startup applies a stored model preference without checking that LM Studio has the model or what context it is loaded at. LM Studio reloads idle models with its saved per-model settings. Nothing compares a step's reported `input_tokens` with the window. | `model_choice.py:186`; `api/main.py:51`; `agents/loop.py:256` | Observed: no check exists. Inferred impact, which depends on LM Studio's overflow policy (Gap 3). | Medium | S–M | Medium |
| 7 | C | **There is no export in an open format.** The only copy is `pg_dump --format=custom`, which only `pg_restore` into Postgres can read. Hand-entered balances, workouts and weigh-ins, threads, preferences and the model log exist nowhere else. | `scripts/backup.py:81`; `README.md:517` | Observed | Medium | M | High |
| 8 | C | **The documented restore leaves the tools unable to connect on a new cluster.** `pg_dump` dumps one database and no roles. Only migration `fa7860f9535d` creates the read-only role, and `alembic upgrade head` will not re-run it on a restored database already at head. The restore instructions leave this step out, and no test restores a dump. | `scripts/backup.py:18-21`; `README.md:521-522`; `migrations/versions/fa7860f9535d_read_only_role.py:65-77`; `tests/test_backup.py` | Inferred from documented `pg_dump` behaviour | Medium | S | High |
| 9 | A/C | **Stored answers do not record which model or prompt produced them.** `message` carries agent, confidence, ungrounded figures and detail level, but no model id and no prompt hash. The model log does carry the model, but it is swept after 90 days while threads stay a year. The model can also change at runtime. | `db/models.py:331-356`, `400`; `history/store.py:161-166`; `llm.py:63-66` | Observed | Medium | S–M | High |
| 10 | A | **The grounding check skips figures written without `$`, a weight unit or `%`.** "Rose 38,250 dollars" or "about 38 thousand" passes unflagged, in chat and in the evals. | `agents/grounding.py:42-46`, `77-83` | Observed regex. Inferred frequency, since the prompts tell the model to copy `$` figures. | Medium | S | Medium |
| 11 | B | **Manual workout entry is not idempotent.** Each POST inserts a new workout. A double submit or a retried request stores the session twice, and the date then appears twice in lift progression. | `ingest/training.py:158-164`; `api/routes/training.py:117` | Observed | Low | S | High |
| 12 | B | **The routing call has no `max_tokens` and runs outside the 180-second turn deadline.** An unbounded reply can run to the 120-second timeout. The client retries once, and an unreadable reply makes the router try again. | `steward/router.py:251-255`; `llm.py:80`, `116` | Observed. Inferred worst case. | Low | S | Medium |
| 13 | A | **The re-measure hook does not watch `tools/finance.py`.** That file writes the caveat sentences the model repeats verbatim, and `136b318` changed them. | `.githooks/pre-commit:22`; `tools/finance.py:90-140` | Observed | Low | S | High |
| 14 | A | **The eval fingerprint hashes only each specialist's first-step request.** It skips the tool-free final step and the title call, so a change to either does not change the recorded hashes. | `evals/fingerprint.py:53-71` | Observed | Low | S | High |
| 15 | C | **The SearXNG image is unpinned** (`searxng/searxng:latest`). | `docker-compose.yml:30` | Observed | Low | S | High |
| 16 | C | **Two columns are never written:** `thread.archived_at` and `message.token_count`. An export would carry always-null fields that look meaningful. | `db/models.py:311`, `340` | Observed: grep finds no writer | Low | S | High |
| 17 | C | **Six tables use BIGINT identity keys, while the rest use UUIDs.** Merging two databases would collide on these keys. Natural unique keys cover all of them except `search_audit`. | `db/models.py:94, 153, 182, 215, 269, 451` | Observed | Low | L | High |
| 18 | A | **The status doc is stale.** It lists `turn-limits` and `carry-forward` as unmerged and the caveat work as uncommitted, but all three are on `main`. | `docs/project-status.md:3`, `63-65` | Observed | Low | S | High |

---

## 5. Data Store Inventory

| Store | Contents | Format | Schema defined? | Export path | Lock-in risk |
|---|---|---|---|---|---|
| `hearth`: `account`, `balance_snapshot`, `holding_snapshot` | Labelled accounts, balances and positions | Postgres rows, NUMERIC | Yes. `db/models.py` and 9 Alembic migrations, with up/down and models-match-migrations tests. | `pg_dump` only | L on Postgres, M elsewhere |
| `hearth`: `import_batch`, `import_row` | File hash, file name, normalizer name, every raw CSV row | JSONB keyed by header | Yes | `pg_dump`. The CSVs in `HEARTH_DATA_DIR` can rebuild them. | L |
| `hearth`: `workout`, `workout_set`, `body_metric` | Hand-entered sessions and weigh-ins, imported weight history | Rows | Yes | `pg_dump` only. Hand entries exist nowhere else. | M |
| `hearth`: `thread`, `message` | Questions, answers, tool calls with results, ungrounded flags | Rows and JSONB, with a GIN `tsvector` index | Yes | `pg_dump`. `GET /api/threads/{id}` returns one thread at a time. | M |
| `hearth`: `model_log` | Every model request body and response, and every tool run | JSONB | Yes | `pg_dump`. `GET /api/model-log/...` returns one run at a time. | M (90-day retention) |
| `hearth`: `preference` | Default answer length, retention periods, chosen chat model | JSONB values | Yes | `pg_dump` | L |
| `hearth`: `search_audit` | Errand queries, allowed or refused | Rows | Yes | `pg_dump`, or `GET /api/search-audit` | L |
| `hearth`: `snapshot_coverage` view; read-only role | Derived coverage; a cluster-level login role | SQL view; Postgres role | Yes, in migrations. The role is not in `pg_dump`. | Recreated only by a migration (finding 8) | M |
| `hearth_test`, `hearth_eval` | The fixture, rebuilt on every run | Postgres | Yes | Not needed | L |
| `HEARTH_DATA_DIR` | Fidelity positions CSVs and the weight-history CSV | CSV | Header layout pinned in the normalizers | They are the source | L |
| Backups (`%LOCALAPPDATA%\Hearth\backups`) | The whole database; the newest 14 are kept | `pg_dump` custom format | n/a | `pg_restore` | M |
| `evals/results/*.json` | Pass rates, model, package versions, prompt hashes | JSON in git | Implicit, in `runner.record` | git | L |
| `prompts/*.md` | Agent, router and title prompts | Markdown in git | n/a | git | L |
| LM Studio (outside the repo) | Model weights; per-model context and overflow settings | LM Studio config | No | None | M (Gap 3) |
| Browser `localStorage` | Panel state and theme | JSON | n/a | None needed | L |
| Process memory | Chosen model, SearXNG probe cache, prompt cache | Memory | n/a | Rebuilt from `preference` at startup | L |

No LangGraph checkpointer is configured, so no state is owned by the framework. There are no vectors yet, since Phase 10 is blocked. `EMBEDDING_MODEL` is pinned in env and documented in the README, as rebuilding an index will need.

---

## 6. Migration Walkthrough

### To a new host, keeping Postgres 17

| # | Step | Status |
|---|---|---|
| 1 | On the old host, run `make backup`. It writes a custom-format dump outside the repo. | Easy |
| 2 | Install Python 3.12, Node, Postgres 17 (binary zip or Docker) and LM Studio. | Manual, documented in the README's Prerequisites |
| 3 | Clone, copy `.env.example` to `.env`, and fill in the URLs, model ids and `HEARTH_DATA_DIR`. `make install` applies `requirements.lock`. | Manual, documented |
| 4 | **Create the read-only role named in `DATABASE_URL_RO` before restoring.** Without it, `pg_restore` reports errors on the GRANTs and every tool call fails to connect. | Manual and **undocumented** (finding 8) |
| 5 | Run `createdb`, then `pg_restore --clean --if-exists`. `alembic current` should then print the head revision. | Easy |
| 6 | If the role was created after the restore, re-run the grant block from migration `fa7860f9535d` by hand. Do not downgrade past it, because that would drop every later table. | Manual |
| 7 | Copy `HEARTH_DATA_DIR`. This is optional, because `import_row` holds every raw row. | Easy |
| 8 | In LM Studio, download the `CHAT_MODEL` key and set its context to 8,192. The quantization behind the key is not recorded, so whether the weights match the old host is unknown. | Manual |
| 9 | Check the stored chat-model preference. Startup applies it without checking that LM Studio has the model (`api/main.py:51`), so a missing model fails every turn until it is reset in Settings. | Manual |
| 10 | Run `make test`, then `make eval`. Compare the prompt hashes and package versions with the last recorded result. That comparison is the acceptance test for the move. | Easy |

### To a different database engine

| Item | Status |
|---|---|
| Tables, keys and constraints. Alembic and the SQLAlchemy models are explicit. `gen_random_uuid()` defaults and JSONB columns need replacing. | Manual |
| Data out of Postgres. There is no exporter, so this needs per-table `COPY` or a new JSONL exporter (finding 7). | Manual |
| Thread search uses `websearch_to_tsquery`, `ts_headline` and a GIN index on `to_tsvector` (`history/store.py:186-209`). | **Blocked** until it is rewritten for the new engine's full-text search |
| Rule 2's read-only role depends on database roles and GRANTs. | **Blocked** on engines without roles, such as SQLite, unless replaced by a read-only connection mode |
| Upserts with `on_conflict_do_update` (`preferences.py`, `model_choice.py:208-214`). | Manual |
| The `snapshot_coverage` view and the tool SQL in `tools/finance.py` and `tools/fitness.py` are standard SQL. | Manual review |
| The test and eval harnesses use `DROP DATABASE ... WITH (FORCE)` (`evals/conftest.py`). | Manual |

### To a different model provider

| Item | Status |
|---|---|
| Chat through `llm.py`. Any OpenAI-compatible server works through `base_url`. | Easy |
| `reasoning_effort="none"` and `json_schema` structured output for the router and titles. Support varies by server, and both are load-bearing: the router returned empty replies with reasoning on (plan, Phase 6 addendum). | Manual. Re-measure with `make eval`. |
| `model_choice.py` loads and unloads models through LM Studio's own API. | **Blocked** for the Settings model switch without an adapter. Chat is unaffected. |
| The model log builds request bodies with langchain-openai's private payload builder (`modellog.py:70`), which `tests/test_model_log.py:131-143` pins. | Easy while staying on `ChatOpenAI` |

---

## 7. Recommendations

### Finding 1 (Critical): restore the step's model, and cap the final step

**Change.** In `agents/loop.py`, inside `run()`'s `for step in range(MAX_STEPS):` loop and straight after the deadline check, put back:

```python
last = step == MAX_STEPS - 1
model = answer_only() if last else with_tools
```

Also change `answer_only()` to `return chat_model(max_tokens=MAX_OUTPUT_TOKENS).bind()`, which fixes finding 4.

**Benefit.** Tally and Forge answer again, and every step, the last one included, is capped.

**Trade-off.** The final step can now be cut off at 4,000 tokens, like the other steps. The measured maximum was 2,399.

**Verify.** Run:

```
.venv/Scripts/ruff check --select F821,F841 agents/
make test
make eval          # then commit evals/results/<sha>.json
```

Add a test that drives four steps and asserts the fourth request body has no `tools` and has `max_tokens == 4000`. `tests/test_guardrails.py` already drives four steps, so this is one more assertion there.

### Finding 2 (High): put a gate in front of `main`

**Change.** Add a pull-request workflow, required by branch protection, that runs:

- `ruff check`
- `ruff format --check`
- `mypy`
- `pytest` against a `pgvector/pgvector:pg17` service, with an env built from `.env.example`.

It needs no model and no real data. Add one more job that fails when a PR changes a watched path (`prompts/`, `agents/`, `steward/`, `tools/`, `llm.py`) without adding a file under `evals/results/`.

**Benefit.** PR #6 would have been blocked by both the undefined name and the two `model_choice` tests. The eval job turns the hook's reminder into a check.

**Trade-off.** CI runs on GitHub's machines and sees the repo and the invented fixture. CLAUDE.md applies rules 5 and 6 to the running app, not to development. If you want nothing to run off-machine, a `pre-push` hook running `make lint test` is the local alternative, but it does not cover merges made in the GitHub web UI. The eval-file job checks only that a file was added, not that it is good.

**Verify.** Open a throwaway PR that reintroduces the F821 error, and confirm the check fails.

### Finding 3 (High): record a crash as a failed run

**Change.** In `evals/runner.py` `run()`:

```python
try:
    ok, why = run_once(case, today)
except Exception as error:  # noqa: BLE001 - a crash is a failed run, and is recorded
    ok, why = False, f"raised {type(error).__name__}: {error}"
```

In `record()`, write `"cases_expected": <number of cases in cases.yaml>` into `totals`. Add `-incomplete` to the file name when fewer results than that were recorded.

**Benefit.** The committed number covers every case, and a crash shows as 0/3 with its exception named.

**Trade-off.** An infrastructure fault, such as LM Studio dying mid-run, now reads as failed cases rather than missing ones. The `raised ...` detail tells the two apart. pytest still fails the case, so the exit code does not change.

**Verify.** Add a unit test that monkeypatches `tally.answer` to raise. Assert that `runner.run(...)` returns `passed == 0` with the exception's name in `detail`.

### Medium findings, briefly

- **Finding 6.** Before each turn, check that the active model is listed and loaded at 8,192 tokens, using the same 60-second cache as `errand.available()`. Refuse the turn with a sentence if not. Separately, log a warning when a step's `input_tokens + MAX_OUTPUT_TOKENS > 8192`. The cost is one local HTTP call a minute, and more coupling to LM Studio's API.
- **Finding 7.** Add `make export`. It would write one JSONL file per table, plus a `manifest.json` holding the Alembic head, `EMBEDDING_MODEL` and the export time. Add a test that exports the fixture, loads it into an empty database and compares row counts. The cost is a second format to keep in step with the migrations.
- **Finding 8.** Move the migration's create-or-alter and grant block into a function (for example `db/roles.py: grant_readonly(conn, url)`). Call it from the migration and from a `make grant-ro` target, and add that step to the README's restore instructions. Cheaper still: run `pg_restore --list` on each new dump in `scripts/backup.py`.
- **Finding 9.** Add `model` and `prompt_hash` columns to `message`. The hash would reuse `evals.fingerprint._digest` over the first step's request without the question. The cost is one migration and a hash per turn.
- **Finding 10.** Add a pass that flags bare numbers of 100 or more with thousands separators, and "thousand"/"k" forms, that match nothing in the tool results or the question. Measure the false positives against the last eval run's answers before keeping it.
- **Finding 5.** Pick one value. Either revert to `6 * 2**30`, or keep 7.5 GiB and update CLAUDE.md, the README, the plan and the tests to match.

---

## 8. Quick Wins

Each of these takes under an hour.

1. Finding 1 and finding 4: two lines and one keyword argument.
2. Finding 5: make the code, tests and docs agree on one size limit.
3. Add `tools/finance.py` and `tools/fitness.py` to the hook's watched pattern (`.githooks/pre-commit:22`).
4. Add the read-only role step to the restore instructions in `README.md:521` and `scripts/backup.py:18`.
5. Run `pg_restore --list <dump>` after each backup, and fail the backup if it errors.
6. Pin the SearXNG image to a dated tag.
7. At startup, if LM Studio's model list loads and does not include the stored chat-model preference, log a warning and fall back to `.env`'s model. If the list does not load, change nothing.
8. Update `docs/project-status.md` to show the branches as merged.

---

## 9. Keep Doing

- **Rule 1, end to end.** Tools hand the model rendered, unit-bearing figures and finished caveat sentences. The grounding check flags any figure no tool produced, and the evals reuse that same check.
- **Guardrails as code, with tests that try to break them.** These include:
  - the pre-flight check before routing and again in each specialist;
  - the hop cap as a graph edge;
  - the step, token and time caps;
  - the read-only role, proven unable to write;
  - the Host-header check.
- **Evals that are honest about noise.** They use assertions rather than judges, pass rates over three runs, and a disposable fixture database. Each result records request-byte hashes and package versions. Close calls are settled with interleaved A/B runs. The suite has been shown to move, 3/3 to 0/3 and back.
- **Ingestion built for recovery.** Imports are idempotent on SHA-256. Raw rows are stored first and normalized from storage. A savepoint makes each import all-or-nothing, headers must match exactly, and `renormalize()` can rebuild snapshots from raw rows alone.
- **No persistence owned by a framework.** Threads, messages and the model log are your own typed tables, not checkpointer blobs.
- **Schema hygiene.** Money is NUMERIC, timestamps are timezone-aware through the type map, and Alembic uses a naming convention. Tests cover migrating down as well as up, and check that the models match the migrations.
- **One LLM factory.** Model names come from env, every URL is checked as local, and the factory refuses to build with telemetry on.
- **A model log that is the real request.** It records the body the client built, and it is kept in a `finally` even when the client disconnects.

---

## 10. Open Questions for me

1. Is the GPU host serving `main` at `4c21485`? If so, finance and training questions have failed since PR #6 merged.
2. Do you want CI on GitHub, running fixture data only, or a gate that stays on your machine? The answer decides the shape of finding 2's fix.
3. Is the 7.5 GiB limit meant to stay? If `gemma-4-12b` earned its place, CLAUDE.md's hardware constraint should change with it.
4. What does "a different database" mean for you: another Postgres host, or another engine? A JSONL exporter is worth an evening only for the second.
5. What context-overflow policy is LM Studio set to for the chat model, and which quantization is loaded under `nvidia/nemotron-3-nano-4b`?
6. Should each stored answer record its model and prompt hash (finding 9), given that the model log is kept for only 90 days?

### The one missing fact that would most change the top recommendation

**Which commit the GPU host is actually running.**

- If it is `4c21485`, the app is down for every finance and training question, and finding 1 is an outage fix to make today.
- If it is a local tree that differs from `main`, then the system being used and measured is not the one in git. The top action becomes reconciling the two before any further eval run, because every recorded pass rate is tied to a commit.

Running `make test` on `main` is the quickest way to confirm the rest of the picture.
