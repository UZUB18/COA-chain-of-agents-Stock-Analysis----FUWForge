"""Runtime calculation witness validation tests.

Covers:
1. Typed metadata roundtrip for CalculationWitness and beneish_witness (derivations, input_periods, input_bases).
2. Elimination of the silent derivations-without-input-map bypass (non-empty derivations with absent/nonmapping input_fact_ids).
3. validate_contract validation of embedded witnesses: valid calculations, invalid arithmetic/result, invalid provenance.
4. Source binding validation through validate_contract: value mismatches, company mismatches, cutoff look-ahead violations.
5. Backward compatibility for no-ledger mock envelopes and legacy envelopes with no witnesses.
"""

from __future__ import annotations

import pytest

from src.calculation_witness import (
    COMPUTED_BY_DETERMINISTIC,
    PROVENANCE_MODEL_REPORTED,
    CalculationWitness,
    beneish_witness,
    unavailable_beneish_witness,
    validate_witness,
)
from src.contracts import validate_contract
from src.envelope import AgentInfo, Envelope
from src.facts import Fact
from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score, classify

ACCEPTED_ACCESSION = "0000320193-25-000079"

BASELINE_INDICES = {
    "dsri": 1.0,
    "gmi": 1.0,
    "aqi": 1.0,
    "sgi": 1.0,
    "depi": 1.0,
    "sgai": 1.0,
    "lvgi": 1.0,
    "tata": 0.0,
}


def _fact(**overrides) -> Fact:
    has_instant = "instant" in overrides
    payload = {
        "id": "fact_1",
        "company": "ACME",
        "concept": "AccountsReceivable",
        "unit": "USD",
        "currency": "USD",
        "value": 100.0,
        "filing_accession": ACCEPTED_ACCESSION,
        "availability_time": "2025-02-01T00:00:00Z",
        "retrieval_time": "2025-02-02T00:00:00Z",
        "source_location": {"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"},
        "accounting_basis": "US-GAAP",
        "extractor_version": "sec_companyfacts_v1",
        **({} if has_instant else {"period_start": "2024-01-01", "period_end": "2024-12-31"}),
        **overrides,
    }
    return Fact.from_dict(payload)


def _make_a3_envelope(artifacts: dict, **kwargs) -> Envelope:
    default_artifacts = {
        "AltmanZ": {"score": 3.8, "classification": "Safe Zone"},
        "OCF_vs_NI_Flag": "Aligned",
        "PEG_TTM": 1.2,
        "PEG_Fwd": 1.1,
        "HypeFlag": "Reality",
        "PeerTable": [],
        "TechnicalSet": {"SMA_50": 100.0, "SMA_200": 95.0, "RSI_14": 55.0},
    }
    merged_artifacts = {**default_artifacts, **artifacts}
    return Envelope(
        schema_version="2.1",
        agent=AgentInfo(id="A3", name="Quant-Valuation-Technicals"),
        timestamp="2025-06-01T00:00:00Z",
        params={"ticker": "ACME", "analysis_date": "2025-06-01"},
        gates={"passed": True, "notes": []},
        sections_md={
            "SECTION_4_FINANCIAL_FORENSICS": "# Forensics",
            "SECTION_5_VALUATION": "# Valuation",
            "SECTION_12_TECHNICALS": "# Technicals",
        },
        artifacts=merged_artifacts,
        sources=[],
        warnings=[],
        metrics={"data_checksum": "sha256:abc"},
        confidence=0.9,
        **kwargs,
    )


# =============================================================================
# 1. Typed metadata roundtrip
# =============================================================================


