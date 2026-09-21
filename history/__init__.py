"""Thread history (Phase 8): conversations stored, searched, titled and, after a
year, forgotten.

Its own tables rather than the LangGraph checkpointer. The checkpointer gives
durable execution; it is not a readable history, and `thread` and `message` are
what the UI reads (CLAUDE.md). Writes go through `db.writer`; nothing here is
reachable from a tool.
"""
