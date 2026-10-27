"""Deterministic derived-input verification tests.

Verifies:
1. Bounded safe arithmetic AST replay (no eval/exec, only +, -, *, / over fact-bound variables).
2. Elimination of the '100 / 2' arbitrary constant bypass (expressions cannot use arbitrary
   constants; operands must be declared and bound to ledger facts).
3. Elimination of the invalid arithmetic bypass with no result_value.
4. Full provenance, company, date/cutoff, period, and accounting basis validation of EVERY operand.
5. Rejection of future-period, wrong-company, look-ahead, or invalid-accession operands.
6. Rejection of unused, missing, counterfeit, boolean, null, or non-finite operands.
7. Unit, currency, and scale coherence enforcement.
8. Support for legitimate prior/current multi-period ratios and single-period derivations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.calculation_witness import validate_witness
from src.fact_binding import (
    DIM_CURRENCY,
    DIM_PER_SHARE,
    DIM_RATIO,
    NUMERIC_COMPARISON_POLICY,
    Derivation,
    DerivationEvaluationError,
    DerivationSyntaxError,
    DerivationUnitError,
    binding_failures,
    binding_notes,
    verify_input_binding,
    verify_witness_bindings,
)
from src.facts import Fact

ACCEPTED_ACCESSION = "0000320193-25-000079"


def _fact(**overrides) -> Fact:
    """Helper to produce a valid baseline fact."""
    payload = {
        "id": "fact_acme_rec_2024",
        "company": "ACME",
        "concept": "AccountsReceivable",
        "unit": "USD",
        "currency": "USD",
        "value": 400.0,
        "filing_accession": ACCEPTED_ACCESSION,
        "availability_time": "2025-02-01T00:00:00Z",
        "retrieval_time": "2025-02-02T00:00:00Z",
        "source_location": {"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"},
        "accounting_basis": "US-GAAP",
        "extractor_version": "sec_companyfacts_v1",
        "kind": "reported",
        "period_start": None,
        "period_end": None,
        "instant": "2024-12-31",
    }
    payload.update(overrides)
    return Fact.from_dict(payload)


# =========================================================================
# 1. Defect reproduction regressions: '100 / 2' arbitrary constant bypass
# =========================================================================


def test_reproduced_defect_fake_expression_100_div_2_with_fudged_result_rejected():
    """Reproduced defect: '100 / 2' with claimed=999, result=999 previously passed.

    Now it must be rejected: arbitrary constants are forbidden, operands are required,
    and claimed values must match actual evaluated arithmetic.
    """
    fact = _fact(id="fact_1", value=100.0)
    ledger = {fact.id: fact}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=999.0,
        fact=fact,
        fact_id=fact.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "100 / 2",
            "operand_fact_ids": [fact.id],
            "result_value": 999.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    joined = " ".join(binding.problems)
    assert "arbitrary" in joined or "constant" in joined or "unused" in joined


def test_fake_expression_100_div_2_even_with_accurate_math_is_rejected():
    """Even if claimed=50 matches 100/2, arbitrary numeric constants cannot substitute for facts."""
    fact = _fact(id="fact_1", value=50.0)
    ledger = {fact.id: fact}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=50.0,
        fact=fact,
        fact_id=fact.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "100 / 2",
            "operand_fact_ids": [fact.id],
            "result_value": 50.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("arbitrary" in p or "constant" in p for p in binding.problems)


# =========================================================================
# 2. Defect reproduction regressions: invalid arithmetic with no result_value
# =========================================================================


def test_reproduced_defect_invalid_arithmetic_with_no_result_fails():
    """Reproduced defect: previously if result_value was None, invalid arithmetic passed with a note.

    Now arithmetic is ALWAYS recomputed and validated against claimed_value.
    """
    f1 = _fact(id="fact_1", value=100.0)
    f0 = _fact(id="fact_0", value=0.0, instant="2023-12-31")
    ledger = {f1.id: f1, f0.id: f0}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f1.id, f0.id],
            "result_value": None,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("division by zero" in p.lower() for p in binding.problems)


# =========================================================================
# 3. Defect reproduction regressions: extra operands skipping validation
# =========================================================================


def test_reproduced_defect_extra_operand_with_wrong_company_fails():
    """Extra operands in a derivation must belong to expected_company."""
    f_acme = _fact(id="fact_acme", value=400.0, company="ACME")
    f_evil = _fact(id="fact_evil", value=200.0, company="EVILCORP", instant="2023-12-31")
    ledger = {f_acme.id: f_acme, f_evil.id: f_evil}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_acme,
        fact_id=f_acme.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_acme.id, f_evil.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("EVILCORP" in p and "ACME" in p for p in binding.problems)


def test_reproduced_defect_extra_operand_with_future_cutoff_fails():
    """Extra operands available after research cutoff violate look-ahead rules."""
    f_curr = _fact(id="fact_curr", value=400.0, availability_time="2025-01-01T00:00:00Z")
    f_future = _fact(
        id="fact_future",
        value=200.0,
        availability_time="2025-09-01T00:00:00Z",
        instant="2023-12-31",
    )
    ledger = {f_curr.id: f_curr, f_future.id: f_future}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_curr,
        fact_id=f_curr.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        cutoff="2025-06-01T00:00:00Z",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_curr.id, f_future.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("cutoff" in p.lower() or "look-ahead" in p.lower() for p in binding.problems)


def test_extra_operand_with_future_period_past_expected_period_fails():
    """An operand from a future fiscal period (e.g. 2025 when witness is for 2024) is rejected."""
    f_2024 = _fact(id="fact_2024", value=400.0, instant="2024-12-31")
    f_2025 = _fact(id="fact_2025", value=200.0, instant="2025-12-31")
    ledger = {f_2024.id: f_2024, f_2025.id: f_2025}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_2024,
        fact_id=f_2024.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        expected_period={"instant": "2024-12-31"},
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_2024.id, f_2025.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("future operand violation" in p.lower() for p in binding.problems)


def test_extra_operand_with_placeholder_filing_identity_fails():
    """All operands must carry valid, non-placeholder SEC accession identities."""
    f_valid = _fact(id="fact_valid", value=400.0)
    f_placeholder = _fact(
        id="fact_ph",
        value=200.0,
        filing_accession="0000000000-00-000000",
        instant="2023-12-31",
    )
    ledger = {f_valid.id: f_valid, f_placeholder.id: f_placeholder}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_valid,
        fact_id=f_valid.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_valid.id, f_placeholder.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("placeholder" in p.lower() for p in binding.problems)


def test_extra_operand_with_malformed_source_reference_fails():
    """All operands must carry absolute http(s) source URLs."""
    f_valid = _fact(id="fact_valid", value=400.0)
    f_bad_src = _fact(
        id="fact_bad_src",
        value=200.0,
        source_location="ftp://insecure.site",
        instant="2023-12-31",
    )
    ledger = {f_valid.id: f_valid, f_bad_src.id: f_bad_src}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_valid,
        fact_id=f_valid.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_valid.id, f_bad_src.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("source_location" in p or "reference" in p for p in binding.problems)


def test_extra_operand_with_null_or_boolean_or_nan_value_fails():
    """Operands must carry actual, finite numeric values."""
    f_valid = _fact(id="fact_valid", value=400.0)
    # Fact object with missing value (None)
    f_null = _fact(
        id="fact_null",
        value=None,
        missing_reason="Omitted in 10-K",
        instant="2023-12-31",
    )
    # Raw dict fact with boolean value
    dict_bool = {
        "id": "fact_bool",
        "company": "ACME",
        "value": True,
        "filing_accession": ACCEPTED_ACCESSION,
        "source_location": "https://sec.gov/filing",
        "instant": "2023-12-31",
        "unit": "USD",
        "accounting_basis": "US-GAAP",
    }
    # Raw dict fact with NaN value
    dict_nan = {
        "id": "fact_nan",
        "company": "ACME",
        "value": float("nan"),
        "filing_accession": ACCEPTED_ACCESSION,
        "source_location": "https://sec.gov/filing",
        "instant": "2023-12-31",
        "unit": "USD",
        "accounting_basis": "US-GAAP",
    }
    ledger = {
        f_valid.id: f_valid,
        f_null.id: f_null,
        "fact_bool": dict_bool,
        "fact_nan": dict_nan,
    }

    # Case A: None value
    b_null = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_valid,
        fact_id=f_valid.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_valid.id, f_null.id],
            "result_value": 2.0,
        },
    )
    assert b_null.bound is False
    assert any("records no value" in p for p in b_null.problems)

    # Case B: Boolean value
    b_bool = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_valid,
        fact_id=f_valid.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_valid.id, "fact_bool"],
            "result_value": 2.0,
        },
    )
    assert b_bool.bound is False
    assert any("invalid numeric value" in p for p in b_bool.problems)

    # Case C: NaN value
    b_nan = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_valid,
        fact_id=f_valid.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_valid.id, "fact_nan"],
            "result_value": 2.0,
        },
    )
    assert b_nan.bound is False
    assert any("invalid numeric value" in p for p in b_nan.problems)


def test_extra_operand_with_accounting_basis_mismatch_fails():
    """Operands must match expected_basis and be mutually basis-compatible."""
    f_usgaap = _fact(id="fact_usgaap", value=400.0, accounting_basis="US-GAAP")
    f_ifrs = _fact(id="fact_ifrs", value=200.0, accounting_basis="IFRS", instant="2023-12-31")
    ledger = {f_usgaap.id: f_usgaap, f_ifrs.id: f_ifrs}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_usgaap,
        fact_id=f_usgaap.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        expected_basis="US-GAAP",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_usgaap.id, f_ifrs.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("accounting basis mismatch" in p or "conflicting" in p for p in binding.problems)


# =========================================================================
# 4. Operand integrity: unused, missing, counterfeit operands
# =========================================================================


def test_unused_declared_operand_is_rejected():
    """Every operand declared in operand_fact_ids must be used in the expression."""
    f1 = _fact(id="fact_1", value=400.0)
    f2 = _fact(id="fact_2", value=200.0, instant="2023-12-31")
    f3 = _fact(id="fact_unused", value=100.0, instant="2022-12-31")
    ledger = {f1.id: f1, f2.id: f2, f3.id: f3}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f1.id, f2.id, f3.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("unused operand" in p for p in binding.problems)


def test_missing_or_unknown_variable_in_expression_is_rejected():
    """Every variable in expression must map to a declared operand."""
    f1 = _fact(id="fact_1", value=400.0)
    f2 = _fact(id="fact_2", value=200.0, instant="2023-12-31")
    ledger = {f1.id: f1, f2.id: f2}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / c",
            "operand_fact_ids": [f1.id, f2.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("cannot resolve" in p or "unknown variable" in p for p in binding.problems)


def test_counterfeit_operand_not_in_ledger_is_rejected():
    """Operands cited in derivation must exist in fact_ledger."""
    f1 = _fact(id="fact_1", value=400.0)
    ledger = {f1.id: f1}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f1.id, "fact_counterfeit_999"],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("do not exist in the fact ledger" in p for p in binding.problems)


# =========================================================================
# 5. Unsupported syntax & safety limits
# =========================================================================


def test_unsupported_syntax_function_call_or_exec_rejected():
    """No eval, exec, function calls or built-ins are permitted."""
    f1 = _fact(id="fact_1", value=400.0)
    ledger = {f1.id: f1}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=400.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "abs(fact_1)",
            "operand_fact_ids": [f1.id],
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("unsupported syntax" in p.lower() for p in binding.problems)


def test_unsupported_operators_exponent_or_shift_rejected():
    """Only +, -, *, / are supported binary operators."""
    f1 = _fact(id="fact_1", value=4.0)
    f2 = _fact(id="fact_2", value=2.0, instant="2023-12-31")
    ledger = {f1.id: f1, f2.id: f2}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=16.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a ** b",
            "operand_fact_ids": [f1.id, f2.id],
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("unsupported binary operator" in p.lower() for p in binding.problems)


def test_overlarge_expression_rejected():
    """Expressions exceeding length limits are rejected."""
    f1 = _fact(id="fact_1", value=400.0)
    ledger = {f1.id: f1}
    long_expr = ("a + " * 300) + "a"
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=400.0,
        fact=f1,
        fact_id=f1.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": long_expr,
            "operand_fact_ids": [f1.id],
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("limit" in p.lower() for p in binding.problems)


# =========================================================================
# 6. Unit, currency, and scale coherence
# =========================================================================


def test_unit_incoherence_subtracting_shares_from_currency_fails():
    """Cannot add/subtract operands of different physical dimensions."""
    f_usd = _fact(id="f_usd", value=500.0, unit="USD")
    f_shares = _fact(id="f_shares", value=100.0, unit="shares", instant="2023-12-31")
    ledger = {f_usd.id: f_usd, f_shares.id: f_shares}
    binding = verify_input_binding(
        input_key="diff",
        claimed_value=400.0,
        fact=f_usd,
        fact_id=f_usd.id,
        expected_unit="USD",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a - b",
            "operand_fact_ids": [f_usd.id, f_shares.id],
            "result_value": 400.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("unit error" in p or "different dimensions" in p for p in binding.problems)


def test_unit_incoherence_currency_mismatch_in_ratio_division_fails():
    """Dividing USD by EUR cannot produce a pure dimensionless ratio without FX reconciliation."""
    f_usd = _fact(id="f_usd", value=400.0, unit="USD", currency="USD")
    f_eur = _fact(id="f_eur", value=200.0, unit="EUR", currency="EUR", instant="2023-12-31")
    ledger = {f_usd.id: f_usd, f_eur.id: f_eur}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_usd,
        fact_id=f_usd.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_usd.id, f_eur.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("currency mismatch" in p for p in binding.problems)


def test_unit_incoherence_scale_mismatch_in_ratio_division_fails():
    """Dividing raw USD by USD thousand is a 1000x scale discrepancy."""
    f_usd = _fact(id="f_usd", value=400.0, unit="USD")
    f_k = _fact(id="f_k", value=200.0, unit="USD thousand", instant="2023-12-31")
    ledger = {f_usd.id: f_usd, f_k.id: f_k}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_usd,
        fact_id=f_usd.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_usd.id, f_k.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("scale mismatch" in p for p in binding.problems)


def test_unit_incoherence_subtracting_usd_when_expecting_ratio_fails():
    """USD - USD yields USD currency, not dimensionless ratio."""
    f_rev = _fact(id="f_rev", value=500.0, unit="USD")
    f_cogs = _fact(id="f_cogs", value=300.0, unit="USD", instant="2023-12-31")
    ledger = {f_rev.id: f_rev, f_cogs.id: f_cogs}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=200.0,
        fact=f_rev,
        fact_id=f_rev.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a - b",
            "operand_fact_ids": [f_rev.id, f_cogs.id],
            "result_value": 200.0,
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("dimension mismatch" in p for p in binding.problems)


# =========================================================================
# 7. Supported legitimate derivations: ratios & multi-period formulas
# =========================================================================


def test_legitimate_multi_period_prior_current_ratio_passes():
    """DSRI = receivables_t1 / receivables_t0: period T anchored, prior period T-1 allowed."""
    f_curr = _fact(id="rec_2024", value=400.0, unit="USD", instant="2024-12-31")
    f_prev = _fact(id="rec_2023", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f_curr.id: f_curr, f_prev.id: f_prev}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_curr,
        fact_id=f_curr.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        expected_period={"instant": "2024-12-31"},
        cutoff="2025-06-01",
        derivation_spec={
            "expression": "rec_2024 / rec_2023",
            "operand_fact_ids": [f_curr.id, f_prev.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is True
    assert binding.status == "bound"
    assert binding.problems == []


def test_legitimate_single_period_compound_formula_passes():
    """TATA = (net_income - cfo) / assets: all operands share period 2024."""
    f_ni = _fact(id="ni_2024", value=50.0, unit="USD", instant="2024-12-31")
    f_cfo = _fact(id="cfo_2024", value=30.0, unit="USD", instant="2024-12-31")
    f_ta = _fact(id="ta_2024", value=100.0, unit="USD", instant="2024-12-31")
    ledger = {f_ni.id: f_ni, f_cfo.id: f_cfo, f_ta.id: f_ta}
    binding = verify_input_binding(
        input_key="tata",
        claimed_value=0.2,
        fact=f_ni,
        fact_id=f_ni.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        expected_period={"instant": "2024-12-31"},
        cutoff="2025-06-01",
        derivation_spec={
            "expression": "(net_income - cfo) / total_assets",
            "operand_fact_ids": [f_ni.id, f_cfo.id, f_ta.id],
            "operand_names": ["net_income", "cfo", "total_assets"],
            "result_value": 0.2,
        },
    )
    assert binding.bound is True
    assert binding.status == "bound"
    assert binding.problems == []


def test_currency_divided_by_count_yields_per_share():
    """USD / shares correctly derives USD/share dimension."""
    f_rev = _fact(id="f_rev", value=1000.0, unit="USD", instant="2024-12-31")
    f_shares = _fact(id="f_shares", value=500.0, unit="shares", instant="2024-12-31")
    ledger = {f_rev.id: f_rev, f_shares.id: f_shares}
    binding = verify_input_binding(
        input_key="rev_per_share",
        claimed_value=2.0,
        fact=f_rev,
        fact_id=f_rev.id,
        expected_unit="USD/share",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_rev.id, f_shares.id],
            "result_value": 2.0,
        },
    )
    assert binding.bound is True
    assert binding.status == "bound"
    assert binding.problems == []


def test_operands_dict_syntax_support():
    """Derivation from_spec supports operands as a dict {var_name: fact_id}."""
    f_curr = _fact(id="rec_2024", value=400.0, unit="USD", instant="2024-12-31")
    f_prev = _fact(id="rec_2023", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f_curr.id: f_curr, f_prev.id: f_prev}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=f_curr,
        fact_id=f_curr.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "rec_t1 / rec_t0",
            "operands": {"rec_t1": f_curr.id, "rec_t0": f_prev.id},
            "result_value": 2.0,
        },
    )
    assert binding.bound is True
    assert binding.status == "bound"


# =========================================================================
# 8. Numeric comparison policy & tolerance
# =========================================================================


def test_claimed_value_mismatch_with_recomputed_under_exact_policy_fails():
    """Claimed value 2.0001 vs recomputed 2.0 is a hard failure under default exact policy."""
    f_curr = _fact(id="rec_2024", value=400.0, unit="USD", instant="2024-12-31")
    f_prev = _fact(id="rec_2023", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f_curr.id: f_curr, f_prev.id: f_prev}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0001,
        fact=f_curr,
        fact_id=f_curr.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_curr.id, f_prev.id],
        },
    )
    assert binding.bound is False
    assert binding.status == "failed"
    assert any("does not match recomputed" in p for p in binding.problems)
    assert any(NUMERIC_COMPARISON_POLICY in p for p in binding.problems)


def test_opt_in_tolerance_allows_small_float_delta_when_explicitly_requested():
    """Opt-in tolerance rel_tol=1e-3 accommodates rounding, while exceeding tolerance fails."""
    f_curr = _fact(id="rec_2024", value=400.0, unit="USD", instant="2024-12-31")
    f_prev = _fact(id="rec_2023", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f_curr.id: f_curr, f_prev.id: f_prev}

    # Within tolerance
    b_ok = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0001,
        fact=f_curr,
        fact_id=f_curr.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_curr.id, f_prev.id],
        },
        rel_tol=1e-3,
    )
    assert b_ok.bound is True

    # Exceeding tolerance
    b_fail = verify_input_binding(
        input_key="dsri",
        claimed_value=2.1,
        fact=f_curr,
        fact_id=f_curr.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        derivation_spec={
            "expression": "a / b",
            "operand_fact_ids": [f_curr.id, f_prev.id],
        },
        rel_tol=1e-3,
    )
    assert b_fail.bound is False


# =========================================================================
# 9. Witness level integration
# =========================================================================


def test_verify_witness_bindings_with_valid_and_invalid_derived_inputs():
    """End-to-end witness bindings verification."""
    f1 = _fact(id="f1", value=400.0, unit="USD", instant="2024-12-31")
    f0 = _fact(id="f0", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f1.id: f1, f0.id: f0}

    witness = {
        "formula_id": "beneish_m_score",
        "inputs": {"dsri": 2.0, "sgi": 99.0},
        "input_fact_ids": {"dsri": f1.id, "sgi": f1.id},
        "units": {"dsri": "ratio", "sgi": "ratio"},
        "derivations": {
            "dsri": {
                "expression": "a / b",
                "operand_fact_ids": [f1.id, f0.id],
                "result_value": 2.0,
            },
            "sgi": {
                "expression": "a / b",
                "operand_fact_ids": [f1.id, f0.id],
                "result_value": 99.0,  # Fudged result vs actual 2.0
            },
        },
    }

    bindings = verify_witness_bindings(
        witness,
        fact_ledger=ledger,
        expected_company="ACME",
        cutoff="2025-06-01",
    )
    b_map = {b.input_key: b for b in bindings}
    assert b_map["dsri"].bound is True
    assert b_map["sgi"].bound is False
    assert binding_failures(bindings) != []