def test_calculation_witness_metadata_typed_roundtrip():
    """CalculationWitness preserves new explicit metadata when provided, and preserves legacy dict shape when omitted."""
    # When explicit metadata is provided
    derivations = {"dsri": {"expression": "a / b", "operand_fact_ids": ["f1", "f2"]}}
    input_periods = {"dsri": {"period_start": "2024-01-01", "period_end": "2024-12-31"}}
    input_bases = {"dsri": "US-GAAP"}

    witness = CalculationWitness(
        formula_id="test_formula",
        formula_version="1.0",
        calculator_id="test_calc",
        result=2.5,
        inputs={"dsri": 2.5},
        derivations=derivations,
        input_periods=input_periods,
        input_bases=input_bases,
    )

    data = witness.to_dict()
    assert data["derivations"] == derivations
    assert data["input_periods"] == input_periods
    assert data["input_bases"] == input_bases

    # When metadata is omitted, to_dict must preserve exact legacy keys without unexpected None fields
    legacy_witness = CalculationWitness(
        formula_id="test_formula",
        formula_version="1.0",
        calculator_id="test_calc",
        result=2.5,
        inputs={"dsri": 2.5},
    )
    legacy_data = legacy_witness.to_dict()
    assert "derivations" not in legacy_data
    assert "input_periods" not in legacy_data
    assert "input_bases" not in legacy_data
    assert set(legacy_data.keys()) == {
        "formula_id",
        "formula_version",
        "calculator_id",
        "result",
        "inputs",
        "parameters",
        "input_fact_ids",
        "units",
        "provenance",
        "notes",
    }


def test_beneish_witness_keyword_args_roundtrip():
    """beneish_witness accepts new metadata keyword args and preserves explicit metadata in to_dict."""
    derivations = {"dsri": {"expression": "a / b", "operand_fact_ids": ["f1", "f0"]}}
    periods = {"dsri": {"instant": "2024-12-31"}}
    bases = {"dsri": "US-GAAP"}

    score = beneish_score(**BASELINE_INDICES)
    witness = beneish_witness(
        indices=BASELINE_INDICES,
        result=score,
        derivations=derivations,
        input_periods=periods,
        input_bases=bases,
    )

    data = witness.to_dict()
    assert data["derivations"] == derivations
    assert data["input_periods"] == periods
    assert data["input_bases"] == bases
    assert data["result"] == score

    # Default call without metadata preserves legacy dict
    default_witness = beneish_witness(indices=BASELINE_INDICES, result=score)
    default_data = default_witness.to_dict()
    assert "derivations" not in default_data
    assert "input_periods" not in default_data
    assert "input_bases" not in default_data


# =============================================================================
# 2. Elimination of derivations-without-input-map bypass
# =============================================================================


def test_missing_input_fact_ids_fails_when_derivation_cannot_bind():
    """A non-empty derivations mapping with absent input_fact_ids must fail when bindings cannot be resolved."""
    witness = {
        "formula_id": "beneish_m_score",
        "formula_version": "2.0.0",
        "calculator_id": "beneish_m_score",
        "result": beneish_score(**BASELINE_INDICES),
        "inputs": BASELINE_INDICES,
        # Notice: input_fact_ids is absent!
        "derivations": {
            "dsri": {"expression": "a / b", "operand_fact_ids": ["non_existent_1", "non_existent_2"]}
        },
        "provenance": COMPUTED_BY_DETERMINISTIC,
    }

    # No fact ledger provided: must fail clearly (not silently pass)
    errors = validate_witness(witness)
    assert errors, "derivations with absent input_fact_ids and no ledger must not silently pass"
    assert any("does not exist in the fact ledger" in e or "absent input_fact_ids" in e for e in errors)


def test_missing_input_fact_ids_succeeds_when_derivation_resolves_complete_binding():
    """A non-empty derivations mapping with absent input_fact_ids succeeds if shared binder completely verifies all operands."""
    f1 = _fact(id="f1", value=400.0, unit="USD", instant="2024-12-31")
    f0 = _fact(id="f0", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f1.id: f1, f0.id: f0}

    indices = {**BASELINE_INDICES, "dsri": 2.0}
    score = beneish_score(**indices)

    witness = {
        "formula_id": "beneish_m_score",
        "formula_version": "2.0.0",
        "calculator_id": "beneish_m_score",
        "result": score,
        "inputs": indices,
        # Notice: input_fact_ids is absent
        "derivations": {
            "dsri": {
                "expression": "a / b",
                "operand_fact_ids": [f1.id, f0.id],
                "result_value": 2.0,
            }
        },
        "provenance": COMPUTED_BY_DETERMINISTIC,
    }

    errors = validate_witness(witness, fact_ledger=ledger, expected_company="ACME", cutoff="2025-06-01")
    assert not errors, f"completely resolvable derivation should pass safely: {errors}"


