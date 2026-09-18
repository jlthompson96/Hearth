Behavioural evals. Lands in Phase 7, extending Phase 6's 20 labeled routing cases.

- `cases.yaml` — the case file: routing, tool selection, grounded numbers, required
  caveats, refusals.
- `results/<sha>.json` — recorded runs, committed, so a prompt change produces a
  measurable delta rather than a vibe.

Assertions, not LLM judges: the only local model available is the same 8B being
graded, so judging would be circular. Seed a known DB state and assert the exact
figure from the tool result appears verbatim — "roughly $10,000" in place of
"$9,750" is a rounding failure and a fail.

Local models are nondeterministic. Run each case 3–5 times and record a pass rate.
Routing and refusal cases require all runs to pass.
