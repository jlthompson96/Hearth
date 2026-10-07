# Implementation plan: GenAI review findings

**Source:** [genai-review-2026-10-07.md](genai-review-2026-10-07.md) · **Written:** 2026-10-07 · **Base:** `main` @ `4c21485`

This plan turns the review's findings into nine pull requests, ordered so that `main` works again first and every later change is measured on a working base. Effort is in evenings, like `docs/plan.md`, and is the least reliable part of this document.

The repository's own conventions apply throughout:

- one branch and one PR per package;
- tests before code in the data and tool layers;
- `make lint` and `make test` before every merge;
- `make eval` on the host whenever a model-facing file changes, with `evals/results/<sha>.json` committed in a follow-up "Eval result for <sha>" commit;
- real data never enters the repository, and `.env` is never read into a session.

---

## Step 0: two facts to settle before any code

| Check | How | Why it matters |
|---|---|---|
| Which commit the GPU host is running | `git rev-parse --short HEAD` and `git status --short` in the host's checkout | If the host runs `4c21485`, finance and training questions are failing now, and PR 1 is an outage fix. If the host runs a local tree that differs from `main`, reconcile the two first. Otherwise every recorded pass rate describes code that is not in git. |
| What `make test` reports on `main` | `make test` on the host, saved to a scratch file | The review predicts failures in `tests/test_guardrails.py`, `tests/test_model_log.py` and `tests/test_model_choice.py`. PR 1 must clear every failure on this list, including any the review did not predict. |

---

## Decisions this plan assumes

Each decision changes the work. The default is what this plan builds. Say so before the PR in question if you want the alternative.

| # | Decision | Default in this plan | Alternative and what changes |
|---|---|---|---|
| D1 | Chat model size limit (finding 5) | **Revert to 6 GiB.** It matches CLAUDE.md, the README and the existing tests. | Keep 7.5 GiB. PR 1 then updates two tests, the README, `docs/plan.md`, `docs/project-status.md` and CLAUDE.md's hardware section instead. |
| D2 | Merge gate (finding 2) | **GitHub Actions on the invented fixture**, with branch protection on `main`. | A local `pre-push` hook running `make lint test`. It does not cover merges made in GitHub's web UI. |
| D3 | Read-only role for restores (finding 8) | **Leave migration `fa7860f9535d` frozen.** A new `db/roles.py` repeats its statements, and a test proves both grant the same privileges. | Have the migration call `db/roles.py`. That removes the duplication, but an old migration then changes whenever the module does. |
| D4 | Export (finding 7) | **JSONL per table plus a manifest, with a tested load back into Postgres.** | A documented `COPY ... TO CSV` recipe only. Cheaper, but untested and with no manifest. |
| D5 | Unused columns (finding 16) | **Drop `thread.archived_at` and `message.token_count`.** | Start writing them instead. Token counts already live in `model_log`. |
| D6 | Wrong context length at turn time (finding 6) | **Refuse the turn** with a sentence pointing to the reload on the Settings screen. Load the model at 8,192 only when it is not loaded at all. | Reload automatically, which unloads an instance you may have loaded on purpose. |
| D7 | Unit-less figures (finding 10) | **Report-only for one measured eval run, then enforce.** | Enforce immediately and accept a possible dip in the grounded cases. |

---

## Sequence

| PR | Branch | Findings | Eval run? | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | `fix-agent-loop` | 1, 4, 5 | Yes | 0.5 | Step 0 |
| 2 | `eval-recording` | 3, 14 | Yes, to record the new hashes | 1 | PR 1 |
| 3 | `merge-gate` | 2, 13 | No | 1 | PR 1 |
| 4 | `turn-guards` | 6, 12, quick win 7 | Yes | 1.5 | PR 2 |
| 5 | `portable-data` | 7, 8, 16, quick wins 4 and 5 | No | 2 | PR 3 |
| 6 | `answer-provenance` | 9 | No; the request bytes must not change | 1 | PRs 2 and 5 |
| 7 | `grounding-unitless` | 10 | Yes, twice | 1 | PR 2 |
| 8 | `workout-idempotency` | 11 | No | 0.5 | PR 3 |
| 9 | `housekeeping` | 15, 18 | No | 0.25 | Last |