def test_non_mapping_input_fact_ids_fails_clearly():
    """A non-mapping input_fact_ids (e.g. string or list) must fail clearly."""
    f1 = _fact(id="f1", value=200.0)
    ledger = {f1.id: f1}

    witness = {
        "formula_id": "beneish_m_score",
        "formula_version": "2.0.0",
        "calculator_id": "beneish_m_score",
        "result": beneish_score(**BASELINE_INDICES),
        "inputs": BASELINE_INDICES,
        "input_fact_ids": "invalid_string_instead_of_mapping",
        "derivations": {
            "dsri": {"expression": "a", "operand_fact_ids": [f1.id]}
        },
        "provenance": COMPUTED_BY_DETERMINISTIC,
    }

    errors = validate_witness(witness, fact_ledger=ledger)
    assert any("must be a mapping" in e for e in errors)


# =============================================================================
# 3. validate_contract runtime integration: valid calculations
# =============================================================================


def test_validate_contract_valid_direct_witness():
    """validate_contract passes valid embedded calculation witnesses with fact ledger."""
    f1 = _fact(id="f_dsri", value=1.0, unit="ratio")
    ledger = {f1.id: f1}

    score = beneish_score(**BASELINE_INDICES)
    witness = beneish_witness(
        indices=BASELINE_INDICES,
        fact_ids={"dsri": f1.id},
        result=score,
    ).to_dict()

    beneish_art = {
        "score": round(score, 2),
        "raw_score": score,
        "classification": classify(score),
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": "deterministic",
        "calculation_witness": witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})
    result = validate_contract(env, fact_ledger=ledger, strict=True)
    assert result.valid, result.errors


def test_validate_contract_valid_derived_witness():
    """validate_contract passes valid embedded witness containing derivations with fact ledger."""
    f_curr = _fact(id="f_curr", value=400.0, unit="USD", instant="2024-12-31")
    f_prev = _fact(id="f_prev", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f_curr.id: f_curr, f_prev.id: f_prev}

    indices = {**BASELINE_INDICES, "dsri": 2.0}
    score = beneish_score(**indices)

    derivations = {
        "dsri": {
            "expression": "a / b",
            "operand_fact_ids": [f_curr.id, f_prev.id],
            "result_value": 2.0,
        }
    }

    witness = beneish_witness(
        indices=indices,
        result=score,
        derivations=derivations,
    ).to_dict()

    beneish_art = {
        "score": round(score, 2),
        "raw_score": score,
        "classification": classify(score),
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": "deterministic",
        "calculation_witness": witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})
    result = validate_contract(env, fact_ledger=ledger, strict=True)
    assert result.valid, result.errors


# =============================================================================
# 4. validate_contract: invalid arithmetic and result replay
# =============================================================================


def test_validate_contract_rejects_arithmetic_mismatch():
    """validate_contract rejects a witness whose recorded result disagrees with recomputed arithmetic."""
    score = beneish_score(**BASELINE_INDICES)
    # Fabricate result
    witness = beneish_witness(indices=BASELINE_INDICES, result=999.0).to_dict()

    beneish_art = {
        "score": round(score, 2),
        "raw_score": score,
        "classification": classify(score),
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": "deterministic",
        "calculation_witness": witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})
    result = validate_contract(env, strict=True)
    assert not result.valid
    assert any("arithmetic mismatch" in e.lower() or "disagrees with deterministic replay" in e.lower() for e in result.errors)


def test_validate_contract_rejects_derived_arithmetic_mismatch():
    """validate_contract rejects a derived witness whose claimed input value contradicts AST evaluation."""
    f_curr = _fact(id="f_curr", value=400.0, unit="USD", instant="2024-12-31")
    f_prev = _fact(id="f_prev", value=200.0, unit="USD", instant="2023-12-31")
    ledger = {f_curr.id: f_curr, f_prev.id: f_prev}

    # 400 / 200 is 2.0, but claimed value is 5.0
    indices = {**BASELINE_INDICES, "dsri": 5.0}
    score = beneish_score(**indices)

    derivations = {
        "dsri": {
            "expression": "a / b",
            "operand_fact_ids": [f_curr.id, f_prev.id],
            "result_value": 2.0,
        }
    }

    witness = beneish_witness(indices=indices, result=score, derivations=derivations).to_dict()

    beneish_art = {
        "score": round(score, 2),
        "raw_score": score,
        "classification": classify(score),
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": "deterministic",
        "calculation_witness": witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})
    result = validate_contract(env, fact_ledger=ledger, strict=True)
    assert not result.valid
    assert any("does not match recomputed derived value" in e for e in result.errors)


