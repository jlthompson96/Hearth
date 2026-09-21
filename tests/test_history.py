"""Thread history: storing conversations, finding them again, and forgetting
them on schedule.

    Exit: a thread from last week is findable by a word you remember typing.

The word you remember is rarely the exact word you typed — "squat" for a
question about squats — so search is Postgres full-text with English stemming,
over the same expression the GIN index was built on in Phase 1.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from history import store
from history.store import RETENTION
from ingest.errors import NotFound

NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)


def _thread(conn: sa.Connection, *turns: tuple[str, str], title: str | None = None) -> uuid.UUID:
    thread_id, _ = store.open_thread(conn, None, now=NOW)
    for question, answer in turns:
        store.add_message(conn, thread_id, role="user", content=question)
        store.add_message(conn, thread_id, role="assistant", content=answer, agent="tally")
    if title:
        store.set_title(conn, thread_id, title)
    return thread_id


def _age(conn: sa.Connection, thread_id: uuid.UUID, by: dt.timedelta) -> None:
    """Move a thread and its messages into the past."""
    when = NOW - by
    conn.execute(
        sa.text("update thread set created_at = :w, updated_at = :w where id = :id"),
        {"w": when, "id": thread_id},
    )
    conn.execute(
        sa.text("update message set created_at = :w where thread_id = :id"),
        {"w": when, "id": thread_id},
    )


# --- the exit criterion --------------------------------------------------------


def test_a_thread_from_last_week_is_found_by_a_word_you_remember(conn: sa.Connection) -> None:
    last_week = _thread(
        conn,
        ("How have my squats progressed since March?", "Your back squat went up 17.5kg."),
        title="Back squat progress",
    )
    _age(conn, last_week, dt.timedelta(days=7))
    _thread(conn, ("how has my net worth moved this year", "It rose $38,250.00."))

    found = store.list_threads(conn, query="squat")

    assert [t.id for t in found] == [last_week]
    # Not a substring match: "squatting" appears nowhere in the thread, and
    # finds it because both stem to "squat".
    assert [t.id for t in store.list_threads(conn, query="squatting")] == [last_week]


def test_the_match_is_shown_in_context(conn: sa.Connection) -> None:
    """Finding a thread is half of it; seeing why it matched is the other."""
    _thread(conn, ("What did I bench in July?", "Your bench press was 76.25kg in July."))

    [found] = store.list_threads(conn, query="bench")

    assert found.snippet is not None
    matched = [part.text for part in found.snippet if part.match]
    assert matched and all(word.lower().startswith("bench") for word in matched)
    assert "".join(part.text for part in found.snippet).strip()


def test_search_reaches_answers_as_well_as_questions(conn: sa.Connection) -> None:
    thread = _thread(conn, ("how is my allocation", "Mostly VTI, with some BND."))

    assert [t.id for t in store.list_threads(conn, query="VTI")] == [thread]


@pytest.mark.parametrize("query", ["the", "   ", "!!!", "'; drop table thread; --", '"unclosed'])
def test_a_query_that_says_nothing_finds_nothing_and_breaks_nothing(
    conn: sa.Connection, query: str
) -> None:
    """Typed into a search box, so it is text from a person, not a query
    language. websearch_to_tsquery never raises on malformed input."""
    _thread(conn, ("the net worth question", "an answer"))

    found = store.list_threads(conn, query=query)

    assert isinstance(found, list)
    assert conn.execute(sa.text("select count(*) from thread")).scalar_one() == 1


# --- storing and reading back ----------------------------------------------------


def test_a_thread_reads_back_in_order_with_everything_the_ui_draws(conn: sa.Connection) -> None:
    thread_id, created = store.open_thread(conn, None, now=NOW)
    store.add_message(conn, thread_id, role="user", content="how has my net worth moved")
    store.add_message(
        conn,
        thread_id,
        role="assistant",
        content="It rose $38,250.00.",
        agent="tally",
        tool_calls=[{"name": "net_worth_trend", "args": {"start": "2026-01-01"}}],
        confidence=Decimal("0.92"),
    )

    detail = store.get_thread(conn, thread_id)

    assert created
    assert [m.role for m in detail.messages] == ["user", "assistant"]
    answer = detail.messages[1]
    assert (answer.agent, answer.refused, answer.confidence) == ("tally", False, Decimal("0.920"))
    assert answer.tool_calls == [{"name": "net_worth_trend", "args": {"start": "2026-01-01"}}]


def test_an_existing_thread_is_continued_not_duplicated(conn: sa.Connection) -> None:
    thread_id = _thread(conn, ("first", "one"))

    again, created = store.open_thread(conn, thread_id, now=NOW)

    assert (again, created) == (thread_id, False)


def test_continuing_a_thread_that_does_not_exist_is_not_found(conn: sa.Connection) -> None:
    with pytest.raises(NotFound):
        store.open_thread(conn, uuid.uuid4(), now=NOW)


def test_a_new_message_moves_the_thread_to_the_top(conn: sa.Connection) -> None:
    older = _thread(conn, ("older", "a"))
    newer = _thread(conn, ("newer", "b"))
    _age(conn, older, dt.timedelta(days=3))
    _age(conn, newer, dt.timedelta(days=1))

    store.add_message(conn, older, role="user", content="a follow-up")

    assert [t.id for t in store.list_threads(conn)][:2] == [older, newer]


def test_a_title_is_needed_once(conn: sa.Connection) -> None:
    thread_id = _thread(conn, ("q", "a"))

    assert store.needs_title(conn, thread_id)
    store.set_title(conn, thread_id, "Net worth")
    assert not store.needs_title(conn, thread_id)


def test_pinned_threads_list_first(conn: sa.Connection) -> None:
    pinned = _thread(conn, ("pinned", "a"))
    _age(conn, pinned, dt.timedelta(days=30))
    _thread(conn, ("recent", "b"))

    store.set_pinned(conn, pinned, True, now=NOW)

    listed = store.list_threads(conn)
    assert listed[0].id == pinned and listed[0].pinned


def test_a_thread_can_be_deleted_with_its_messages(conn: sa.Connection) -> None:
    thread_id = _thread(conn, ("q", "a"))

    store.delete_thread(conn, thread_id)

    assert conn.execute(sa.text("select count(*) from message")).scalar_one() == 0
    with pytest.raises(NotFound):
        store.delete_thread(conn, thread_id)


# --- retention -----------------------------------------------------------------


def test_retention_is_one_year() -> None:
    """The decision, recorded where it can be checked: a year from the last
    message, pinned threads exempt."""
    assert dt.timedelta(days=365) == RETENTION


def test_a_thread_untouched_for_a_year_is_forgotten(conn: sa.Connection) -> None:
    stale = _thread(conn, ("an old question", "an old answer"))
    kept = _thread(conn, ("a recent question", "a recent answer"))
    _age(conn, stale, RETENTION + dt.timedelta(days=1))
    _age(conn, kept, RETENTION - dt.timedelta(days=1))

    removed = store.sweep(conn, now=NOW)

    remaining = conn.execute(sa.text("select id from thread")).scalars().all()
    assert (removed, remaining) == (1, [kept])
    assert conn.execute(sa.text("select count(*) from message")).scalar_one() == 2


def test_a_pinned_thread_is_kept_however_old(conn: sa.Connection) -> None:
    ancient = _thread(conn, ("keep this", "kept"))
    store.set_pinned(conn, ancient, True, now=NOW)
    _age(conn, ancient, RETENTION * 3)

    assert store.sweep(conn, now=NOW) == 0


def test_unpinning_puts_a_thread_back_under_retention(conn: sa.Connection) -> None:
    thread_id = _thread(conn, ("q", "a"))
    store.set_pinned(conn, thread_id, True, now=NOW)
    _age(conn, thread_id, RETENTION * 2)

    store.set_pinned(conn, thread_id, False, now=NOW)

    assert store.sweep(conn, now=NOW) == 1


def test_starting_a_thread_sweeps_first(conn: sa.Connection) -> None:
    """Retention holds even if the backend runs for a year without a restart:
    every new thread is also a sweep."""
    stale = _thread(conn, ("old", "a"))
    _age(conn, stale, RETENTION + dt.timedelta(days=1))

    store.open_thread(conn, None, now=NOW)

    assert stale not in conn.execute(sa.text("select id from thread")).scalars().all()
