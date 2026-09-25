import datetime as dt
import uuid

import sqlalchemy as sa

from db.models import SearchAudit


def test_search_audit_is_listed_newest_first(conn: sa.Connection) -> None:
    earlier = conn.execute(
        sa.insert(SearchAudit).values(
            query="local transit news",
            allowed=True,
            result_count=4,
            violation=None,
            thread_id=None,
            created_at=dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC),
        )
    )
    later = conn.execute(
        sa.insert(SearchAudit).values(
            query="account balance",
            allowed=False,
            violation="contains_account_number",
            result_count=None,
            thread_id=uuid.uuid4(),
            created_at=dt.datetime(2026, 9, 21, 12, 1, tzinfo=dt.UTC),
        )
    )
    conn.commit()

    rows = conn.execute(
        sa.select(SearchAudit).order_by(SearchAudit.created_at.desc(), SearchAudit.id.desc())
    ).scalars().all()

    assert [row.query for row in rows] == ["account balance", "local transit news"]
    assert rows[1].allowed is True
    assert rows[0].violation == "contains_account_number"
    assert later.inserted_primary_key[0] > earlier.inserted_primary_key[0]