# =============================================================================
# 5. validate_contract: invalid provenance and malformed witnesses
# =============================================================================


def test_validate_contract_rejects_invalid_provenance_string():
    """Unknown provenance values must reject at runtime."""
    score = beneish_score(**BASELINE_INDICES)
    witness = beneish_witness(indices=BASELINE_INDICES, result=score).to_dict()
    witness["provenance"] = "unverified_llm_guess"

    beneish_art = {
        "score": round(score, 2),
        "raw_score": score,
        "classification": classify(score),
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": "deterministic",
        "calculation_witness": witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})
    result = validate_contract(env)
    assert not result.valid
    assert any("unknown witness provenance" in e for e in result.errors)


def test_validate_contract_rejects_model_reported_in_strict_mode():
    """PROVENANCE_MODEL_REPORTED rejects in strict mode."""
    witness = {
        "formula_id": "beneish_m_score",
        "formula_version": "2.0.0",
        "calculator_id": "beneish_m_score",
        "result": -2.5,
        "inputs": {},
        "provenance": PROVENANCE_MODEL_REPORTED,
        "notes": "Model reported unverified score",
    }
    beneish_art = {
        "score": -2.5,
        "raw_score": -2.5,
        "classification": "Unlikely Manipulator",
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": PROVENANCE_MODEL_REPORTED,
        "calculation_witness": witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})
    result = validate_contract(env, strict=True)
    assert not result.valid
    assert any("model-reported" in e or "model_reported_unverified" in e for e in result.errors)


def test_validate_contract_rejects_malformed_witness_object():
    """Non-dict and missing structural fields in calculation_witness must reject."""
    # Primitive string instead of dict
    env_str = _make_a3_envelope({
        "BeneishM": {
            "score": -2.5,
            "raw_score": -2.5,
            "classification": "Unlikely Manipulator",
            "feasible": True,
            "calculator_version": "2.0.0",
            "provenance": "deterministic",
            "calculation_witness": "not_a_valid_witness_object",
        }
    })
    res_str = validate_contract(env_str, strict=True)
    assert not res_str.valid
    assert any("calculation witness must be a dictionary" in e for e in res_str.errors)

    # Missing formula_id
    env_bad = _make_a3_envelope({
        "BeneishM": {
            "score": -2.5,
            "raw_score": -2.5,
            "classification": "Unlikely Manipulator",
            "feasible": True,
            "calculator_version": "2.0.0",
            "provenance": "deterministic",
            "calculation_witness": {
                "calculator_id": "calc",
                "provenance": "deterministic",
                "result": -2.5,
                "inputs": BASELINE_INDICES,
            },
        }
    })
    res_bad = validate_contract(env_bad, strict=True)
    assert not res_bad.valid
    assert any("missing or non-string 'formula_id'" in e for e in res_bad.errors)


# =============================================================================
# 6. Source binding checks through validate_contract
# =============================================================================


def test_validate_contract_rejects_source_value_mismatch():
    """validate_contract rejects when claimed input value differs from fact in ledger."""
    f1 = _fact(id="f_rev", value=100.0, unit="USD")
    ledger = {f1.id: f1}

    # Witness claims 150.0 instead of 100.0
    art = {
        "revenue_metric": {
            "calculation_witness": {
                "formula_id": "rev_check",
                "formula_version": "1.0",
                "calculator_id": "calc",
                "result": 150.0,
                "inputs": {"rev": 150.0},
                "input_fact_ids": {"rev": f1.id},
                "units": {"rev": "USD"},
                "provenance": COMPUTED_BY_DETERMINISTIC,
            }
        }
    }
    env = _make_a3_envelope(art)
    result = validate_contract(env, fact_ledger=ledger, strict=True)
    assert not result.valid
    assert any("does not equal the referenced fact's value" in e for e in result.errors)


