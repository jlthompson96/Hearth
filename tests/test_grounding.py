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

    assert ungrounded("It was $1,950.23; it is now -$950.50.", [tool], [question]) == []


def test_weights_are_checked_too() -> None:
    tool = "back squat: 100.000kg x5 (est. 1RM 116.7kg) ... 117.500kg; change +17.500kg"

    assert ungrounded("Your squat went up 17.5kg, to 117.5kg.", [tool]) == []
    assert ungrounded("Your squat went up about 18kg.", [tool]) == ["18kg"]


def test_an_answer_with_no_figures_has_nothing_to_flag() -> None:
    assert ungrounded("I have no data for that period.", []) == []


def test_each_figure_is_flagged_once() -> None:
    assert ungrounded("$5.00 and again $5.00", []) == ["$5.00"]


def test_a_percentage_no_tool_returned_is_flagged() -> None:
    """Asked what share of a net worth sat in one account, the model answered
    "100%" — arithmetic on two figures, which is what rule 1 forbids."""
    result = "\n".join(
        [
            "VTI  $27,600.00  56.1%",
            "total of these holdings, which is not net worth: $49,200.00",
        ]
    )

    assert ungrounded("100% of it is in your brokerage.", [result]) == ["100%"]
    assert ungrounded("VTI is 56.1% of the account.", [result]) == []


def test_a_percentage_the_question_carried_is_not_flagged() -> None:
    """The same rule as for money: what they typed is a fair thing to repeat."""
    assert ungrounded("A 4% withdrawal is what you asked about.", [""], ["is 4% safe"]) == []


# --- a tool's figure grounds only a figure of its own kind --------------------------
#
# Every number in a tool result used to count, dates included, so the day, the
# month and the year of each line vouched for a dollar amount nobody returned.
# Probed against a real trend output: "$31", "$2,026" and "$28" all passed.


def test_the_digits_of_a_date_do_not_ground_an_amount() -> None:
    tool = "net worth\n  2026-01-31  $38,250.00\n  2026-02-28  $39,000.00"

    assert ungrounded("It rose by $31.", [tool]) == ["$31"]
    assert ungrounded("Up $2,026 since January.", [tool]) == ["$2,026"]
    assert ungrounded("About $28 more than February.", [tool]) == ["$28"]


def test_a_rep_count_does_not_ground_an_amount_or_a_weight() -> None:
    """ "x5" is five reps. It is not five dollars, and it is not five kilograms."""
    tool = "  2026-01-31  100.000kg x5  est. 1RM 116.7kg"

    assert ungrounded("That is $5 of progress.", [tool]) == ["$5"]
    assert ungrounded("You added 5kg.", [tool]) == ["5kg"]


def test_a_group_label_does_not_ground_a_figure() -> None:
    """The grouped weight history labels its lines "2025-Q1" and "2025-01"."""
    tool = "  2025-Q1  average 182.3lb, low 181.920lb, high 182.690lb (78 recordings)"

    assert ungrounded("It averaged 182.3lb across 78 weigh-ins.", [tool]) == []
    assert ungrounded("You lost 25lb.", [tool]) == ["25lb"]
    assert ungrounded("That is 78% of the way.", [tool]) == ["78%"]


def test_a_bare_amount_they_typed_may_be_quoted_back_with_its_unit() -> None:
    """People type "40000", and the answer writes "$40,000". What they typed is
    theirs to have repeated, in whatever form."""
    assert ungrounded("It is under $40,000.", ["$38,250.00"], ["is it over 40000?"]) == []


def test_a_date_in_the_question_does_not_ground_an_amount() -> None:
    question = "what was my net worth on 2026-01-31?"

    assert ungrounded("It rose by $31.", ["$38,250.00"], [question]) == ["$31"]
