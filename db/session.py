"""Database connections.

Two engines, deliberately asymmetric. `readonly_engine` is what every query
tool runs through; there is no read-write engine here at all, because nothing
that reads on behalf of the model has any business writing. Ingestion and
migrations build their own engine from `DATABASE_URL` where that is the whole
point of the code.

This is rule 2's second half. The model never writes SQL, and the connection
its answers are assembled from could not execute a write if it did.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy as sa

from config import get_settings


@lru_cache
def readonly_engine() -> sa.Engine:
    return sa.create_engine(
        get_settings().database_url_ro,
        # Query tools are short and frequent; a small pool is plenty for one
        # person, and pre-ping avoids the stale connection you get after the
        # container restarts underneath a long-running backend.
        pool_size=5,
        max_overflow=0,
        pool_pre_ping=True,
    )


@contextmanager
def readonly_connection() -> Iterator[sa.Connection]:
    """A connection for one query tool call."""
    with readonly_engine().connect() as connection:
        yield connection
