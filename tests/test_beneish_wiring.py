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
from src.agents.model import ModelAgent, ModelBackend, _normalize_beneish
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


# --------------------------------------------------- the strict normalization seam
#
# Every test above calls ``_normalize_beneish`` directly and hands the *result*
# to the contract. They prove both halves work and never cross the seam between
# them. A live AMD run exposed that gap: strict mode validated the model's raw
# payload and aborted the whole paid run on a fully compliant response, because
# the normalization only ran on the lenient path.


class _NeverCalledBackend(ModelBackend):
    """The seam under test is local normalization; no provider call is allowed."""

    def __init__(self) -> None:
        self.model = "recording"

    def generate(self, prompt: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("strict normalization must not call the provider")


def _strict_a3_agent() -> ModelAgent:
    from src.agents.registry import model_registry

    backend = _NeverCalledBackend()
    return model_registry(backend, provider="recording", strict=True).resolve("A3")


def _strict_context(tmp_path) -> AgentContext:
    return AgentContext(
        run_id="strict_beneish",
        params={
            "ticker": "AMD",
            "company": "Advanced Micro Devices, Inc.",
            "analysis_date": "2026-10-11",
            "weights": "default",
        },
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:frozen",
        workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        source_documents=[
            {
                "type": "SEC 10-K",
                "url": "https://www.sec.gov/Archives/edgar/data/0000002488/amd-10k.htm",
                "doc_date": "2026-02-04",
                "accessed": "2026-10-11T00:00:00Z",
                "verification": "verified",
            }
        ],
        source_text="",
        evidence_checksum="sha256:pack",
        envelopes={},
    )


def _raw_a3_payload(beneish):
    """The shape the A3 prompt asks a live model for, before runner normalization."""
    return {
        "schema_version": "2.1",
        "agent": {"id": "A3", "name": "Quant-Valuation-Technicals"},
        "gates": {"passed": True, "notes": []},
        "sections_md": {
            "SECTION_4_FINANCIAL_FORENSICS": "# Forensics",
            "SECTION_5_VALUATION": "# Valuation",
            "SECTION_12_TECHNICALS": "# Technicals",
        },
        "artifacts": {
            "AltmanZ": {"score": 43.77, "classification": "Safe"},
            "BeneishM": beneish,
            "OCF_vs_NI_Flag": "Aligned",
            "PEG_TTM": 1.2,
            "PEG_Fwd": 1.1,
            "HypeFlag": "Reality",
            "PeerTable": [],
            "TechnicalSet": {"SMA_50": 100.0, "SMA_200": 95.0, "RSI_14": 55.0},
        },
        "sources": [],
        "warnings": [],
        "metrics": {"data_checksum": "sha256:abc"},
        "confidence": 0.9,
    }


def test_strict_path_recomputes_model_supplied_indices(tmp_path):
    """
    Live regression (AMD, 2026-10-11): the model supplied all eight index ratios
    and no provenance label — exactly what the A3 prompt asks for — and strict
    mode aborted the run with "BeneishM carries an unlabelled legacy score". The
    prompt promises the runner recomputes the score, so strict normalization must
    resolve that artifact before the contract sees it.
    """
    payload = _raw_a3_payload(
        {
            "indices": {**ALL_ONE, "tata": -0.0202},
            "score": -1.89,
            "classification": "Unlikely Manipulator",
            "feasible": True,
            "unavailable_reason": None,
        }
    )

    out = _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))

    beneish = out["artifacts"]["BeneishM"]
    assert beneish["provenance"] == "deterministic"
    assert set(beneish["calculation_witness"]["inputs"]) == {*ALL_ONE, "tata"}
    # The runner's number wins; the model's own -1.89 is never adopted.
    assert beneish["score"] == round(beneish["raw_score"], 2)
    assert beneish["score"] != -1.89


def test_a3_forensics_renders_the_calculated_score(tmp_path):
    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}})
    out = _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))
    text = out["sections_md"]["SECTION_4_FINANCIAL_FORENSICS"]
    assert f"Beneish M-Score: **{out['artifacts']['BeneishM']['score']}**" in text
    assert "# Forensics" in text


def test_a3_forensics_rejects_model_written_beneish_equation(tmp_path):
    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}})
    payload["sections_md"]["SECTION_4_FINANCIAL_FORENSICS"] = (
        "# Forensics\nBeneish M-score = ... + 4.037(TATA) + 0.0327(LVGI) = -1.96"
    )
    with pytest.raises(ValueError, match="runner.*Beneish"):
        _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))


