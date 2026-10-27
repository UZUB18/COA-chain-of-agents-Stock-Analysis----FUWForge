"""Fail closed on malformed derivation metadata and untrusted numeric fields."""
import pytest

from src.fact_binding import verify_input_binding


def _fact(fact_id, value):
    return {
        "id": fact_id, "company": "ACME", "concept": "Assets", "value": value,
        "unit": "USD", "currency": "USD", "instant": "2024-12-31",
        "filing_accession": "0000320193-25-000001", "availability_time": "2025-02-01T00:00:00Z",
        "source_location": {"url": "https://data.sec.gov/filing"}, "accounting_basis": "US-GAAP",
        "confidence": value,
    }


def _bind(spec=None, **a_overrides):
    a = _fact("f_a", 100.0)
    a.update(a_overrides)
    b = _fact("f_b", 50.0)
    derivation = {"expression": "a / b", "operand_fact_ids": ["f_a", "f_b"]}
    derivation.update(spec or {})
    return verify_input_binding(
        input_key="ratio", claimed_value=2.0, fact=a, fact_id="f_a", expected_unit="ratio",
        expected_company="ACME", cutoff="2025-06-01", fact_ledger={"f_a": a, "f_b": b},
        derivation_spec=derivation,
    )


@pytest.mark.parametrize("value", [True, "2", "nonsense", float("nan"), float("inf"), 10 ** 500])
def test_declared_result_must_be_finite_number(value):
    assert _bind({"result_value": value}).status == "failed"


@pytest.mark.parametrize("fields", [[], ["value"], ["value"] * 3, "value", ["confidence", "confidence"]])
def test_operand_fields_cannot_be_padded_truncated_or_use_metadata(fields):
    assert _bind({"operand_fields": fields}).status == "failed"


def test_huge_operand_fails_without_overflow_exception():
    assert _bind(value=10 ** 500).status == "failed"


@pytest.mark.parametrize("period", [
    {"instant": "2024-99-99"},
    {"instant": "20241231"},
    {"instant": None, "period_start": "2024-12-31", "period_end": "2024-01-01"},
    {"instant": "2024-12-31", "period_start": "2024-01-01", "period_end": "2024-12-31"},
])
def test_invalid_operand_economic_periods_cannot_bind(period):
    assert _bind(**period).status == "failed"


def test_unknown_unit_cannot_be_inferred_as_financial_ratio():
    assert _bind(unit="unrecognized", currency=None).status == "failed"


def test_valid_explicit_value_fields_remain_replayable():
    assert _bind({"operand_fields": ["value", "value"], "result_value": 2.0}).bound
