import pytest

from insightflow_core.validation.number_grounding import ungrounded_numbers

SOURCE = "revenue = 16,008,872.12\nrevenue_growth: -0.1005\ncustomers by region: {'region': 'SP', 'value': 41746.0} {'region': 'AL', 'value': 413.0}"


@pytest.mark.parametrize(
    "text",
    [
        "Revenue is 16,008,872.12.",
        "Revenue is about 16.0 million, roughly 16M.",
        "Revenue fell 10.05% (about 10%).",
        "SP has 41,746 customers, about 41.7k; AL has 413.",
        "The top 3 regions and 2 others.",
    ],
)
def test_numbers_present_in_the_source_to_their_written_precision_are_grounded(text):
    assert ungrounded_numbers(text, SOURCE) == []


@pytest.mark.parametrize(
    ("text", "ungrounded"),
    [
        ("SP and AL have 42159 customers together.", ["42159"]),  # a sum the engine never computed
        ("Revenue is about 17 million.", ["17 million"]),
        ("Revenue fell 12%.", ["12%"]),
        ("Revenue fell 10.08%.", ["10.08%"]),  # more precision than the source supports
        ("Revenue grew in 2018.", ["2018"]),  # not in anything the LLM was shown
    ],
)
def test_numbers_not_in_the_source_are_reported(text, ungrounded):
    assert ungrounded_numbers(text, SOURCE) == ungrounded
