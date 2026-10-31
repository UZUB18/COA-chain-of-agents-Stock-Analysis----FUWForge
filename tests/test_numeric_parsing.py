"""Model-supplied numbers are parsed by an explicit scale table, never guessed."""
import pytest

from src.agents.model import _parse_numeric, _parse_numeric_or_none


@pytest.mark.parametrize("text, expected", [
    ("1.2bn", 1.2e9), ("1.2B", 1.2e9), ("350 million", 3.5e8), ("$4,100m", 4.1e9),
    ("1.5tn", 1.5e12), ("2mm", 2e6), ("15x", 15.0), ("12.5%", 12.5), ("-2.5", -2.5),
])
def test_scaled_values_parse(text, expected):
    assert _parse_numeric_or_none(text) == pytest.approx(expected)
    assert _parse_numeric(text, -1.0) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["abc", "5 kg", "nan", "inf", "1.2 bananas", ""])
def test_unknown_or_non_finite_text_is_unparseable(text):
    assert _parse_numeric_or_none(text) is None
    assert _parse_numeric(text, -1.0) == -1.0
