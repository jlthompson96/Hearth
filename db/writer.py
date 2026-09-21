"""The read-write connection, and the only one outside migrations.

`db.session` holds the read-only engine every query tool uses. This is the other
half, kept in its own module so the asymmetry stays visible: two things write —
ingestion (`ingest/`) and thread history (`history/`) — and neither is reachable
from a tool. Nothing that reads on behalf of the model imports this file.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy as sa

from config import get_settings


@lru_cache
def writer_engine() -> sa.Engine:
    # One person, one import at a time: a pool of two is generous.
    return sa.create_engine(
        get_settings().database_url, pool_size=2, max_overflow=0, pool_pre_ping=True
    )


@contextmanager
def writer_connection() -> Iterator[sa.Connection]:
    """One transaction: committed if the block completes, rolled back if not."""
    with writer_engine().begin() as connection:
        yield connection