**Total:** about 9 evenings, plus five eval runs of about 27 minutes each.

**Migration order.** PRs 5 and 6 each add an Alembic migration, and today's head is `b7d2e9f41c63`. Merge PR 5 first. PR 6's migration then takes PR 5's revision as its `down_revision`, so there are never two heads.

**Deferred.** Finding 17, the BIGINT keys, is not scheduled. PR 5's export preserves every id, and merging two databases is not a plan. Revisit it if that changes.

**Not in this plan.** These items are already tracked in `docs/project-status.md` section 9, and none of them blocks anything here:

- the value-aware egress check;
- the hop cap's off-by-one;
- `ingest` importing from `scripts`;
- generated SSE event types;
- Forge restating remembered figures;
- dropped cents in the detailed positions answer.

---

## PR 1: `fix-agent-loop`. Restore the loop, cap the last step, settle the size limit

**Goal.** Tally and Forge answer again, every step is capped, and `make test` is green.

**Steps**

1. **Test first.** In `tests/test_guardrails.py`, add `test_every_step_including_the_last_is_capped_in_tokens`. It records the kwargs of every `chat_model` call while a model like `_Relentless` calls a tool on every step, then asserts:
   - there were two builds, one from `bound()` and one from `answer_only()`;
   - both carried `max_tokens == loop.MAX_OUTPUT_TOKENS`.

   Today the test fails with `NameError`. After step 2 alone, it fails on the second build.
2. **`agents/loop.py`.** In `run()`, straight after the deadline check, restore:
   ```python
   last = step == MAX_STEPS - 1
   model = answer_only() if last else with_tools
   ```
   Then change `answer_only()` to return `chat_model(max_tokens=MAX_OUTPUT_TOKENS).bind()`, and update its docstring to say the cap applies.
3. **`model_choice.py:66`.** Under D1, set this back to `MAX_MODEL_BYTES = 6 * 2**30` and delete the "PoC" comment. If gemma-4-12b was tried, add one sentence to `docs/plan.md` saying what it showed.
4. **Lint and test:**
   ```
   .venv/Scripts/ruff check --select F821,F841 agents/
   make lint
   make test
   ```
   Fix anything else that came in with `136b318` in this PR. Step 0 says what to expect.
5. **Eval.** Run `make eval` on the host and commit the result. This is also the first measurement of `136b318`'s rewording of the caveat sentences and `prompts/tally.md`, so compare it with `7bb7ebb` (70/73).
   - If a caveat case drops, settle it with an interleaved A/B between the old and new wording, as the project has done before.
   - Do not attribute the drop to the loop fix without that A/B.
6. **Smoke check.** Restart the backend on the host and ask one finance question and one training question. Open each run in the Model log: it should show a route, a tool step and an answer step.

**Done when.** `make test` is green, and the new results file names the fix commit and covers all 73 cases.

**Trade-off.** The final step can now be cut off at 4,000 tokens, like every other step. The measured maximum was 2,399.

---

## PR 2: `eval-recording`. A crash is a failed run, and every request is fingerprinted

**Goal.** The committed eval number covers every case, and the fingerprint covers every request a turn can send.

**Steps**

1. **Tests first.** Add `tests/test_eval_runner.py`. It needs no model and no database. Monkeypatch `runner.RESULTS` to `tmp_path` and `runner.head_sha` to a fixed string, then check:
   - a specialist that raises gives `Result.passed == 0`, with `raised RuntimeError` in `detail`;
   - `record(..., expected=N)` writes `totals.cases_expected == N`;
   - the file name ends in `-incomplete.json` when fewer than N results are given, and does not otherwise.
