"""The body-weight history export: Date, Recorded, Moving Average.

The layout is from a screenshot of the real file, 2026-09-25, and the first
real import settled what the screenshot could not: the third column is
"Moving Average", and the file writes dates YYYY-MM-DD — the screenshot's
"1/4/2017" was Excel re-formatting them. Checked from the header and the
cells' digit-masked shapes, so no weigh-in entered the session: 809 rows,
every date `####-##-##`, every weight `###.##`. Every row here is invented.

The moving average is the app's own arithmetic. It is kept in the raw rows
and never becomes a figure: a trend of a trend is not a weigh-in.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from db.models import BodyMetric, ImportRow
from ingest.errors import Conflict, DateMismatch, FixtureLoaded, RowRefused, UnknownLayout
from ingest.importer import import_export, remove_import, renormalize
from ingest.training import record_body_weight

FILENAME = "weight_history.csv"
EXPORTED = dt.date(2026, 9, 25)
HEADER = "Date,Recorded,Moving Average"


def history(*rows: str) -> bytes:
    body = rows or ("2021-03-02,200.4,200.4", "2021-03-09,199.8,200.1", "2021-03-16,198.6,199.6")
    return ("\n".join((HEADER, *body)) + "\n").encode()


def _readings(conn: sa.Connection) -> list[tuple[dt.date, Decimal, str]]:
    rows = conn.execute(
        sa.select(BodyMetric.as_of, BodyMetric.value, BodyMetric.unit)
        .where(BodyMetric.metric == "body_mass")
        .order_by(BodyMetric.as_of)
    ).all()
    return [(r.as_of, r.value, r.unit) for r in rows]


def test_each_recorded_weight_becomes_a_body_weight_in_pounds(conn: sa.Connection) -> None:
    result = import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)

    assert _readings(conn) == [
        (dt.date(2021, 3, 2), Decimal("200.400"), "lb"),
        (dt.date(2021, 3, 9), Decimal("199.800"), "lb"),
        (dt.date(2021, 3, 16), Decimal("198.600"), "lb"),
    ]
    assert result.body_weights == 3
    assert result.accounts == ()
    assert result.source_label == "Body weight history"


def test_the_moving_average_is_kept_raw_and_never_becomes_a_figure(conn: sa.Connection) -> None:
    import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)

    metrics = conn.execute(sa.select(BodyMetric.metric).distinct()).scalars().all()
    assert metrics == ["body_mass"]
    raw = conn.execute(sa.select(ImportRow.raw).order_by(ImportRow.row_number)).scalars().first()
    assert raw == {"Date": "2021-03-02", "Recorded": "200.4", "Moving Average": "200.4"}


def test_a_day_with_nothing_recorded_is_skipped_not_read_as_zero(conn: sa.Connection) -> None:
    """Apps that smooth often write a row for every day, the average filled in
    and the weigh-in blank. A blank is a day nobody stepped on the scale."""
    result = import_export(
        conn,
        content=history("2021-03-02,200.4,200.4", "2021-03-03,,200.3", "2021-03-04,199.9,200.2"),
        filename=FILENAME,
        as_of=EXPORTED,
    )

    assert [d for d, _, _ in _readings(conn)] == [dt.date(2021, 3, 2), dt.date(2021, 3, 4)]
    assert (result.rows, result.body_weights) == (3, 2)


def test_the_date_is_read_year_month_day(conn: sa.Connection) -> None:
    import_export(
        conn, content=history("2021-01-04,210.0,210.0"), filename=FILENAME, as_of=EXPORTED
    )

    assert _readings(conn)[0][0] == dt.date(2021, 1, 4)


@pytest.mark.parametrize(
    ("cell", "shape"),
    [
        ("3/2/2021", "#/#/####"),
        ("02/03/21", "##/##/##"),
        ('"Mar 2, 2021"', "Mar #, ####"),
        ("2021-3-2", "####-#-#"),
    ],
)
def test_a_date_in_any_other_form_is_refused_by_its_shape(
    conn: sa.Connection, cell: str, shape: str
) -> None:
    with pytest.raises(RowRefused, match="row 2, Date") as refused:
        import_export(
            conn, content=history(f"{cell},200.4,200.4"), filename=FILENAME, as_of=EXPORTED
        )

    assert shape in str(refused.value)
    assert _readings(conn) == []


def test_a_day_that_does_not_exist_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(RowRefused, match="not a date"):
        import_export(
            conn, content=history("2021-02-30,200.4,200.4"), filename=FILENAME, as_of=EXPORTED
        )


def test_a_weight_that_is_not_a_weight_is_refused_by_its_shape(conn: sa.Connection) -> None:
    with pytest.raises(RowRefused, match=r"row 3, Recorded: '###\.# lb'"):
        import_export(
            conn,
            content=history("2021-03-02,200.4,200.4", "2021-03-09,199.8 lb,200.1"),
            filename=FILENAME,
            as_of=EXPORTED,
        )
    assert _readings(conn) == []


def test_a_zero_weight_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(RowRefused, match="row 2"):
        import_export(conn, content=history("2021-03-02,0,0"), filename=FILENAME, as_of=EXPORTED)


def test_the_same_day_twice_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(RowRefused, match="rows 2 and 3"):
        import_export(
            conn,
            content=history("2021-03-02,200.4,200.4", "2021-03-02,200.0,200.2"),
            filename=FILENAME,
            as_of=EXPORTED,
        )


def test_a_weigh_in_after_the_export_was_taken_is_refused(conn: sa.Connection) -> None:
    with pytest.raises(DateMismatch, match="2026-09-26"):
        import_export(
            conn, content=history("2026-09-26,190.0,190.0"), filename=FILENAME, as_of=EXPORTED
        )


def test_a_day_already_recorded_is_never_overwritten(conn: sa.Connection) -> None:
    record_body_weight(conn, as_of=dt.date(2021, 3, 9), weight=Decimal("199.0"))

    with pytest.raises(Conflict, match="2021-03-09") as clash:
        import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)

    assert "entered by hand" in str(clash.value)
    assert _readings(conn) == [(dt.date(2021, 3, 9), Decimal("199.000"), "lb")]


def test_a_longer_export_overlapping_an_earlier_one_names_it(conn: sa.Connection) -> None:
    """A later export repeats the earlier one's history. It is refused, naming
    the earlier file — remove that import, and the longer one replaces it."""
    import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)
    longer = history(
        "2021-03-02,200.4,200.4",
        "2021-03-09,199.8,200.1",
        "2021-03-16,198.6,199.6",
        "2021-03-23,198.2,199.2",
    )

    with pytest.raises(Conflict, match="weight_history.csv"):
        import_export(conn, content=longer, filename="weight_history_2.csv", as_of=EXPORTED)


def test_importing_the_same_file_twice_is_a_no_op(conn: sa.Connection) -> None:
    import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)
    again = import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)

    assert again.already_imported
    assert len(_readings(conn)) == 3


def test_removing_the_import_takes_its_weigh_ins_with_it(conn: sa.Connection) -> None:
    """The foreign key is ON DELETE SET NULL. Deleting the batch alone would
    leave every weigh-in behind, looking exactly like one entered by hand."""
    result = import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)

    remove_import(conn, result.batch_id)

    assert _readings(conn) == []


def test_renormalizing_rebuilds_the_weigh_ins_from_the_raw_rows(conn: sa.Connection) -> None:
    result = import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)

    rebuilt = renormalize(conn, result.batch_id)

    assert rebuilt.body_weights == 3
    assert len(_readings(conn)) == 3


def test_a_history_in_another_unit_is_refused(conn: sa.Connection) -> None:
    conn.execute(
        sa.insert(BodyMetric).values(
            as_of=dt.date(2020, 1, 1), metric="body_mass", value=Decimal("90"), unit="kg"
        )
    )

    with pytest.raises(Conflict, match="kg"):
        import_export(conn, content=history(), filename=FILENAME, as_of=EXPORTED)


def test_a_header_that_is_not_exactly_this_one_names_its_columns(conn: sa.Connection) -> None:
    """The third column was cut off in the screenshot. If its name is not
    "Moving Average", the refusal says what it is — header text is not data."""
    content = b"Date,Recorded,Moving Avg\n2021-03-02,200.4,200.4\n"

    with pytest.raises(UnknownLayout, match="Moving Avg"):
        import_export(conn, content=content, filename=FILENAME, as_of=EXPORTED)


def test_nothing_is_imported_beside_the_fixture(seeded: sa.Connection) -> None:
    with pytest.raises(FixtureLoaded):
        import_export(seeded, content=history(), filename=FILENAME, as_of=EXPORTED)
