"""Steward — the orchestrator. Routes each turn to a specialist (Phase 6).

Routing is a constrained-JSON classifier behind a `Router` protocol, not LLM
tool-calling: at 8B, tool-call parsing is too unreliable to be load-bearing.
"""