def test_a3_forensics_does_not_reject_a_company_named_tata(tmp_path):
    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}})
    payload["sections_md"]["SECTION_4_FINANCIAL_FORENSICS"] = "# Forensics\nTata reported higher cash flow."
    out = _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))
    assert "Tata reported higher cash flow" in out["sections_md"]["SECTION_4_FINANCIAL_FORENSICS"]


def test_a3_forensics_rejects_beneish_in_an_alternate_section_4_key(tmp_path):
    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}})
    payload["sections_md"]["section_4_extra"] = "Beneish M-Score: -1.96"
    with pytest.raises(ValueError, match="runner.*Beneish"):
        _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))


def test_audit_rejects_resealed_report_with_wrong_runner_score(tmp_path):
    from src.run_semantic_audit import (
        AuditReport,
        _audit_final_artifacts,
        _load_manifest,
    )
    from src.utils.checksum import checksum_file

    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}})
    envelope = Envelope.from_dict(_strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path)))
    section = envelope.sections_md["SECTION_4_FINANCIAL_FORENSICS"]
    path = tmp_path / "report.md"
    path.write_text(section.replace("-2.57", "-1.96"), encoding="utf-8")
    (tmp_path / "run_manifest.json").write_text(json.dumps({"outputs": {
        "report": {"path": path.name, "sha256": checksum_file(path)}
    }}), encoding="utf-8")
    manifest, manifest_problem = _load_manifest(tmp_path)
    errors = _audit_final_artifacts(
        tmp_path, {"A3": envelope}, AuditReport(), manifest, manifest_problem
    )
    assert any("Beneish" in error for error in errors)


def test_strict_path_rejects_a_missing_beneish_artifact(tmp_path):
    """
    The seam adopts a recomputation, never a placeholder. An A3 that produced no
    Beneish artifact at all must still fail closed rather than being handed the
    runner's own "unavailable" text.
    """
    payload = _raw_a3_payload(None)
    payload["artifacts"].pop("BeneishM")

    with pytest.raises(ValueError) as excinfo:
        _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))

    assert "BeneishM" in str(excinfo.value)


def test_strict_path_rejects_an_unlabelled_bare_score(tmp_path):
    """A bare score with no ratios is still an error, with the message unchanged."""
    payload = _raw_a3_payload(
        {"score": -2.5, "classification": "Unlikely Manipulator", "feasible": True}
    )

    with pytest.raises(ValueError) as excinfo:
        _strict_a3_agent()._normalize_envelope(payload, _strict_context(tmp_path))

    assert "unlabelled legacy score" in str(excinfo.value)


def test_lenient_path_with_documented_gap_still_adopts_runner_beneish(tmp_path):
    """B9: a valid A3 response that documents a gap took an early return that
    skipped the runner's Beneish recomputation and section-4 block."""
    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}, "score": -1.89})
    payload["artifacts"]["HypeFlag"] = None
    payload["metrics"]["missing_data"] = [{"field": "HypeFlag", "reason": "No forward estimates in evidence."}]
    agent = _strict_a3_agent()
    agent.strict = False

    out = agent._normalize_envelope(payload, _strict_context(tmp_path))

    beneish = out["artifacts"]["BeneishM"]
    assert beneish["provenance"] == "deterministic"
    assert beneish["score"] != -1.89
    assert out["metrics"].get("runner_beneish_block") is True
    assert f"Beneish M-Score: **{beneish['score']}**" in out["sections_md"]["SECTION_4_FINANCIAL_FORENSICS"]


def test_unreadable_numbers_are_recorded_per_field_when_placeholders_are_used(tmp_path):
    payload = _raw_a3_payload({"indices": {**ALL_ONE, "tata": -0.0202}})
    payload["artifacts"]["PEG_TTM"] = "N/A"
    payload["artifacts"]["PEG_Fwd"] = "1.4"
    agent = _strict_a3_agent()
    agent.strict = False

    out = agent._normalize_envelope(payload, _strict_context(tmp_path))

    injected = out["metrics"]["injected_defaults"]
    assert "PEG_TTM" in injected
    assert "PEG_Fwd" not in injected
    assert out["artifacts"]["PEG_Fwd"] == 1.4