2. **`evals/runner.py`.**
   - Wrap `run_once` inside `run()`:
     ```python
     try:
         ok, why = run_once(case, today)
     except Exception as error:  # noqa: BLE001 - a crash is a failed run, and is recorded
         ok, why = False, f"raised {type(error).__name__}: {error}"
     ```
   - Give `record()` a keyword-only `expected: int`. Write it into `totals`, and add `-incomplete` to the file name when `len(results) < expected`.
3. **`evals/test_cases.py`.** Pass `expected=len(CASES)`. A run filtered with `-k` will now be labelled incomplete rather than look like a baseline.
4. **`history/titles.py`.** Extract `title_request(question) -> (model, messages)` and use it in `for_question()`. This mirrors `routing_request`, so the fingerprint and the call cannot describe different requests.
5. **`evals/fingerprint.py`.** Add two kinds of hash:
   - `"{name}/{detail}/final"`, hashing `request_body(loop.answer_only(), conversation, stream=True)`;
   - `"title"`, hashing `title_request(PLACEHOLDER)`.

   Existing keys keep their values, so earlier results stay comparable.
6. **`tests/test_fingerprint.py`.** Update the expected key set (line 47) and the specialist-edit test: an edit to `prompts/tally.md` now changes the `tally/*/final` hashes too.
7. **`evals/README.md`.** Document `cases_expected`, the `-incomplete` suffix and the new hash keys.

**Verify.** `make test`, then `python -m evals.fingerprint`. Every old key's value must match its value on `main`.

**Measure.** One `make eval`, so a result exists with the new keys. You can skip this if PR 4 follows within days, because PR 4 runs one anyway.

**Trade-off.** An infrastructure fault, such as LM Studio dying mid-run, now reads as failed cases rather than missing ones. The `raised ...` detail tells the two apart.

---

## PR 3: `merge-gate`. Nothing reaches `main` without lint and tests

**Goal.** A PR like #6 cannot merge.

**Steps**

1. **Make `make lint` pass on `main` as it stands after PR 1.** It was not run before the last three merges, so expect a few findings beyond F821. Fix them in this PR.
2. **`tests/conftest.py`.** When `HEARTH_REQUIRE_DB=1` is set, the two `pytest.skip(...)` calls for missing configuration and unreachable Postgres become `pytest.fail(...)`. Without this, a misconfigured CI database skips every database test and reports green.
   - Put the choice in a small helper, `_unavailable(reason)`.
   - Unit-test the helper both ways.
3. **`.githooks/watched-paths`.** This is a new file, with one regex per line: `^prompts/`, `^agents/`, `^steward/`, `^tools/` and `^llm\.py$`.
   - `^tools/` replaces `tools/bindings.py` and fixes finding 13: `tools/finance.py` writes the caveat sentences the model repeats.
   - `.githooks/pre-commit:22` reads it with `grep -E -f .githooks/watched-paths`.
   - The Makefile's `hooks` message is updated to match.
