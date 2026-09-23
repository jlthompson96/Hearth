# Hearth MVP 1

**What it is.** Hearth answers questions about your own money, from your own exports, on
your own machine. A router sends each question to a specialist; the specialist answers by
calling Python functions that query Postgres, and it states the figures those functions
computed rather than any of its own. Nothing leaves this machine. Forge, the training
specialist, ships with it and is described below.

Tagged from `TODO-SHA`, 2026-09-22.

## What it does

- **Imports a Fidelity positions export** and records what each account held that day,
  refusing anything it cannot read rather than guessing. Verified against a real export:
  both account totals matched Fidelity's to the cent.
- **Answers questions about what you own and what it is worth**, and about how those
  figures have moved once there is more than one date to compare — with the gap stated
  first when an account is missing from a date.
- **Tells you where every figure came from.** The tool call is shown under the answer, and
  any figure no tool returned is flagged as unverified.
- **Understands a follow-up.** "Yes" accepts what an answer offered; each specialist is
  shown only its own last few answers.
- **Keeps your conversations**, searchable by a word you remember typing, for a year by
  default.
- **Refuses what it should.** Self-harm, hate speech and disordered-eating questions never
  reach a model at all. Advice, prices and anything outside your records are declined.
- **Shows its working.** The Model log keeps every exchange with the model, verbatim.
- **Lets you change what it does**, in Settings: how long answers are, how long threads and
  logs are kept, and which chat model answers — from the models LM Studio has and this card
  can run.

## What it does not do yet

- **Forge has no data.** The training specialist is complete and tested over invented data;
  your database has no workouts or body measurements in it, so it will tell you it has
  nothing logged. Deciding where real training data comes from — an app export or manual
  entry — is the open question that unblocks it.
- **Trends need more history.** One import means one date. Import each month's export, or
  enter past month-end balances by hand, and the trend questions become real.
- **No web search, no documents, no MCP.** Errand (Phase 9) and RAG (Phase 10) are blocked
  on this machine: SearXNG needs Docker, which does not run here, and pgvector needs an
  MSVC build. MCP connections (Phase 12) are unstarted and need one folder chosen.
- **No transactions.** Snapshots only — what an account was worth on a day, not what was
  spent. That was cut deliberately; merchant categorisation is a project of its own.

## Rough edges, known and measured

- **A question asked as a follow-up that seeks advice** ("should I add more weight next
  week?") is sent to the specialist rather than declined by the router. The specialist
  declines it, so no advice is given.
- **The eval suite varies between full runs.** The same case, with the same code, has gone
  0/3 and then 3/3. A single run's one-case delta is not evidence; an interleaved A/B is.
- **An answer can offer to show a period with no records**, and the follow-up then finds
  nothing. Rewording the offer was measured, cost exactness elsewhere, and was reverted.
- **The model's reasoning text is not kept** in the Model log — LM Studio returns it in a
  field the client drops — only its token count.

## Running it

```bash
scripts\hearth.cmd     # or: make start — Postgres, backend, frontend, browser
make backup            # a dump outside the repository; keeps the newest fourteen
make test              # 554 tests, about three seconds, no model
make eval              # 62 behavioural cases against the real model, about 20 minutes
```

The chat is at http://localhost:5173. Postgres is a native cluster rather than a service,
so a reboot needs `make start` (or the shortcut) again.

## What it is measured at

- **`make test`:** 554 tests, no model, about three seconds.
- **`make eval`:** TODO-EVAL on `nvidia/nemotron-3-nano-4b`. Recorded in
  `evals/results/`, one file per commit, each naming the model it measured.
- Routing was measured at 60/60 on twenty labelled questions; the pre-flight filter at 113
  cases in both directions, with every entry point proven to refuse before a model is built.

A pass rate belongs to a model. Changing the chat model in Settings does not carry these
numbers with it, and the screen says which models have been measured.

## Where things are

- **Your exports:** `$HEARTH_DATA_DIR`, outside this repository. Never copied in.
- **Your data:** Postgres, on this machine. Backups: `%LOCALAPPDATA%\Hearth\backups`.
- **The rules this is built to:** [CLAUDE.md](../CLAUDE.md). The guardrails table in
  [README.md](../README.md) names where each one is enforced and the test that proves it.
- **Why each decision was made:** [docs/plan.md](plan.md), including what was measured and
  what was reverted.
