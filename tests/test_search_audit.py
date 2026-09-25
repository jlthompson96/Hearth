import datetime as dt

import sqlalchemy as sa

from db.models import SearchAudit, Thread


def test_search_audit_is_listed_newest_first(conn: sa.Connection) -> None:
    # A search made from a thread names it, and the foreign key holds it to one
    # that exists.
    thread_id = conn.execute(sa.insert(Thread).returning(Thread.id)).scalar_one()
    earlier = conn.execute(
        sa.insert(SearchAudit)
        .values(
            query="local transit news",
            allowed=True,
            result_count=4,
            violation=None,
            thread_id=None,
            created_at=dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC),
        )
        .returning(SearchAudit.id)
    ).scalar_one()
    later = conn.execute(
        sa.insert(SearchAudit)
        .values(
            query="account balance",
            allowed=False,
            violation="contains_account_number",
            result_count=None,
            thread_id=thread_id,
            created_at=dt.datetime(2026, 9, 21, 12, 1, tzinfo=dt.UTC),
        )
        .returning(SearchAudit.id)
    ).scalar_one()
    # No commit: the fixture's transaction is rolled back after the test, and
    # committing it would leave these rows behind for every test after this one.

    # Columns, not the entity: on a Core connection `select(SearchAudit)`
    # with `.scalars()` yields only the first column, the id.
    rows = conn.execute(
        sa.select(
            SearchAudit.query, SearchAudit.allowed, SearchAudit.violation, SearchAudit.thread_id
        ).order_by(SearchAudit.created_at.desc(), SearchAudit.id.desc())
    ).all()

    assert [row.query for row in rows] == ["account balance", "local transit news"]
    assert rows[1].allowed is True
    assert rows[0].violation == "contains_account_number"
    assert rows[0].thread_id == thread_id
    assert later > earlier