4. **`.github/workflows/check.yml`.** Pin the actions to commit SHAs when you add them.
   ```yaml
   name: check
   on:
     pull_request: { branches: [main] }
     push: { branches: [main] }
   jobs:
     check:
       runs-on: ubuntu-latest
       services:
         postgres:
           image: pgvector/pgvector:pg17
           env: { POSTGRES_USER: hearth, POSTGRES_PASSWORD: ci-only, POSTGRES_DB: hearth }
           ports: ["5432:5432"]
           options: >-
             --health-cmd "pg_isready -U hearth" --health-interval 5s --health-retries 12
       env:
         DATABASE_URL: postgresql+psycopg://hearth:ci-only@localhost:5432/hearth
         DATABASE_URL_RO: postgresql+psycopg://hearth_ro:ci-only@localhost:5432/hearth
         CHAT_MODEL: ci-no-model
         EMBEDDING_MODEL: ci-no-model
         LANGCHAIN_TRACING_V2: "false"
         LANGSMITH_TRACING: "false"
         HEARTH_REQUIRE_DB: "1"
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.12" }
         - uses: actions/setup-node@v4
           with: { node-version: "22" }
         - run: make PYTHON=python lint
         - run: make PYTHON=python test

     eval-recorded:
       if: >-
         github.event_name == 'pull_request' &&
         !contains(github.event.pull_request.labels.*.name, 'no-eval-needed')
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
           with: { fetch-depth: 0 }
         - run: |
             base="origin/${{ github.base_ref }}"
             if git diff --name-only "$base"...HEAD | grep -E -q -f .githooks/watched-paths; then
               git diff --name-only --diff-filter=A "$base"...HEAD \
                 | grep -E '^evals/results/[^/]+\.json$' | grep -v -- '-dirty' | grep -q . \
                 || { echo "Model-facing files changed with no recorded eval run."; exit 1; }
             fi
   ```
5. **Branch protection, in GitHub's settings.** Protect `main`, require `check` and `eval-recorded`, and require PRs. This is the step only you can take.

**Verify.** Open two throwaway PRs:

- one that reintroduces the undefined `model` in `agents/loop.py`, which `check` must fail;
- one that edits `prompts/forge.md` with no results file, which `eval-recorded` must fail.

Close both.

**Trade-offs**

- The workflow runs on GitHub's machines. It sees the repo and the invented fixture only, never `.env` or real data. CLAUDE.md scopes rules 5 and 6 to the running app.
- `eval-recorded` checks that a run was recorded, not that it was good. It is a nudge with teeth, not proof. The `no-eval-needed` label is the escape hatch for a refactor that changes no request bytes; confirm that with `python -m evals.fingerprint` first.

---

## PR 4: `turn-guards`. The window, the router and the stored model choice

**Goal.** Every turn runs at the 8,192-token window its budgets assume, and no model call is unbounded.

**Steps**

1. **Move `CONTEXT_TOKENS = 8192` into `llm.py`.** `model_choice.py` re-exports it, so `api/routes/settings.py` and the tests are unchanged.
2. **Tests first.** Add `tests/test_context_window.py`, reusing `FakeLMStudio` from `tests/test_model_choice.py`. Cover each case:
   - Loaded at 8,192: the turn proceeds.
   - Loaded at another length: the turn is refused before any model call, and the message names the Settings reload (D6).
   - Not loaded: one load at 8,192 is requested, then the turn proceeds.
   - LM Studio's native API unreachable: the turn proceeds, a warning is logged once, and the answer is cached for 60 seconds.
   - A specialist step whose reported `input_tokens + MAX_OUTPUT_TOKENS > CONTEXT_TOKENS` gets a Model log `error` saying it went over the context budget.
   - The routing request carries `max_tokens == ROUTER_MAX_TOKENS`.
   - At startup, a stored chat-model preference that LM Studio's catalog does not list leaves `.env`'s model in effect and logs a warning. The stored preference is kept, because the model may still be downloading.
3. **`model_choice.ensure_window(key)`.** This is the check, cached for 60 seconds like `errand.available()`. Call it from:
   - `steward/graph.py` `answer()`, before routing, because the router is a model call too;
   - `agents/loop.py` `run()`, for callers that name a specialist directly, including the evals.

   The evals then also confirm they were measured at 8,192. Add an autouse fixture to `tests/conftest.py` that stubs the check, as it already does for `errand.available`, so `make test` makes no network calls.
