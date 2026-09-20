# Hearth

A local-first personal assistant. An orchestrator routes each turn to a specialist agent
working over my own data. Everything runs on my machine.

The rules that govern this codebase are in [CLAUDE.md](CLAUDE.md) — several of them are
inviolable rather than preferred. The phase plan is in [docs/plan.md](docs/plan.md).

**Status: Phase 4 complete, verified on the host.** Schema, migrations, the read-only
role, the golden fixture, the four query tools and the model connection are in place, and
the constrained-JSON exit criterion has been measured against the real model — 10/10 on
each of three runs. No agents yet — Tally arrives at Phase 5.

This is already useful without one. `get_net_worth_trend` will tell you what your net worth
did over a period and which dates it cannot vouch for; it just cannot yet be asked in
English.

## Prerequisites

- Python 3.12. On the Mac, `uv python install 3.12` provides it without sudo or Homebrew;
  make sure the shim directory (`~/.local/bin`) is on your PATH, or point the Makefile at
  it with `make PYTHON=/full/path/to/python3.12`. On the Windows host use `py install
  3.12`, which registers it with the `py` launcher the Makefile already calls — a
  uv-managed interpreter is invisible to `py -3.12`.
- GNU make. Present on the Mac; on the Windows host, `winget install ezwinports.make`
  (winget does not add it to its Links directory, so add its `bin` to PATH by hand).
