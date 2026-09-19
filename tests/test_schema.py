"""Structural guards for the rules that are easiest to break by accident.

These read the live schema rather than the models, so they stay honest about
what actually got built.
"""

import sqlalchemy as sa

#: Anything that looks like an institution's identifier for an account. Rule 4
#: admits no masked, hashed or last-four variant, so the test looks for the
#: shapes someone would reach for while trying to be careful.
FORBIDDEN_FRAGMENTS = (
    "account_number",
    "acct_num",
    "account_no",
    "accountnum",
    "last_four",
    "last4",
    "mask",
    "iban",
    "routing",
    "sort_code",
    "card_number",
)


def _columns(conn: sa.Connection) -> list[tuple[str, str, str, str]]:
    return [
        (str(t), str(c), str(d), str(n))
        for t, c, d, n in conn.execute(
            sa.text(
                "select table_name, column_name, data_type, is_nullable "
                "from information_schema.columns where table_schema = 'public'"
            )
        ).all()
    ]


def _base_tables(conn: sa.Connection) -> set[str]:
    """Views are excluded where nullability matters: a view column is always
    reported nullable regardless of the column underneath it."""
    return {
        str(name)
        for name in conn.execute(
            sa.text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' and table_type = 'BASE TABLE'"
            )
        ).scalars()
    }


def test_no_account_identifier_columns_exist(conn: sa.Connection) -> None:
    offenders = [
        f"{table}.{column}"
        for table, column, _, _ in _columns(conn)
        if any(fragment in column.lower() for fragment in FORBIDDEN_FRAGMENTS)
    ]
    assert offenders == [], f"rule 4: accounts carry a label, nothing else — {offenders}"


def test_no_floating_point_columns_exist(conn: sa.Connection) -> None:
    """NUMERIC, never FLOAT, for money or measurement — and there is nothing
    else here that would justify binary floating point, so the rule is checked
    across the whole schema rather than a list of known columns."""
    offenders = [
        f"{table}.{column} ({data_type})"
        for table, column, data_type, _ in _columns(conn)
        if data_type in {"double precision", "real"}
    ]
    assert offenders == [], offenders


def test_money_and_measurement_columns_are_numeric(conn: sa.Connection) -> None:
    expected = {
        ("balance_snapshot", "balance"),
        ("holding_snapshot", "quantity"),
        ("holding_snapshot", "price"),
        ("holding_snapshot", "market_value"),
        ("body_metric", "value"),
        ("workout", "duration_minutes"),
        ("workout_set", "weight"),
    }
    actual = {(table, column): data_type for table, column, data_type, _ in _columns(conn)}

    missing = expected - set(actual)
    assert missing == set(), f"columns vanished from the schema: {missing}"

    wrong = {key: actual[key] for key in expected if actual[key] != "numeric"}
    assert wrong == {}, wrong


def test_as_of_is_never_nullable(conn: sa.Connection) -> None:
    """`as_of` is the date a fact was true. A null one is a fact with no date,
    which no tool could honestly report on."""
    tables = _base_tables(conn)
    as_of_columns = [
        (table, nullable)
        for table, column, _, nullable in _columns(conn)
        if column == "as_of" and table in tables
    ]
    assert as_of_columns, "expected several as_of columns"
    assert all(nullable == "NO" for _, nullable in as_of_columns), as_of_columns


def test_import_is_idempotent_by_file_hash(conn: sa.Connection) -> None:
    """Re-running an import must be a no-op, which is a uniqueness constraint
    rather than a convention."""
    unique = conn.execute(
        sa.text(
            "select count(*) from information_schema.table_constraints tc "
            "join information_schema.key_column_usage kcu "
            "  on tc.constraint_name = kcu.constraint_name "
            "where tc.table_name = 'import_batch' "
            "  and tc.constraint_type = 'UNIQUE' "
            "  and kcu.column_name = 'file_sha256'"
        )
    ).scalar_one()
    assert unique == 1


def test_message_content_has_a_full_text_index(conn: sa.Connection) -> None:
    """Thread search is Postgres full-text, not embeddings. Without the GIN
    index it still works and quietly gets slower forever."""
    definition = conn.execute(
        sa.text("select indexdef from pg_indexes where indexname = 'ix_message_content_fts'")
    ).scalar_one()
    assert "gin" in definition.lower()
    assert "to_tsvector" in definition.lower()