4. **`agents/loop.py`.** After each step, compare `tokens(gathered)` with the budget and set the step's `LogEntry.error` when it is over.
5. **`api/main.py` lifespan.** Validate `model_choice.chosen(conn)` against `model_choice.catalog()` only when the catalog loads. If it does not load, change nothing.
6. **Router limits, measured before they are set.**
   - On the host, run:
     ```sql
     select max(output_tokens),
            percentile_cont(0.99) within group (order by output_tokens)
     from model_log where kind = 'route';
     ```
   - Set `ROUTER_MAX_TOKENS` to at least 64, and at least four times the observed maximum.
   - Pass it, with `timeout=30`, in `routing_request` (`steward/router.py:251`).
   - Write the measurement into the constant's comment, as the other caps do.
   - A truncated reply is already handled as unreadable, because `LengthFinishReasonError` is in `_UNREADABLE`.
7. **README.** Add a Guardrails row for the window check.

**Measure.** Run `make eval`. The router's hash changes because `max_tokens` is in the body, so all 37 routing cases must hold. If any routing case moves, run an interleaved A/B with and without the cap before merging.

**Trade-offs**

- More code depends on LM Studio's native API, though it stays inside `llm.py` and `model_choice.py`.
- The first turn after LM Studio idles a model out may pay a load of about 9 seconds. Its own just-in-time load costs the same, but at whatever length LM Studio had saved.

---

## PR 5: `portable-data`. Restore that works, export that is open, a cleaner schema

**Goal.** Moving to a new host is a tested command. Moving to another engine starts from documented JSONL instead of a binary dump.

**Steps**

1. **One inside-the-repo check.** Extract `refuse_inside_repo(path, label)` from the duplicated logic in `ingest/datadir.py:46` and `scripts/backup.py:51`. Use it in both, and in steps 3 and 5.
2. **`db/roles.py` with `grant_readonly(conn, role, password)`.** Under D3, this repeats `fa7860f9535d`'s statements: create or alter the login role, then CONNECT, USAGE, SELECT on all tables, default privileges, and REVOKE CREATE.
   - Test first, in `tests/test_roles.py`. Use a fresh role named `<ro>_roles_test` on `hearth_test`. Reuse `tests/test_readonly_role.py`'s statements to show it can read every table and the view and can write nothing.
   - Assert that its rows in `information_schema.role_table_grants` match the migration-created test role's. Drop the role afterwards.
   - Expose it as `make grant-ro`.
3. **`scripts/restore.py` and `make restore DUMP=<file>`.**
   - Refuse a target database that already holds tables.
   - Run `createdb` if needed, then `pg_restore --no-owner --no-acl --exit-on-error -d <db> <file>`.
   - Run `grant_readonly` from `DATABASE_URL_RO`, then print `alembic current`.
   - Find binaries through `PGBIN`, and pass the password in the environment, as `backup.py` does.
4. **`scripts/backup.py`.** After `pg_dump`, run `pg_restore --list <file>`. On failure, delete the file and raise `BackupError`. Add a test in the existing monkeypatched-`subprocess.run` style.
5. **`scripts/export.py`, with `make export` and `make load-export DIR=<dir>`.**
   - `export(conn, out_dir)` reads through the **read-only** role, so an export cannot write. It writes one `<table>.jsonl` per table in `Base.metadata.sorted_tables` order, rows ordered by primary key.
   - Values are written as follows: Decimal as a string (exact); date and datetime as ISO 8601 with offset; UUID as a string; JSONB nested as-is.
   - `manifest.json` holds `format: 1`, `exported_at` (UTC), `alembic_revision`, per-table `rows` and `sha256`, `chat_model`, `embedding_model` and the git SHA.
   - `load(conn, in_dir)` refuses unless the target is at the manifest's Alembic revision and every exported table is empty. It inserts in the same order, resets each identity sequence with `setval(pg_get_serial_sequence(...), max(id))`, then re-checks counts and checksums.
   - The destination is `HEARTH_EXPORT_DIR`, or `%LOCALAPPDATA%\Hearth\exports\hearth-<stamp>`, never inside the repo. Add `# HEARTH_EXPORT_DIR=` to `.env.example`.
   - Test first, in `tests/test_export.py`, inside the rolled-back `conn` fixture. Seed the fixture plus one thread, message, model-log row and workout. Export to `tmp_path`, delete every row in reverse order, load, and compare counts and checksums. Assert that `38250.00` round-trips exactly, that timestamps keep their offset, and that the export refuses a folder inside the repo.
