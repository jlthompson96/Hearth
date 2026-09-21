"""Every figure in an answer should be one a tool produced (rule 1, at runtime)."""

from agents.grounding import ungrounded

TOOL = """net worth 2026-01-01 to 2026-09-20
  2026-01-31  $123,400.00
  2026-08-31  $161,650.00
change over the period: +$38,250.00
caveat: Net worth is complete only from 2026-08-31 onward."""


def test_a_figure_copied_from_the_tool_is_grounded() -> None:
    assert ungrounded("Your net worth rose $38,250.00 this year.", [TOOL]) == []


def test_the_same_value_written_differently_is_still_grounded() -> None:
    """Compared by value: dropping the cents or telling a fall as a positive
    amount is not a new figure."""
    assert ungrounded("It rose $38,250, from $123,400 to $161,650.", [TOOL]) == []
    assert ungrounded("It fell $50.00.", ["change over the period: -$50.00"]) == []


def test_a_rounded_figure_is_flagged() -> None:
    """ "Roughly $38,000" in place of "$38,250.00" is a rounding failure — the
    same standard the evals hold, applied to every real answer."""
    assert ungrounded("It rose roughly $38,000.", [TOOL]) == ["$38,000"]


def test_a_figure_the_model_computed_is_flagged() -> None:
    """A monthly average nobody's tool returned is arithmetic by the model."""
    flagged = ungrounded("That is about $4,781.25 a month.", [TOOL])

    assert flagged == ["$4,781.25"]


def test_a_figure_with_no_tool_at_all_is_flagged() -> None:
    assert ungrounded("Your balance is $1,200.00.", []) == ["$1,200.00"]


def test_a_figure_from_the_question_may_be_quoted_back() -> None:
    question = "my card was at $1,950.23 last month, is it lower now?"
    tool = "Credit Card (USD)\n  2026-09-20  -$950.50"

    assert ungrounded("It was $1,950.23; it is now -$950.50.", [tool, question]) == []


def test_weights_are_checked_too() -> None:
    tool = "back squat: 100.000kg x5 (est. 1RM 116.7kg) ... 117.500kg; change +17.500kg"

    assert ungrounded("Your squat went up 17.5kg, to 117.5kg.", [tool]) == []
    assert ungrounded("Your squat went up about 18kg.", [tool]) == ["18kg"]


def test_an_answer_with_no_figures_has_nothing_to_flag() -> None:
    assert ungrounded("I have no data for that period.", []) == []


def test_each_figure_is_flagged_once() -> None:
    assert ungrounded("$5.00 and again $5.00", []) == ["$5.00"]
