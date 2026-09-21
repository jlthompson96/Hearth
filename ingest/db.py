"""The read-write connection, and the only one outside migrations.

`db.session` holds the read-only engine every query tool uses and deliberately
has no read-write one. Ingestion is the exception it names: writing financial
data is the whole point of this package. Nothing here is reachable from a tool.
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
