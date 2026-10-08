"""Source-binding verification: a cited fact ID must carry the cited number.

These tests exist because the previous check only confirmed that a referenced
fact ID *existed*. A witness could therefore cite a real fact while supplying a
fabricated number, and the arithmetic replay would faithfully recompute the
score from the fabricated inputs and report success.

The headline case is the mandatory pair from the specification:

- **Witness A** references a fact with value ``100`` and uses input ``100``
  -> must PASS the numeric binding check.
- **Witness B** references that *same* fact ID but uses input ``150``
  -> must FAIL.

Everything else here is the surrounding contract: period, unit, source identity,
point-in-time availability, accounting basis, derived ratios, and the unit
bypass that used to skip every check whenever the expected unit was ``ratio``.
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
    DIM_COUNT,
    DIM_CURRENCY,
    DIM_RATIO,
    NUMERIC_COMPARISON_POLICY,
    binding_failures,
    binding_notes,
    compare_numbers,
    fact_filing_identity_valid,
    fact_source_reference_valid,
    units_compatible,
    unit_currency,
    unit_dimension,
    unit_scale,
    verify_input_binding,
    verify_witness_bindings,
)
from src.facts import Fact

ACCEPTED_ACCESSION = "0000320193-25-000079"


def _fact(**overrides) -> Fact:
    """A single well-formed, point-in-time-usable ratio fact."""
    payload = {
        "id": "fact_acme_dsri_1",
        "company": "ACME",
        "concept": "DSRI",
        "unit": "ratio",
        "currency": None,
        "value": 100.0,
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


# ============================================================ the mandatory pair


def test_witness_a_real_value_binds_and_witness_b_fabricated_value_fails():
    """
    Two witnesses, identical except for the number they claim.

    Both cite the same fact, whose recorded value is 100. Witness A uses 100 and
    must bind. Witness B uses 150 against that same fact and must fail, because
    the binding check compares the *value*, not merely the citation's existence.
    """
    fact = _fact(value=100.0)
    ledger = {fact.id: fact}

    def witness(value: float) -> dict:
        return {
            "formula_id": "beneish_m_score",
            "formula_version": "2.0.0",
            "calculator_id": "beneish_m_score",
            "result": 0.0,  # arithmetic correctness is checked elsewhere
            "inputs": {"dsri": value},
            "input_fact_ids": {"dsri": fact.id},
            "units": {"dsri": "ratio"},
            "provenance": "deterministic",
        }

    bindings_a = verify_witness_bindings(
        witness(100.0), fact_ledger=ledger, expected_company="ACME"
    )
    bindings_b = verify_witness_bindings(
        witness(150.0), fact_ledger=ledger, expected_company="ACME"
    )

    assert bindings_a[0].bound is True, binding_failures(bindings_a)
    assert binding_failures(bindings_a) == []

    assert bindings_b[0].bound is False
    assert bindings_b[0].status == "failed"
    failures_b = binding_failures(bindings_b)
    assert failures_b, "witness B must produce a binding failure"
    # The message must name the disagreement, not just the fact ID.
    joined = " ".join(failures_b)
    assert "150" in joined and "100" in joined
    assert NUMERIC_COMPARISON_POLICY in joined


def test_validate_witness_rejects_fabricated_input_through_the_public_api():
    """The same defect must be caught through the exported validator too."""
    fact = _fact(value=100.0)
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "beneish_m_score",
        "formula_version": "2.0.0",
        "calculator_id": "beneish_m_score",
        "result": 0.0,
        "inputs": {"dsri": 150.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert errors, "a fabricated input must produce a validation error"
    assert any("150" in e and "100" in e for e in errors)


# ================================================================ wrong period


def test_witness_declaring_the_wrong_period_is_rejected():
    fact = _fact(value=100.0, instant="2024-12-31")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        # The witness claims FY2023; the fact is a 2024-12-31 instant.
        "input_periods": {"dsri": {"instant": "2023-12-31"}},
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("period" in e.lower() for e in errors), errors


def test_mismatched_period_type_is_rejected():
    """An instant fact cannot satisfy a duration period expectation."""
    fact = _fact(value=100.0, instant="2024-12-31")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        "input_periods": {
            "dsri": {"period_start": "2024-01-01", "period_end": "2024-12-31"}
        },
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("period type" in e.lower() for e in errors), errors


# ================================================================== wrong unit


def test_ratio_input_citing_a_currency_fact_is_rejected():
    """
    The historical bypass: ``expected_unit != "ratio" and fact_unit != expected_unit``
    skipped every unit check when the expected unit *was* a ratio, so a ratio
    index could cite a dollar-denominated fact.
    """
    fact = _fact(value=100.0, unit="USD", concept="Revenues")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("ratio" in e and "currency" in e for e in errors), errors


def test_ratio_input_citing_a_share_count_fact_is_rejected():
    fact = _fact(value=100.0, unit="shares", concept="CommonStockSharesOutstanding")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("count" in e for e in errors), errors


def test_unit_scale_mismatch_is_reported_as_a_scale_error():
    """USD and 'USD thousand' share a dimension but differ by 1000x."""
    assert units_compatible("USD", "USD thousand") is False
    fact = _fact(value=100.0, unit="USD thousand", concept="Revenues")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"revenue": 100.0},
        "input_fact_ids": {"revenue": fact.id},
        "units": {"revenue": "USD"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("scale" in e.lower() for e in errors), errors


def test_currency_mismatch_is_rejected():
    fact = _fact(value=100.0, unit="EUR", currency="EUR", concept="Revenues")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"revenue": 100.0},
        "input_fact_ids": {"revenue": fact.id},
        "units": {"revenue": "USD"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("currency" in e.lower() for e in errors), errors


def test_compatible_normalized_units_are_accepted():
    """Synonyms in the same dimension with no scale or currency conflict pass."""
    assert units_compatible("ratio", "pure") is True
    fact = _fact(value=100.0, unit="pure")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert errors == [], errors


def test_unit_dimension_classification():
    assert unit_dimension("ratio") == DIM_RATIO
    assert unit_dimension("pure") == DIM_RATIO
    assert unit_dimension("USD") == DIM_CURRENCY
    assert unit_dimension("EUR") == DIM_CURRENCY
    assert unit_dimension("shares") == DIM_COUNT
    assert unit_dimension("count") == DIM_COUNT
    assert unit_dimension("USD/share") == "per_share"
    assert unit_currency("USD/share") == "USD"
    assert unit_scale("USD thousand") == 1000.0


# ============================================================== source identity


def test_fact_with_no_filing_accession_cannot_back_an_input():
    fact = _fact(filing_accession="")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("accession" in e.lower() for e in errors), errors


def test_placeholder_accession_is_rejected():
    """A placeholder like the SEC default must not count as filing identity."""
    fact = _fact(filing_accession="0000000000-00-000000")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("accession" in e.lower() for e in errors), errors


def test_fact_filing_identity_helper():
    ok, _ = fact_filing_identity_valid(_fact())
    assert ok is True
    ok, _ = fact_filing_identity_valid(_fact(filing_accession="nonsense"))
    assert ok is False


def test_fact_without_source_reference_cannot_back_an_input():
    fact = _fact(source_location="")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("source_location" in e for e in errors), errors


def test_fact_source_reference_helper():
    ok, _ = fact_source_reference_valid(_fact())
    assert ok is True
    ok, _ = fact_source_reference_valid(_fact(source_location="not-a-url"))
    assert ok is False


# =========================================================== point-in-time rule


def test_future_dated_fact_is_rejected_as_look_ahead():
    fact = _fact(availability_time="2025-08-01T00:00:00Z")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("look-ahead" in e.lower() or "after the research cutoff" in e for e in errors), errors


def test_fact_available_exactly_at_cutoff_is_accepted():
    fact = _fact(availability_time="2025-06-01T00:00:00Z")
    ledger = {fact.id: fact}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=100.0,
        fact=fact,
        fact_id=fact.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        cutoff="2025-06-01",
    )
    assert binding.bound is True, binding.problems


# ============================================================ wrong company


def test_fact_from_another_company_is_rejected():
    fact = _fact(company="OTHERCO")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("company" in e.lower() for e in errors), errors


# =========================================================== missing / unusable


def test_missing_fact_id_is_rejected():
    ledger: dict = {}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": "fact_does_not_exist"},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("does not exist" in e for e in errors), errors


def test_unavailable_fact_cannot_back_a_numeric_input():
    fact = _fact(value=None, missing_reason="not reported")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("records no value" in e for e in errors), errors


def test_unsourced_input_is_disclosed_not_failed():
    """
    An input with no cited fact is a disclosed gap, not a false claim.

    It must not be escalated as an error, but it must still be visible, so that
    "the arithmetic replayed" is never read as "every input was source-backed".
    """
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
    }
    errors = validate_witness(witness, fact_ledger={}, cutoff="2025-06-01")
    assert errors == [], errors
    bindings = verify_witness_bindings(witness, fact_ledger={})
    assert bindings[0].status == "unavailable"
    assert binding_notes(bindings), "the gap must be disclosed"
    assert binding_failures(bindings) == []


# ============================================================= accounting basis


def test_accounting_basis_mismatch_is_rejected():
    fact = _fact(accounting_basis="IFRS")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        "input_bases": {"dsri": "US-GAAP"},
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("basis" in e.lower() for e in errors), errors


def test_equivalent_accounting_basis_spellings_are_compatible():
    fact = _fact(accounting_basis="US_GAAP")
    ledger = {fact.id: fact}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 100.0},
        "input_fact_ids": {"dsri": fact.id},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        "input_bases": {"dsri": "US-GAAP"},
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert errors == [], errors


# ================================================================== derivations


def test_derived_ratio_requires_an_inspectable_derivation():
    """
    A ratio input may legitimately be computed from other facts, but only when
    the derivation is stated and its operands exist.
    """
    operand = _fact(id="fact_revenue", value=400.0, unit="USD", concept="Revenues")
    ledger = {operand.id: operand}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 2.0},
        "input_fact_ids": {"dsri": "fact_revenue"},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        "derivations": {
            "dsri": {
                "expression": "receivables_t1 / receivables_t0",
                "operand_fact_ids": ["fact_revenue"],
                "result_value": 2.0,
            }
        },
    }
    # Marked derived, so the raw-USD fact is acceptable as an operand, and the
    # declared result matches the claimed input.
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert errors == [], errors


def test_derived_ratio_with_missing_operand_is_rejected():
    operand = _fact(id="fact_revenue", value=400.0, unit="USD", concept="Revenues")
    ledger = {operand.id: operand}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 2.0},
        "input_fact_ids": {"dsri": "fact_revenue"},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        "derivations": {
            "dsri": {
                "expression": "a / b",
                "operand_fact_ids": ["fact_revenue", "fact_absent"],
                "result_value": 2.0,
            }
        },
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("do not exist" in e for e in errors), errors


def test_derived_ratio_with_wrong_declared_result_is_rejected():
    operand = _fact(id="fact_revenue", value=400.0, unit="USD", concept="Revenues")
    ledger = {operand.id: operand}
    witness = {
        "formula_id": "x",
        "formula_version": "1",
        "calculator_id": "x",
        "result": 1.0,
        "inputs": {"dsri": 2.0},
        "input_fact_ids": {"dsri": "fact_revenue"},
        "units": {"dsri": "ratio"},
        "provenance": "deterministic",
        "derivations": {
            "dsri": {
                "expression": "a / b",
                "operand_fact_ids": ["fact_revenue"],
                # Declared derivation result disagrees with the claimed input.
                "result_value": 99.0,
            }
        },
    }
    errors = validate_witness(
        witness, fact_ledger=ledger, cutoff="2025-06-01", expected_company="ACME"
    )
    assert any("derived value" in e for e in errors), errors


def test_derived_marked_without_a_derivation_spec_is_rejected():
    """`derivation_spec` present but unusable must not silently pass."""
    fact = _fact(value=100.0, unit="USD", concept="Revenues")
    ledger = {fact.id: fact}
    binding = verify_input_binding(
        input_key="dsri",
        claimed_value=2.0,
        fact=fact,
        fact_id=fact.id,
        expected_unit="ratio",
        fact_ledger=ledger,
        expected_company="ACME",
        cutoff="2025-06-01",
        derivation_spec={"expression": "", "operand_fact_ids": []},
    )
    assert binding.bound is False
    assert any("derivation" in p for p in binding.problems)


# ============================================================= numeric policy


def test_compare_numbers_is_exact_by_default():
    ok, _ = compare_numbers(100, 100)
    assert ok is True
    ok, detail = compare_numbers(100, 150)
    assert ok is False
    assert "no tolerance" in detail
    # A tiny difference is still a difference under the default policy.
    ok, _ = compare_numbers(1.0, 1.0 + 1e-15)
    assert ok is False


def test_compare_numbers_rejects_non_finite_and_boolean():
    ok, _ = compare_numbers(True, 1.0)
    assert ok is False
    ok, _ = compare_numbers(float("inf"), 1.0)
    assert ok is False
    ok, _ = compare_numbers(float("nan"), float("nan"))
    assert ok is False


def test_opt_in_scaled_comparison_is_declared_not_hidden():
    ok, detail = compare_numbers(1.0, 1.0 + 1e-12, rel_tol=1e-9)
    assert ok is True
    assert "tolerance" in detail
    ok, detail = compare_numbers(100.0, 150.0, rel_tol=1e-9)
    assert ok is False
    assert "exceeding the declared tolerance" in detail


def test_no_tolerance_can_excuse_the_mandatory_fifty_percent_error():
    """Even the opt-in scaled mode must not excuse 100 vs 150."""
    ok, _ = compare_numbers(100.0, 150.0, rel_tol=1e-9, abs_tol=1e-12)
    assert ok is False

# ================================================== integration: the audit path


def _beneish_envelope(inputs, input_fact_ids):
    """A minimal A3-shaped envelope carrying a BeneishM witness."""
    from src.envelope import AgentInfo, Envelope
    from src.utils.beneish import beneish_score

    from src.calculation_witness import beneish_witness

    indices = {k: float(v) for k, v in inputs.items()}
    witness = beneish_witness(
        indices=indices,
        fact_ids=input_fact_ids,
        result=beneish_score(**indices),
    ).to_dict()
    return Envelope(
        schema_version="2.1",
        agent=AgentInfo(id="A3", name="Quantitative-Risk"),
        timestamp="2025-06-01T00:00:00Z",
        params={"ticker": "ACME", "analysis_date": "2025-06-01"},
        gates={},
        sections_md={},
        artifacts={
            "BeneishM": {
                "score": round(beneish_score(**indices), 2),
                "raw_score": beneish_score(**indices),
                "feasible": True,
                "provenance": "deterministic",
                "calculation_witness": witness,
            }
        },
        sources=[],
        warnings=[],
        metrics={},
    )


_ALL_INDICES = {
    "dsri": 1.0, "gmi": 1.0, "aqi": 1.0, "sgi": 1.0,
    "depi": 1.0, "sgai": 1.0, "lvgi": 1.0, "tata": 0.0,
}


def test_replay_beneish_accepts_witness_a_real_value():
    """Witness A: input 100 against a fact whose value is 100 -> no problems."""
    from src.run_semantic_audit import replay_beneish

    fact = _fact(value=100.0, id="fact_acme_dsri")
    ledger = {fact.id: fact}
    indices = dict(_ALL_INDICES)
    indices["dsri"] = 100.0
    envelope = _beneish_envelope(indices, {"dsri": fact.id})
    problems = replay_beneish(
        envelope, fact_ledger=ledger, analysis_date="2025-06-01",
        expected_company="ACME",
    )
    assert problems == [], problems


def test_replay_beneish_rejects_witness_b_fabricated_value():
    """
    Witness B: the same fact ID, but the witness uses 150.

    The score is recomputed faithfully from 150, so the arithmetic is
    internally consistent. Only the source-binding check can catch this, and it
    must.
    """
    from src.run_semantic_audit import replay_beneish

    fact = _fact(value=100.0, id="fact_acme_dsri")
    ledger = {fact.id: fact}
    tampered = dict(_ALL_INDICES)
    tampered["dsri"] = 150.0
    envelope = _beneish_envelope(tampered, {"dsri": fact.id})

    problems = replay_beneish(
        envelope, fact_ledger=ledger, analysis_date="2025-06-01",
        expected_company="ACME",
    )
    assert problems, "a fabricated input must fail the calculation-replay level"
    joined = " ".join(problems)
    assert "150" in joined and "100" in joined


def test_replay_beneish_reports_unsourced_indices_as_unavailable_not_pass():
    """
    A witness with no fact citations still replays arithmetically, but the audit
    must disclose that the inputs were not source-backed. 'Replayed' and
    'source-backed' are different claims.
    """
    from src.run_semantic_audit import replay_beneish

    envelope = _beneish_envelope(_ALL_INDICES, {})
    unavailable: list = []
    problems = replay_beneish(
        envelope, fact_ledger={}, analysis_date="2025-06-01",
        expected_company="ACME", unavailable=unavailable,
    )
    assert problems == [], problems
    assert unavailable, "unsourced inputs must be disclosed as unavailable"
    assert any("no source fact" in u for u in unavailable)


def test_replay_beneish_rejects_future_dated_fact():
    from src.run_semantic_audit import replay_beneish

    fact = _fact(value=1.0, id="fact_acme_dsri", availability_time="2026-01-01T00:00:00Z")
    ledger = {fact.id: fact}
    envelope = _beneish_envelope(_ALL_INDICES, {"dsri": fact.id})
    problems = replay_beneish(
        envelope, fact_ledger=ledger, analysis_date="2025-06-01",
        expected_company="ACME",
    )
    assert problems and any("cutoff" in p or "look-ahead" in p for p in problems), problems


def test_replay_beneish_rejects_ratio_input_backed_by_currency_fact():
    """The unit bypass, exercised through the real audit entry point."""
    from src.run_semantic_audit import replay_beneish

    fact = _fact(value=1.0, id="fact_acme_rev", unit="USD", concept="Revenues")
    ledger = {fact.id: fact}
    envelope = _beneish_envelope(_ALL_INDICES, {"dsri": fact.id})
    problems = replay_beneish(
        envelope, fact_ledger=ledger, analysis_date="2025-06-01",
        expected_company="ACME",
    )
    assert problems and any("ratio" in p and "currency" in p for p in problems), problems


def test_replay_beneish_rejects_swapped_source_identity():
    """A fact from another issuer cannot back this run's index."""
    from src.run_semantic_audit import replay_beneish

    fact = _fact(value=1.0, id="fact_acme_dsri", company="OTHERCO")
    ledger = {fact.id: fact}
    envelope = _beneish_envelope(_ALL_INDICES, {"dsri": fact.id})
    problems = replay_beneish(
        envelope, fact_ledger=ledger, analysis_date="2025-06-01",
        expected_company="ACME",
    )
    assert problems and any("company" in p.lower() for p in problems), problems