6. **Restore round trip,** in `tests/test_restore.py`. Skip with a stated reason when `pg_dump` or `pg_restore` is not found.
   - Create `hearth_restore_src`, migrate, seed and commit, then dump it.
   - Restore into `hearth_restore_dst`, with its own read-only role.
   - Read the net worth trend through that role and assert `$38,250.00`.
   - Drop both databases and the role.
   - CI needs a version 17 client: add a step to `check.yml` that installs `postgresql-client-17` from the PGDG apt repository.
7. **Migration `<rev>_drop_unused_columns`,** with `down_revision = "b7d2e9f41c63"`. It drops `thread.archived_at` and `message.token_count`; its downgrade re-adds both as nullable. Remove them from `db/models.py`. `tests/test_migrations.py` covers both directions and the agreement between models and migrations.
8. **README.** Rewrite the "Backing up" section around `make backup`, `make restore` and `make export`. Name the role step explicitly, and add `make restore` and `make export` to Commands.

**Verify.** `make test`. Then, on the host:

1. Run `make backup`.
2. Run `make restore` into a scratch database.
3. Point a second backend at it and ask one question.
4. Run `make export` and look at the manifest.

**Trade-offs**

- The export is a second format to keep in step with the migrations. The manifest's revision makes a mismatched load refuse rather than misread.
- Export files hold real balances as plain text. They live outside the repo, like the dumps, and carry the same exposure.
- `db/roles.py` repeats about 15 lines of the migration. The parity test is what keeps them equal.

---

## PR 6: `answer-provenance`. Every stored answer names its model and prompt

**Goal.** An answer from six months ago can be matched to the model and prompt that produced it, and to the eval run that measured that prompt.

**Steps**

1. **Shared hashing, with no change in value.** Move `evals/fingerprint.py`'s `_digest` into `modellog.py` as `digest(body)`. Move the specialist request builder into `agents/requests.py` as `specialist_request(name, detail, day) -> (model, conversation)`, and have `evals/fingerprint.py` call it.

   Check that `python -m evals.fingerprint` prints identical values before and after the move. If any value changes, the refactor is wrong.
2. **A date-free hash.** The system prompt contains today's date, so a raw hash would change daily. Add `HASH_DAY = dt.date(2000, 1, 1)` and an `lru_cache`d `answer_hash(name, detail)`, which is `digest(request_body(*specialist_request(name, detail, HASH_DAY)))`.

   Record the same values in each eval result under a new `answer_hashes` key, so a stored hash can be looked up in `evals/results/` directly.
3. **Migration `<rev>_answer_provenance`,** with its `down_revision` set to PR 5's migration. It adds `message.model` and `message.prompt_hash`, both TEXT and nullable.
4. **`history/store.py`.** Add the two kwargs to `add_message`, and the two fields to `StoredMessage` and `_MESSAGE_COLUMNS`.
5. **`api/routes/chat.py` `_turn`.** For a Tally or Forge answer that was not refused:
   - `model` is the first `kind == "step"` Model log entry's `model`;
   - `prompt_hash` is `answer_hash(agent, request.detail)`.

   Declines, refusals and searches store neither.
6. **The threads API** returns both fields. Run `npm run gen:types`. Showing them in the UI is optional and not part of this PR.
7. **Tests:**
   - a stored answer carries both fields; a refusal and a decline carry neither;
   - `answer_hash` is the same on two different days;
   - `answer_hash` changes when `load_prompt("tally")` is monkeypatched to other text.

**Verify.** `make test`, and the unchanged fingerprint values from step 1. Because no request byte changes, no eval run is needed. Label the PR `no-eval-needed`.

**Trade-off.** One migration, and one cached hash per specialist and detail level per process.

---

