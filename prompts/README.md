Agent prompts live here as version-controlled Markdown, one file per agent —
`tally.md`, `forge.md`. Never as inline string literals in Python.

The reason is Phase 7: the eval harness measures the delta when a prompt changes,
and a prompt you cannot diff is a prompt you cannot measure.

A rule that exists only in one of these files is not implemented. Iteration caps,
egress validation and refusal filters are conditional edges and input checks in
code (CLAUDE.md, rule 7).