def test_validate_contract_rejects_wrong_company_derivation_operand():
    """Derivation operand from another company must be rejected by validate_contract."""
    f_other = _fact(id="f_other", company="WRONG_CORP", value=100.0)
    f_acme = _fact(id="f_acme", company="ACME", value=100.0)
    ledger = {f_other.id: f_other, f_acme.id: f_acme}

    art = {
        "ratio_metric": {
            "calculation_witness": {
                "formula_id": "ratio_check",
                "formula_version": "1.0",
                "calculator_id": "calc",
                "result": 1.0,
                "inputs": {"r": 1.0},
                "units": {"r": "ratio"},
                "derivations": {
                    "r": {"expression": "a / b", "operand_fact_ids": [f_other.id, f_acme.id]}
                },
                "provenance": COMPUTED_BY_DETERMINISTIC,
            }
        }
    }
    env = _make_a3_envelope(art)
    result = validate_contract(env, fact_ledger=ledger, expected_company="ACME", strict=True)
    assert not result.valid
    assert any("not the run's company" in e for e in result.errors)


def test_validate_contract_rejects_lookahead_cutoff_violation():
    """Derivation operand available after cutoff must be rejected by validate_contract."""
    f_future = _fact(id="f_future", availability_time="2026-01-01T00:00:00Z", value=100.0)
    f_valid = _fact(id="f_valid", availability_time="2024-01-01T00:00:00Z", value=100.0)
    ledger = {f_future.id: f_future, f_valid.id: f_valid}

    art = {
        "ratio_metric": {
            "calculation_witness": {
                "formula_id": "ratio_check",
                "formula_version": "1.0",
                "calculator_id": "calc",
                "result": 1.0,
                "inputs": {"r": 1.0},
                "units": {"r": "ratio"},
                "derivations": {
                    "r": {"expression": "a / b", "operand_fact_ids": [f_future.id, f_valid.id]}
                },
                "provenance": COMPUTED_BY_DETERMINISTIC,
            }
        }
    }
    env = _make_a3_envelope(art)
    result = validate_contract(env, fact_ledger=ledger, cutoff="2025-01-01", strict=True)
    assert not result.valid
    assert any("look-ahead violation" in e for e in result.errors)


def test_validate_contract_rejects_arbitrary_constant_derivation():
    """Derivations containing arbitrary numeric literals must reject at runtime."""
    f1 = _fact(id="f1", value=100.0)
    ledger = {f1.id: f1}

    art = {
        "ratio_metric": {
            "calculation_witness": {
                "formula_id": "ratio_check",
                "formula_version": "1.0",
                "calculator_id": "calc",
                "result": 2.0,
                "inputs": {"r": 2.0},
                "units": {"r": "ratio"},
                "derivations": {
                    "r": {"expression": "a + 2", "operand_fact_ids": [f1.id]}
                },
                "provenance": COMPUTED_BY_DETERMINISTIC,
            }
        }
    }
    env = _make_a3_envelope(art)
    result = validate_contract(env, fact_ledger=ledger, strict=True)
    assert not result.valid
    assert any("arbitrary numeric constants" in e for e in result.errors)


# =============================================================================
# 7. Backward compatibility for no-ledger mocks and legacy envelopes
# =============================================================================


def test_no_ledger_mocks_remain_compatible():
    """Ordinary mocks without citations or ledger pass validate_contract in both lenient and strict modes."""
    score = beneish_score(**BASELINE_INDICES)
    mock_witness = beneish_witness(indices=BASELINE_INDICES, result=score).to_dict()

    beneish_art = {
        "score": round(score, 2),
        "raw_score": score,
        "classification": classify(score),
        "feasible": True,
        "calculator_version": "2.0.0",
        "provenance": "deterministic",
        "calculation_witness": mock_witness,
    }

    env = _make_a3_envelope({"BeneishM": beneish_art})

    # Lenient mode without ledger
    res_lenient = validate_contract(env, fact_ledger=None, strict=False)
    assert res_lenient.valid, res_lenient.errors

    # Strict mode without ledger
    res_strict = validate_contract(env, fact_ledger=None, strict=True)
    assert res_strict.valid, res_strict.errors


