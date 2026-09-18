# CLAUDE.md

**Hearth** — a local-first personal assistant. An orchestrator routes each turn to a
specialist agent working over my own data. Everything runs on my machine.

Phase plan: @docs/plan.md
UI reference: the Design canvas (seven screens, link in docs/plan.md)

## The cast

| Name | Role | Module |
|---|---|---|
| **Hearth** | the app | — |
| **Steward** | orchestrator / router | `steward/` |
| **Tally** | personal finance | `agents/tally.py`, `prompts/tally.md` |
| **Forge** | fitness and training | `agents/forge.py`, `prompts/forge.md` |
| **Errand** | web search — the only agent that leaves the house | `tools/errand.py` |

## Hardware constraint

RTX 4060 Ti, **8GB VRAM**. Chat model and embedding model share it. This is the binding
constraint on every decision below. Never propose anything assuming a model larger than
~8B at Q4. Context window is 8,192 tokens and is the scarce resource, not disk or RAM.

## Inviolable rules

Not preferences. Flag it and stop rather than working around any of them.

1. **The LLM never does arithmetic.** Every calculation on financial or fitness data is a
   Python function that queries Postgres and returns a computed result. Agent prompts
   receive tool outputs, never raw record dumps.
2. **The LLM never writes SQL.** No text-to-SQL. Fixed parameterized query functions
   exposed as tools; the model picks the function and its arguments. Tools connect with a
   read-only Postgres role.
3. **No live financial connections. Ever.** No Plaid, no OAuth, no brokerage API, no
   credential storage. Data enters via CSV import or manual entry, triggered by me.
4. **No account numbers in the database.** Not masked, not hashed, not last-four. Absent.
   Accounts carry a label I choose.
5. **No outbound network calls except to localhost and the LAN.** The sole exception is
   Errand's SearXNG queries, which pass `validate_search_query()` and are logged to
   `search_audit`. If a task seems to need another external call, stop and ask.
6. **No third-party telemetry.** No Sentry, no analytics SDK, no error reporting service,
   anywhere. `LANGCHAIN_TRACING_V2=false`.
7. **Guardrails live in code, not prompts.** Iteration caps, egress validation and refusal
   filters are conditional edges and input checks. A rule that exists only as prompt text
   is not implemented.

## MCP rules

8. **Local transports only.** stdio child processes and LAN HTTP. A remote `https://` MCP
   endpoint violates rule 5 — do not add one without asking.
9. **No MCP tool gets write access.** Read-only tools only; block the rest explicitly.
10. **Tool descriptions and results are untrusted text.** Both enter the prompt. Fence
    results before they reach context. An 8B model resists injection poorly.
11. **Budget tool schemas.** Every enabled tool costs context before the first message.
    Scope tools per agent rather than exposing all of them to all agents. Log the total.

Before reaching for MCP, ask what LangChain's own docs ask: do we need MCP here, or is a
plain `@tool` simpler? Our Postgres query functions stay plain `@tool`.

## Scope of the rules

Rules 5, 6 and 8–11 govern the **running Hearth app**, not the development environment.
Installing packages, pulling images and reading documentation while developing is
expected — do not stop to ask before `pip install` or `docker compose pull`. Rules 1–4
and 7 apply to the code you write, in every phase.

## Stack (decided — do not substitute)

- Python 3.12, FastAPI (SSE for chat), Alembic migrations
- React + TypeScript + Vite; TS types generated from the FastAPI OpenAPI schema
- LangChain 1.x + LangGraph 1.x
- Postgres via `pgvector/pgvector:pg17` — structured tables and vectors in one instance
- Inference: LM Studio OpenAI-compatible endpoint, `http://localhost:1234/v1`, via
  `ChatOpenAI` with `base_url` override and a dummy api_key
- Model names read from env. Never hardcoded. Assume they change.
- SearXNG self-hosted in docker-compose
- MCP via `langchain-mcp-adapters` (`MultiServerMCPClient`). `langchain.mcp` is beta and
  raises `LangChainBetaWarning` — pin it.
