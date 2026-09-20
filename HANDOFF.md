# Handoff

Working notes for whichever machine picks this up next. Delete this file once Phase 4's
exit criterion is met and folded into a commit — it is scratch, not a permanent doc.

## Where things stand (2026-09-20)

Phases 0, 1, 3 and 4's code are committed and pushed (`main`, up to date with `origin`).
Phase 2 is deliberately skipped, parked on open question 1 (a real CSV header row).

Phase 4 (model connection) is code-complete but its exit criterion is **not yet verified**:
a constrained-JSON call returning schema-valid output 10/10 times. That can only be measured
where the model actually runs — this repo is developed on a Mac, hosted on a Windows GPU box
running LM Studio natively (no WSL anywhere; see the "Dev/host split" note below).

`tests/test_llm_connection.py::test_constrained_json_is_schema_valid_ten_times` is marked
`@pytest.mark.model` and skips itself with a reason when no LM Studio endpoint answers —
that is expected and correct on a machine with no model server, not a bug.

## The one blocker

`.env` has `CHAT_MODEL` and `EMBEDDING_MODEL` empty (by design — no hardcoded model names).
Nothing model-dependent can run anywhere until these are filled in from what LM Studio
actually reports for the loaded models.

**Next concrete step:** open LM Studio, note the exact model id(s) it shows for the loaded
chat model (and, when chosen, the embedding model — see open question 2 in docs/plan.md),
and set them in `.env`. Then:

```bash
make test        # the skip disappears; the model test either passes 10/10 or fails and says why
```

If running from the Mac against the host over the LAN rather than on the host directly,
also enable **Serve on Local Network** in LM Studio and set:

```
LM_STUDIO_BASE_URL=http://<host-lan-address>:1234/v1
```

Rule 5 (CLAUDE.md) permits LAN calls, so this is within the rules — it is the same model on
the same GPU, reached over a wire rather than through `localhost`.

## Dev/host split

Developed on a Mac (Apple Silicon), hosted on an RTX 4060 Ti box running Windows natively —
LM Studio is the Windows app, **no WSL anywhere**. Consequences already handled in this repo:

- The Makefile resolves `$(BIN)` to `.venv/Scripts` on Windows, `.venv/bin` elsewhere.
  `make migrate`/`seed`/`test`/`lint` work unmodified on both machines.
- `make dev` still assumes a POSIX shell (`trap`/`wait` in one recipe) — on the Windows host
  that means Git Bash or MSYS2, or starting `uvicorn` and `vite` as two separate commands.
- Every eval pass rate and the Phase 4 smoke test only mean something when run against the
  actual 8B-at-Q4 model on the actual host. A number produced anywhere else does not
  characterise the target — do not report one as if it does.

## Setting up a fresh clone

```bash
git clone https://github.com/jlthompson96/Hearth.git
cd Hearth
cp .env.example .env      # fill in CHAT_MODEL, EMBEDDING_MODEL, SEARXNG_SECRET, etc.
make install
make up                   # Postgres + SearXNG — needs Docker
make migrate
make seed
make test
```

`.env` is gitignored and machine-specific; it does not travel with `git clone` and has to be
recreated on each machine. Real financial data never enters this repo regardless of machine
— see "Real data" in CLAUDE.md.

## After Phase 4 is actually verified

Next is Phase 5 — Tally end to end: the finance agent over Phase 3's tools, SSE streaming to
a minimal chat UI. Exit criterion: "how has my net worth moved this year" returns a correct,
tool-derived answer that states coverage caveats. That is a legitimate stopping point per
docs/plan.md — one agent over your own data is most of the value.