def test_legacy_envelope_without_witnesses_remains_compatible():
    """Envelopes without calculation_witness validate identically to legacy behavior."""
    env = _make_a3_envelope({
        "BeneishM": {
            "score": -2.5,
            "raw_score": -2.5,
            "classification": "Unlikely Manipulator",
            "feasible": True,
            # No calculation_witness entry
        }
    })
    # Non-strict legacy allows bare score with warning
    res = validate_contract(env, strict=False)
    assert res.valid
    assert any("bare score" in w for w in res.warnings)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 10**400])
def test_model_reported_invalid_results_are_not_disclosure_warnings(value):
    env = _make_a3_envelope({"BeneishM": {
        "score": -2.5, "raw_score": -2.5, "classification": "Unlikely Manipulator",
        "feasible": True, "provenance": PROVENANCE_MODEL_REPORTED,
        "calculation_witness": {
            "formula_id": "beneish_m_score", "result": value,
            "provenance": PROVENANCE_MODEL_REPORTED,
        },
    }})
    result = validate_contract(env, strict=False)
    assert not result.valid
    assert any("finite numeric" in error for error in result.errors)


@pytest.mark.parametrize("mismatch", [False, True])
def test_model_agent_receives_ledger_and_preserves_source_metadata(tmp_path, mismatch):
    import json
    from src.agents.base import AgentContext
    from src.agents.model import ModelAgent

    fact = _fact(id="fact_dsri", value=2.0 if mismatch else 1.0,
                 unit="ratio", currency=None, instant="2024-12-31")
    witness = beneish_witness(indices=BASELINE_INDICES,
                              result=beneish_score(**BASELINE_INDICES),
                              fact_ids={"dsri": fact.id}).to_dict()
    env = _make_a3_envelope({"BeneishM": {
        "indices": BASELINE_INDICES, "score": witness["result"],
        "raw_score": witness["result"], "classification": classify(witness["result"]),
        "feasible": True, "provenance": "deterministic", "calculation_witness": witness,
    }})

    class OfflineBackend:
        def generate(self, prompt):
            return json.dumps(env.to_dict())

    context = AgentContext(
        run_id="context_binding", params=dict(env.params), depth_config={},
        focus_overrides={}, frozen_checksum="sha256:abc", workdir=tmp_path,
        provenance_path=tmp_path / "provenance.log", fact_ledger={fact.id: fact},
    )
    agent = ModelAgent("A3", "Quant-Valuation-Technicals", OfflineBackend(),
                       max_retries=0, strict=False)
    if mismatch:
        with pytest.raises(RuntimeError, match="claimed value"):
            agent.run(context)
    else:
        output = agent.run(context)
        actual = output.envelope.artifacts["BeneishM"]["calculation_witness"]
        assert actual["input_fact_ids"] == {"dsri": fact.id}
        assert not validate_witness(actual, fact_ledger=context.fact_ledger,
                                    expected_company="ACME", cutoff="2025-06-01")


def test_validate_contract_detects_list_of_list_witness():
    """Witness embedded inside nested list-of-list is discovered and validated."""
    bad_witness = {
        "formula_id": "identity", "formula_version": "1.0", "calculator_id": "test",
        "provenance": "invalid_provenance", "inputs": {"a": 1.0}, "result": 999.0,
    }
    env = _make_a3_envelope({"matrix": [[{"sub": bad_witness}]]})
    res = validate_contract(env)
    assert not res.valid
    assert any("matrix[0][0].sub" in err and "invalid_provenance" in err for err in res.errors)


def test_validate_contract_scan_depth_exhaustion_is_explicit_error():
    """Artifacts exceeding scan depth limit produce an explicit validation error."""
    deep = {"layer": 1}
    for _ in range(12):
        deep = {"layer": deep}
    env = _make_a3_envelope({"deep": deep})
    res = validate_contract(env)
    assert not res.valid
    assert any("scan depth limit exceeded" in err for err in res.errors)

