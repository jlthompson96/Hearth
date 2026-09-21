"""Getting data in: CSV exports and manual entry (Phase 2).

The only package in Hearth that writes financial data, through the read-write
engine in `db.writer`. Everything the model reads goes through the read-only
role in `db.session`; nothing in here is reachable from a tool.
"""