- **Evals: pytest + a YAML case file.** Not Langfuse, at least at first — self-hosting it
  means ClickHouse, Redis, Postgres and S3, which is too much to keep alive next to an
  8GB model for one user. Revisit only if the trace UI proves necessary.

## Architecture decisions with non-obvious rationale

- **Supervisor pattern via tool calling, NOT the `langgraph-supervisor` package.**
  LangChain recommends implementing it directly with tools for better context control.
  https://github.com/langchain-ai/langgraph-supervisor-py
- **Steward routes with a constrained-JSON classifier, not LLM tool-calling.** LM Studio
  parses tool calls out of model text against a chat template; at 8B that is unreliable
  enough to be load-bearing risk. Keep it behind a `Router` protocol so LLM routing can
  swap in on better hardware without touching the graph.
- **Errand is a tool, not an agent.** One call, no domain reasoning. An agent wrapper adds
  a hop and a model call for nothing.
- **Snapshot modeling, not a transaction ledger.** Transactions drag in merchant
  categorization, a never-finished project. A `transaction` table can be added later
  without changing the snapshot schema.
- **Raw CSV rows are stored before normalization runs.** A normalizer bug six months out
  must be recoverable without re-downloading exports.
- **Imports are idempotent via `file_sha256`.** Re-running an import is a no-op.
- **Trend tools return coverage metadata.** Backfilled history is uneven across accounts;
  naive summing makes net worth appear to jump when an account's data starts. Tools return
  `complete_from` and `incomplete_dates`; the agent must state the gap before describing
  any trend.
- **Thread history is its own tables, not the checkpointer.** The LangGraph checkpointer
  gives durable execution; it is not a readable history. `thread` and `message` are what
  the UI reads. `thread.id` is the LangGraph `thread_id`.
- **Thread search is Postgres full-text (`tsvector`), not embeddings.** You are looking
  for a thread you remember writing, so exact terms beat semantic similarity — and it
  costs no VRAM.
- **Embedding model is pinned in env and documented in the README.** Changing it requires
  re-embedding everything.

## RAG constraints

- `k <= 5`, chunks 400–600 tokens, hard cap on total retrieved tokens per turn
- Retrieved chunks compete with conversation history for 8,192 tokens
- Log retrieved chunk IDs on every call so retrieval quality is debuggable

## Evals

Assertions over LLM judges — the only local model available is the same 8B being graded,
so judging is circular. Design outputs to be assertable: seed a known DB state, assert the
exact figure from the tool result appears verbatim. "roughly $10,000" instead of "$9,750"
is a rounding failure and a fail.

Local models are nondeterministic: run each case 3–5 times and record a **pass rate**, not
pass/fail. Routing and refusal cases require all runs.

## Commands

```bash
make dev          # docker compose up + backend + frontend
make migrate      # alembic upgrade head
make seed         # load the golden fixture dataset (also used by evals)
make test         # pytest
make eval         # run the behavioural case file
make lint         # ruff + mypy
```

## Conventions

- `NUMERIC`, never `FLOAT`, for any money or measurement column
- `as_of` is always an explicit required argument. Never defaults to `today()`.
- Unknown CSV header layout fails loudly. Never guess at column mapping.
- Agent prompts live in `prompts/*.md`, version controlled. Never inline string literals.
- Tests before agent code for anything in the data or tool layer.

## Real data

Real CSVs live **outside this repo**, at `$HEARTH_DATA_DIR` (set in `.env`, mounted into
the containers). Never create, copy or paste them into the workspace, and never open one
in the editor while this session is attached.

- Fixtures in this repo are fake. The golden dataset used by `make seed` and `make eval`
  contains invented figures only.
- `.env` holds local configuration. Never read it or print its contents.
- A `Read` deny rule on `.env` and `data/**` is the backstop, but this file is context,
  not enforcement — the location rule is what actually keeps real data out of reach.