## PR 7: `grounding-unitless`. Figures without a `$` are checked too

**Goal.** The grounding check sees "38,250 dollars", "38.2k" and a bare "38,250", and not dates, years or rep counts.

**Steps**

1. **Tests first,** in `tests/test_grounding.py`.
   - Flagged against a tool's `$38,000.00`, and grounded against a tool's `$38,250.00`: "38,250 dollars", "38.25 thousand", "38.2k" and a bare "38,250". "38.2k" is flagged even against `$38,250.00`.
   - Never flagged: "2026", "2026-07-31", "5 reps", "quantity 120".
2. **`agents/grounding.py`.** Add three patterns:
   - a dollars-word form, matched as money;
   - a scaled form (`k`, `thousand`, `m`, `million`), whose value is multiplied before comparison;
   - a bare number with thousands separators, grounded by any figure in the results or any number in the question.

   Add a `strict: bool` parameter. With `strict=False`, the new patterns are reported separately and not returned as flags.
3. **Phase A, report-only (D7).** The chat route keeps `strict=False`. The eval runner computes the strict flags too, and appends `would flag: [...]` to `Result.detail` without failing the case. Run `make eval` and read every `would flag` line.
4. **Phase B, enforce.** If phase A shows no false positives, switch both the chat route and the runner to `strict=True`. Otherwise tighten the patterns and repeat phase A. Run `make eval` and commit.

**Trade-off.** More flags under answers, and possibly more failing grounded cases. Both are the intended effect if the flagged figures really are paraphrases.

---

## PR 8: `workout-idempotency`. One session, stored once

**Goal.** A double click or a retried request cannot store a workout twice.

**Steps**

1. **Tests first,** in `tests/test_training_entry.py` and `tests/test_training_routes.py`:
   - the same `id` twice with the same content stores one workout, and the second call returns it;
   - the same `id` with different content is refused with a `Conflict`;
   - no `id` behaves as today.
2. **`api/routes/training.py`.** Add `NewWorkout.id: uuid.UUID | None = None`. A repeat answers 200 rather than 201.
3. **`ingest/training.py` `record_workout`.**
   - Accept `workout_id` and insert it as the primary key.
   - When it already exists, compare the stored session with the request: return the stored session if they are the same, and raise `Conflict` if they differ.
4. **`web/src/TrainingEntry.tsx`.**
   - Hold a `crypto.randomUUID()` per form, sent on every attempt and renewed after a success.
   - Disable the submit button while a request is in flight.
   - Run `npm run gen:types`.

**Verify.** `make test`, then double-click "record" on the host.

---

## PR 9: `housekeeping`

1. **`docker-compose.yml:30`.** Pin SearXNG by digest. On a machine with Docker, run:
   ```
   docker pull searxng/searxng:latest
   docker inspect --format '{{index .RepoDigests 0}}' searxng/searxng:latest
   ```
   Write the result as `image: searxng/searxng@sha256:...`. Phase 9 cannot run without Docker anyway, so this can wait for it.
2. **`docs/project-status.md`.** Update the branch table, the findings list and the eval history to match `main`.
3. **`docs/plan.md`.** Add an addendum dated on the day the last PR merges. It should cover what this round changed and what the eval results showed, in the style of the existing addenda.
4. **README.** Add Guardrails rows for the merge gate and the window check, and update the Commands list.

---

## Done for the whole plan

- `main` is protected, and `check` and `eval-recorded` pass on it.
- The latest results file covers all 73 cases, with no `-incomplete` suffix, and carries hashes for the final step and the title call.
- `make restore` of a fresh `make backup` yields an app whose tools can read, and `tests/test_restore.py` proves it in CI.
- `make export` writes JSONL and a manifest, and `tests/test_export.py` proves the round trip.
- Every new Tally or Forge answer stores its model and a prompt hash that can be looked up in `evals/results/`.
- `docs/project-status.md` and `docs/plan.md` describe what is on `main`.