- Node 20.19+ or 22.12+ (Vite 7's floor)
- Docker with Compose v2
- [LM Studio](https://lmstudio.ai) serving an OpenAI-compatible endpoint on
  `http://localhost:1234/v1`

The hardware target is an RTX 4060 Ti with **8GB of VRAM**, shared between the chat model
and the embedding model. Nothing larger than ~8B at Q4 fits. The 8,192-token context
window, not disk or RAM, is the scarce resource.

**Hearth is developed on a Mac and hosted on the RTX 4060 Ti machine.** The data layer,
ingestion and query tools are ordinary portable software and are built on either. Anything
model-dependent is only meaningful on the host: the Phase 4 smoke test, and every eval
pass rate. A pass rate measured anywhere else does not characterise the target, and
`make freeze` produces a lockfile for one machine's architecture, not both.

## Setup

```bash
cp .env.example .env     # then fill it in — see below
make install             # creates .venv, installs both toolchains
make up                  # Postgres + SearXNG, both bound to 127.0.0.1
make dev                 # infrastructure + backend + frontend
```

`make dev` serves the API on `http://localhost:8000` and the UI on
`http://localhost:5173`. The UI reaches the API through Vite's dev proxy, so the two share
an origin and there is no CORS layer to configure.

Check the backend directly:

```bash
curl http://localhost:8000/health
```

### Filling in `.env`

Most of it is local connection details. Two entries deserve attention:

- **`SEARXNG_SECRET`** — generate with `openssl rand -hex 32`. SearXNG will not start
  without it.
- **`CHAT_MODEL` and `EMBEDDING_MODEL`** — the model ids LM Studio reports. There are no
  defaults in code, deliberately: a default model name in Python is a hardcoded model
  name, and these change. The inference layer refuses to start without them; the API and
  `/health` do not depend on them.

`.env` is gitignored and is never read into an assistant session.

## What the tools do

Four functions, all pure Python over fixed parameterized queries, all running as the
read-only role:

| Function | Answers |
|---|---|
| `get_balance_history` | one account across a period |
| `get_net_worth_trend` | the total across all accounts, **with coverage** |
| `get_allocation` | holdings by symbol, with percentages, on a given date |
| `get_lift_progression` | heaviest set per session for one lift, with estimated 1RM |

The model picks the function and its arguments and receives the computed result. It never
writes SQL and never does arithmetic — an 8B model at Q4 will produce a confident sum that
is wrong by a digit, and a finance assistant that does that once is worthless afterwards.

`get_net_worth_trend` returns coverage alongside the figures, and `coverage.caveat()`
writes the qualification out as a finished sentence rather than leaving the model to
compose one from a list of dates.

## Running the model

The host is a Windows machine running LM Studio as a Windows application. There is no WSL
anywhere in the stack, which has two consequences.

`make` recipes use `$(BIN)` rather than a hardcoded `.venv/bin`, so `migrate`, `seed`,
`test` and `lint` work on both machines. `make dev` runs two servers under one shell with
`trap`/`wait`, which needs a POSIX shell — use Git Bash or MSYS2 on the host, or start
uvicorn and Vite separately.

Anything model-dependent only means something on the host. The Phase 4 exit criterion is
schema-valid structured output ten times out of ten; measured against a different model on
a different machine it measures nothing. The same goes for every eval pass rate — which is
why the table under [Models](#models) records which model produced a number.

To exercise model code while developing on the Mac, enable **Serve on Local Network** in
LM Studio and point the Mac at the host:

```
LM_STUDIO_BASE_URL=http://<host-lan-address>:1234/v1
```

Rule 5 permits the LAN, so this stays inside the rules. Results still belong to the host —
it is the same model on the same GPU, reached over a wire.

## Models

Both model names are read from the environment and never hardcoded. Assume they change.

**The embedding model is pinned.** Changing `EMBEDDING_MODEL` invalidates every vector in
the database — a different model produces a different space, and old vectors are not
comparable to new ones. Changing it means re-embedding everything, so treat it as a
migration rather than a config tweak. Of the two candidates named for Phase 10,
`nomic-embed-text` is the one present on the host, so it is the one pinned.

| Role | Env var | Value | Params |
|---|---|---|---|
| Chat | `CHAT_MODEL` | `nvidia/nemotron-3-nano-4b` | 4B |
| Embedding | `EMBEDDING_MODEL` | `text-embedding-nomic-embed-text-v1.5` | — |

**The chat model is 4B, not the ~8B the hardware budget allows.** It is the largest
instruct model on the host that leaves room for the embedding model beside it: at 8,192
context it occupies 2.84GB of the 8GB card, against 7.56GB for the next size up, which
does not fit alongside anything. The alternative that also fits is a reasoning model,
rejected because its thinking tokens are spent out of the same 8,192-token window that
retrieved chunks and conversation history already compete for.

This is a deviation from the target the rules assume, and it is recorded rather than
smoothed over: a pass rate measured at 4B is not evidence about an 8B, and moving to a
genuine 8B at Q4 means re-measuring every number in `evals/results/`.

## Commands

```bash
make dev          # docker compose up + backend + frontend
make migrate      # alembic upgrade head
make seed         # load the golden fixture dataset
make test         # pytest
make eval         # run the behavioural case file     (Phase 7)
make lint         # ruff + mypy + tsc
```

Also available: `make up` / `make down` / `make logs` for infrastructure alone, `make fmt`
to apply formatting, `make freeze` to resolve `requirements.txt`'s ranges into exact pins,
and `make clean` to remove both toolchains.

The commands whose phase has not landed fail with a message saying so rather than a
stack trace.

TypeScript types are generated from the API's OpenAPI schema, not written by hand. With
the backend running:

```bash
cd web && npm run gen:types
```

## Layout

```
api/          FastAPI app. Phase 0 serves /health.
config.py     Environment configuration, split so the API boots without a model server.
steward/      The orchestrator. Routes each turn to a specialist.      (Phase 6)
agents/       Tally (finance, Phase 5) and Forge (fitness, Phase 11).
tools/        finance.py and fitness.py — every figure an agent reports.
              Errand, the one thing that leaves the house, lands in Phase 9.
prompts/      Agent prompts as version-controlled Markdown, never inline literals.
db/           Schema and shared column types. NUMERIC throughout.
migrations/   Alembic. Tables, the snapshot_coverage view, the read-only role.
scripts/      seed.py — the golden fixture. Invented figures only.
evals/        Behavioural case file and recorded pass rates.           (Phase 7)
tests/        Pytest. Tests come before agent code in the data and tool layers.
web/          React + TypeScript + Vite.
docker/       Postgres init and SearXNG configuration.
```

## Data

`make seed` loads the golden fixture: four accounts over 2024, with coverage deliberately
uneven — the brokerage account opens in March, and one month of retirement data is missing,
as though an export skipped it. A fixture where every account has every month would let a
net worth trend look right while the coverage handling underneath it was broken. `make seed`
refuses to run if imported data is present unless passed `--force`.

Real CSVs live outside this repo, at `$HEARTH_DATA_DIR`. They are never created, copied or
pasted into the workspace. Fixtures in the repo are fake, and the golden dataset used by
`make seed` and `make eval` contains invented figures only.

Data enters through CSV import or manual entry, triggered by hand. There are no live
financial connections, and there will not be.

## What leaves the machine

Nothing, with one exception: Errand's search queries reach a self-hosted SearXNG instance,
which then queries the public web. Every such query passes `validate_search_query()` and is
logged to the `search_audit` table, with a view in the UI (Phase 9).

There is no third-party telemetry anywhere — no analytics, no error reporting,
`LANGCHAIN_TRACING_V2=false`.
