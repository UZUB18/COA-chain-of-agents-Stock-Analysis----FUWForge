"""
Wiring regression: a reported Beneish number must come from the calculator.

The golden fixtures in ``test_finance_formula.py`` prove the arithmetic is
correct in isolation. These tests prove it is actually *reached* — otherwise the
correction changes a helper nobody calls and the defect stays live in real runs.

The trace that motivated this: before the fix, ``beneish_m_score`` was imported
nowhere in a production path. ``mock.py`` produced ``-2.82 + delta * 0.2`` and
``model.py`` passed through a model-supplied number with a ``-2.8`` fallback, so
fixing the helper alone would have changed nothing observable.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.model import ModelAgent, _normalize_beneish
from src.contracts import validate_contract
from src.envelope import AgentInfo, Envelope
from src.utils.beneish import beneish_score

ALL_ONE = {
    "dsri": 1.0,
    "gmi": 1.0,
    "aqi": 1.0,
    "sgi": 1.0,
    "depi": 1.0,
    "sgai": 1.0,
    "lvgi": 1.0,
}


def _a3_envelope(beneish: dict) -> Envelope:
    return Envelope(
        schema_version="2.1",
        agent=AgentInfo(id="A3", name="Quant-Valuation-Technicals"),
        timestamp="2026-02-01T00:00:00Z",
        params={"ticker": "TEST", "analysis_date": "2026-02-01"},
        gates={"passed": True, "notes": []},
        sections_md={
            "SECTION_4_FINANCIAL_FORENSICS": "# Forensics",
            "SECTION_5_VALUATION": "# Valuation",
            "SECTION_12_TECHNICALS": "# Technicals",
        },
        artifacts={
            "AltmanZ": {"score": 3.8, "classification": "Safe Zone"},
            "BeneishM": beneish,
            "OCF_vs_NI_Flag": "Aligned",
            "PEG_TTM": 1.2,
            "PEG_Fwd": 1.1,
            "HypeFlag": "Reality",
            "PeerTable": [],
            "TechnicalSet": {"SMA_50": 100.0, "SMA_200": 95.0, "RSI_14": 55.0},
        },
        sources=[],
        warnings=[],
        metrics={"data_checksum": "sha256:abc"},
        confidence=0.9,
    )


# ------------------------------------------------- production call-path proof


def test_mock_run_beneish_comes_from_deterministic_calculator():
    """The mock A3 producer must call the calculator, not hard-code a number."""
    from src.agents.registry import default_registry

    with tempfile.TemporaryDirectory() as tmp:
        registry = default_registry(provider="mock")
        context = AgentContext(
            run_id="t",
            params={"ticker": "TEST", "analysis_date": "2026-02-01"},
            depth_config={},
            envelopes={},
            focus_overrides={},
            frozen_checksum="sha256:abc",
            workdir=Path(tmp),
            provenance_path=Path(tmp) / "p.log",
        )
        envelope = registry.resolve("A3").run(context).envelope
        beneish = envelope.artifacts["BeneishM"]

        assert beneish["provenance"] == "deterministic"
        witness = beneish["calculation_witness"]
        assert witness["formula_id"] == "beneish_m_score"
        # The witness inputs are sufficient to recompute the score exactly.
        expected = beneish_score(**witness["inputs"])
        assert beneish["raw_score"] == pytest.approx(expected)
        assert beneish["score"] == pytest.approx(round(expected, 2))
        # And it carries the versioned formula identity.
        assert beneish["calculator_version"]
        assert beneish["formula_source"]


def test_model_supplied_indices_are_recomputed_and_model_score_ignored():
    """A model cannot report its way to a score the runner did not compute."""
    injected: list[str] = []
    # The model deliberately reports a wrong score alongside correct indices.
    result = _normalize_beneish(
        {
            "indices": {**ALL_ONE, "tata": 0.1},
            "score": -9.99,
            "classification": "Unlikely Manipulator",
            "feasible": True,
        },
        injected,
    )
    assert result["provenance"] == "deterministic"
    assert result["raw_score"] == pytest.approx(-2.0121)
    assert result["score"] == pytest.approx(-2.01)
    assert result["classification"] == "Unlikely Manipulator"
    # Witness lets a reviewer recompute it.
    assert result["calculation_witness"]["inputs"]["tata"] == 0.1
    assert not injected


def test_bare_model_score_is_labelled_not_silently_accepted():
    """No indices -> the number survives, but it is flagged as unverified."""
    injected: list[str] = []
    result = _normalize_beneish({"score": -2.5, "classification": "Unlikely Manipulator"}, injected)
    assert result["provenance"] == "model_reported_unverified"
    assert result["score"] == pytest.approx(-2.5)
    assert result["calculation_witness"]["provenance"] == "model_reported_unverified"


def test_missing_beneish_no_longer_invents_a_clean_score():
    """The -2.8 / 'Unlikely Manipulator' fabrication is gone."""
    injected: list[str] = []
    result = _normalize_beneish(None, injected)
    assert result["score"] is None
    assert result["feasible"] is False
    assert result["classification"] == "Unavailable"
    assert result["unavailable_reason"]
    assert "BeneishM" in injected


def test_partial_indices_are_not_padded_with_neutral_values():
    """Five of eight indices must not silently become eight."""
    injected: list[str] = []
    partial = {k: 1.0 for k in ("dsri", "gmi", "aqi", "sgi", "depi")}
    result = _normalize_beneish({"indices": partial}, injected)
    # Treated as absent (no `indices` usable) -> bare-scalar path -> unavailable.
    assert result["score"] is None
    assert result["feasible"] is False


def test_nonfinite_index_is_rejected_not_computed():
    injected: list[str] = []
    result = _normalize_beneish({"indices": {**ALL_ONE, "tata": float("nan")}}, injected)
    assert result["score"] is None
    assert result["feasible"] is False


# --------------------------------------------------- contract enforcement


def test_contract_rejects_bare_unlabelled_score_in_strict_mode():
    """Strict mode rejects the legacy unlabelled form (deterministic required)."""
    bare = {"score": -2.5, "classification": "Unlikely Manipulator", "feasible": True}
    env = _a3_envelope(bare)
    strict_result = validate_contract(env, strict=True)
    assert not strict_result.valid
    assert any("unlabelled legacy score" in e for e in strict_result.errors)
    # Non-strict (controller/legacy) preserves it but flags it as a warning.
    lenient = validate_contract(env, strict=False)
    assert lenient.valid
    assert any("bare score" in w for w in lenient.warnings)


def test_contract_rejects_model_reported_in_strict_mode():
    result = _normalize_beneish({"score": -2.5}, [])
    env = _a3_envelope(result)
    strict_result = validate_contract(env, strict=True)
    assert not strict_result.valid
    assert any("model-reported" in e for e in strict_result.errors)


def test_contract_requires_all_eight_indices_in_witness():
    """A 'deterministic' witness with missing ratios is not trustworthy."""
    result = _normalize_beneish({"indices": {**ALL_ONE, "tata": 0.1}}, [])
    # Tamper: remove two inputs from the witness.
    result["calculation_witness"]["inputs"].pop("sgai")
    result["calculation_witness"]["inputs"].pop("lvgi")
    env = _a3_envelope(result)
    strict_result = validate_contract(env, strict=True)
    assert not strict_result.valid
    assert any("eight index ratios" in e for e in strict_result.errors)


def test_contract_accepts_fully_deterministic_witness():
    result = _normalize_beneish({"indices": {**ALL_ONE, "tata": 0.1}}, [])
    env = _a3_envelope(result)
    strict_result = validate_contract(env, strict=True)
    assert strict_result.valid, strict_result.errors


@pytest.mark.parametrize("field,value", [
    ("score", 123.0), ("raw_score", 123.0),
    ("classification", "High Manipulation Risk"),
    ("calculator_version", "1.0.0"),
])
def test_contract_replays_and_binds_deterministic_artifact(field, value):
    result = _normalize_beneish({"indices": {**ALL_ONE, "tata": 0.0}}, [])
    result[field] = value
    assert not validate_contract(_a3_envelope(result), strict=True).valid


@pytest.mark.parametrize("field,value", [
    ("result", 123.0), ("formula_version", "1.0.0"),
    ("units", {key: "USD" for key in (*ALL_ONE, "tata")}),
    ("inputs", {**ALL_ONE, "tata": True}),
])
def test_contract_rejects_invalid_witness_values(field, value):
    result = _normalize_beneish({"indices": {**ALL_ONE, "tata": 0.0}}, [])
    result["calculation_witness"][field] = value
    assert not validate_contract(_a3_envelope(result), strict=True).valid


def test_contract_rejects_number_disguised_as_unavailable():
    result = {"score": -2.8, "feasible": False, "unavailable_reason": "unknown"}
    assert not validate_contract(_a3_envelope(result)).valid