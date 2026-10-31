"""
Regression tests for calculation replay gate, zero coverage, and source binding.

Tests cover:
1. 0 calculation witnesses:
   - Non-gated mode sets calculation_replay to 'not_applicable' (not 'passed').
   - Source-backed gate fails honestly (requires >=1 deterministic replayed witness and >=1 verified financial input).
2. Witness with empty/nonmapping inputs:
   - Does not establish source coverage.
3. Model-only Beneish & honest unavailable calculations:
   - Non-gated mode sets calculation_replay to 'not_replayable' (not 'passed') for honest disclosure.
   - Source-backed gate fails.
   - Dishonest feasible=false claiming deterministic provenance fails validation.
4. Fully sourced replay:
   - Both non-gated and gated modes succeed.
   - Coverage counters correctly report replayed witnesses and verified inputs.
5. Mixed sourced/unsourced inputs:
   - Non-gated succeeds with unverified inputs recorded under unavailable.
   - Gated mode fails due to unverified inputs.
6. Witness with arbitrary extra input labels or unsupported formula:
   - Rejected and never establishes source coverage.
7. Coverage counters in AuditReport.to_dict():
   - Operator visibility into attempted/replayed witnesses and verified/unverified inputs.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.contracts import AGENT_IDS
from src.envelope import Envelope
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.run_audit import semantic_audit, verify_run_semantic
from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score
from src.utils.checksum import checksum_file, envelope_checksum, envelope_content_checksum, sha256_digest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TICKER = "ZERO"
ANALYSIS_DATE = "2026-02-01"


def _create_mock_run(tmp: Path, ticker: str = TICKER) -> Path:
    """Produce a standard complete mock run."""
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Zero Coverage Co",
        ticker=ticker,
        analysis_date=ANALYSIS_DATE,
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("zero_cov_run", params, output_root=tmp)
    return tmp


def _reseal(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["content_checksum"] = envelope_checksum(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _rebind_manifest(run_dir: Path, agent: str, path: Path) -> None:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest.setdefault("roles", {})[agent] = data["content_checksum"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _cascade_references(run_dir: Path, agent: str) -> None:
    for _ in range(10):
        changed = False
        current = {}
        for path in sorted(run_dir.glob("*_envelope.json")):
            agent_id = path.stem.replace("_envelope", "").upper()
            current[agent_id] = json.loads(path.read_text(encoding="utf-8"))["content_checksum"]
        for path in sorted(run_dir.glob("*_envelope.json")):
            agent_id = path.stem.replace("_envelope", "").upper()
            data = json.loads(path.read_text(encoding="utf-8"))
            edited = False
            for entry in data.get("upstream", []):
                parent = entry.get("agent")
                if parent in current and entry.get("content_checksum") != current[parent]:
                    entry["content_checksum"] = current[parent]
                    edited = True
            if edited:
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                _reseal(path)
                _rebind_manifest(run_dir, agent_id, path)
                changed = True
        if not changed:
            return


def _create_evidence_pack(
    evidence_dir: Path,
    ticker: str = TICKER,
    embedded_facts: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    doc_bytes = b"Sample filing document content for SEC testing."
    (evidence_dir / "doc_10k.bytes").write_bytes(doc_bytes)
    (evidence_dir / "doc_10k.txt").write_bytes(doc_bytes)
    doc_sha = sha256_digest(doc_bytes)

    documents = [
        {
            "id": "src_10k_2024",
            "type": "Filing",
            "title": "Annual 10-K",
            "url": "https://sec.gov/edgar/data/320193/000032019325000001.htm",
            "doc_date": "2025-01-15",
            "date_basis": "supplied_manifest",
            "accessed": "2025-01-20T00:00:00Z",
            "content_type": "text/plain",
            "verification": "retrieved",
            "content_sha256": doc_sha,
            "text_sha256": doc_sha,
            "raw_file": "doc_10k.bytes",
            "text_file": "doc_10k.txt",
        }
    ]

    pack_data: Dict[str, Any] = {
        "schema_version": "1",
        "ticker": ticker.upper(),
        "documents": documents,
        "attachments": [],
    }
    if embedded_facts is not None:
        pack_data["facts"] = embedded_facts

    ev_json_path = evidence_dir / "evidence.json"
    ev_json_path.write_text(json.dumps(pack_data, indent=2), encoding="utf-8")
    return ev_json_path


# ----------------------------------------------------------------------
# 1. Zero Calculation Witnesses
# ----------------------------------------------------------------------


def test_zero_witnesses_fails_source_backed_gate_and_reports_counters(tmp_path):
    """
    When a run has 0 calculation witnesses in all envelopes:
    - Non-gated mode: calculation_replay is 'not_applicable' (vacuous 'passed' is prohibited).
    - Source-backed gate: FAILS because >=1 deterministic witness and >=1 verified financial input required.
    - Counters: witnesses_attempted=0, witnesses_replayed=0, inputs_verified=0.
    """
    run_dir = tmp_path / "run_zero_wit"
    _create_mock_run(run_dir)

    # Strip calculation_witness from all envelopes
    for path in sorted(run_dir.glob("*_envelope.json")):
        agent_id = path.stem.replace("_envelope", "").upper()
        data = json.loads(path.read_text(encoding="utf-8"))
        artifacts = data.get("artifacts") or {}
        if agent_id == "A3":
            # Set BeneishM to an honest unavailable state (no witness)
            artifacts["BeneishM"] = {
                "score": None,
                "raw_score": None,
                "classification": "Unavailable",
                "feasible": False,
                "unavailable_reason": "No financial statements available for analysis.",
            }
        for art_val in artifacts.values():
            if isinstance(art_val, dict):
                art_val.pop("calculation_witness", None)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _reseal(path)
        _rebind_manifest(run_dir, agent_id, path)

    _cascade_references(run_dir, "A3")

    # 1. Non-gated audit
    report_nongated = semantic_audit(run_dir, require_source_backed=False)
    assert report_nongated.valid, report_nongated.errors
    # Must NOT be 'passed'
    assert report_nongated.levels["calculation_replay"] in ("not_applicable", "not_replayable")
    assert report_nongated.witnesses_replayed == 0
    assert report_nongated.inputs_verified == 0

    to_dict = report_nongated.to_dict()
    assert to_dict["witnesses_replayed"] == 0
    assert to_dict["inputs_verified"] == 0
    assert to_dict["coverage"]["witnesses_replayed"] == 0
    assert to_dict["coverage"]["inputs_verified"] == 0

    # 2. Source-backed gate must FAIL honestly (not vacuously pass)
    report_gated = semantic_audit(run_dir, require_source_backed=True)
    assert not report_gated.valid
    assert report_gated.levels["calculation_replay"] == "failed"
    assert any("require-source-backed" in err for err in report_gated.errors)
    assert any("no deterministic calculation witnesses replayed" in err for err in report_gated.errors)


# ----------------------------------------------------------------------
# 2. Witness with Empty or Non-Mapping Inputs
# ----------------------------------------------------------------------


def test_witness_empty_inputs_never_establishes_coverage(tmp_path):
    """
    A witness with empty inputs ({}) or non-mapping inputs must never increment
    witnesses_replayed or inputs_verified, and must fail the source-backed gate.
    """
    run_dir = tmp_path / "run_empty_inputs"
    _create_mock_run(run_dir)

    a2_path = run_dir / "a2_envelope.json"
    a2_data = json.loads(a2_path.read_text(encoding="utf-8"))
    a2_data["artifacts"]["empty_metric"] = {
        "calculation_witness": {
            "formula_id": "test_identity",
            "formula_version": "1.0",
            "calculator_id": "test_calc",
            "provenance": "deterministic",
            "inputs": {},  # empty inputs!
            "result": 0.0,
        }
    }
    a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
    _reseal(a2_path)
    _rebind_manifest(run_dir, "A2", a2_path)
    _cascade_references(run_dir, "A2")

    # A2 empty witness should not count towards coverage
    report = semantic_audit(run_dir, require_source_backed=True)
    assert not report.valid
    # In mock run, A3 has 8 unsourced inputs and A2 has empty inputs -> gate fails
    assert report.levels["calculation_replay"] == "failed"
    assert any("require-source-backed" in err for err in report.errors)


def test_beneish_witness_empty_inputs_fails_validation(tmp_path):
    """A Beneish witness claiming deterministic provenance with empty inputs is rejected."""
    run_dir = tmp_path / "run_beneish_empty"
    _create_mock_run(run_dir)

    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    beneish = a3_data["artifacts"]["BeneishM"]
    beneish["calculation_witness"]["inputs"] = {}  # empty!
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    report = semantic_audit(run_dir)
    assert not report.valid
    assert report.levels["calculation_replay"] == "failed"
    assert any("missing index inputs" in err.lower() for err in report.errors)


# ----------------------------------------------------------------------
# 3. Model-Only Beneish and Unavailable Calculations
# ----------------------------------------------------------------------


def test_model_only_beneish_and_unavailable_calculations(tmp_path):
    """
    Model-only Beneish (model-reported without indices) and honest unavailable calculations:
    - Non-gated: calculation_replay is 'not_replayable' (not 'passed'), report is valid.
    - Gated: fails honestly because 0 deterministic witnesses replayed.
    - Dishonest feasible=false claiming deterministic provenance fails validation.
    """
    run_dir = tmp_path / "run_model_only"
    _create_mock_run(run_dir)

    # 1. Model-reported Beneish (honest disclosure, no index ratios)
    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    a3_data["artifacts"]["BeneishM"] = {
        "score": -2.45,
        "raw_score": -2.45,
        "classification": "Unlikely Manipulator",
        "feasible": True,
        "provenance": "model_reported_unverified",
        "calculation_witness": {
            "formula_id": "beneish_m_score",
            "result": -2.45,
            "inputs": {},
            "provenance": "model_reported_unverified",
            "notes": "Model-reported score without recomputable indices.",
        },
    }
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    # Non-gated mode
    report_nongated = semantic_audit(run_dir, require_source_backed=False)
    assert report_nongated.valid, report_nongated.errors
    assert report_nongated.levels["calculation_replay"] == "not_replayable"
    assert report_nongated.witnesses_replayed == 0
    assert report_nongated.inputs_verified == 0

    # Gated mode must fail
    report_gated = semantic_audit(run_dir, require_source_backed=True)
    assert not report_gated.valid
    assert report_gated.levels["calculation_replay"] == "failed"

    # 2. Honest unavailable calculation
    a3_data["artifacts"]["BeneishM"] = {
        "score": None,
        "raw_score": None,
        "classification": "Unavailable",
        "feasible": False,
        "unavailable_reason": "No financial statement data available.",
    }
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    report_unavail = semantic_audit(run_dir, require_source_backed=False)
    assert report_unavail.valid, report_unavail.errors
    assert report_unavail.levels["calculation_replay"] == "not_replayable"

    # 3. Dishonest feasible=false (claims deterministic result but feasible=false)
    a3_data["artifacts"]["BeneishM"]["calculation_witness"] = {
        "formula_id": "beneish_m_score",
        "provenance": "deterministic",
        "result": -2.0,
        "inputs": {},
    }
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    report_dishonest = semantic_audit(run_dir)
    assert not report_dishonest.valid
    assert any("claims deterministic result" in err for err in report_dishonest.errors)


# ----------------------------------------------------------------------
# 4. Fully Sourced Replay
# ----------------------------------------------------------------------


def test_fully_sourced_replay_succeeds_both_modes_with_counters(tmp_path):
    """
    When >=1 deterministic witness replays and all inputs cite verified facts:
    - Both non-gated and gated modes pass.
    - Witnesses replayed >= 1 and inputs verified >= 1.
    - Coverage counters accurately reflect the numbers.
    """
    run_dir = tmp_path / "run_fully_sourced"
    _create_mock_run(run_dir)
    evidence_dir = run_dir / "evidence"

    inputs_values = {
        "dsri": 1.0,
        "gmi": 1.0,
        "aqi": 1.0,
        "sgi": 1.0,
        "depi": 1.0,
        "sgai": 1.0,
        "lvgi": 1.0,
        "tata": 0.0,
    }
    facts_list = []
    fact_ids_map = {}
    for key, val in inputs_values.items():
        f_id = f"fact_zero_{key}"
        fact_ids_map[key] = f_id
        facts_list.append(
            {
                "schema_version": "financial_fact_v1",
                "id": f_id,
                "company": TICKER,
                "concept": f"Index_{key.upper()}",
                "unit": "ratio",
                "value": val,
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "filing_accession": "0000320193-25-000001",
                "availability_time": "2025-01-15T00:00:00Z",
                "retrieval_time": "2025-01-20T00:00:00Z",
                "source_location": "https://sec.gov",
                "accounting_basis": "us_gaap",
                "extractor_version": "v1",
                "kind": "reported",
            }
        )

    _create_evidence_pack(evidence_dir, embedded_facts=facts_list)

    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    beneish = a3_data["artifacts"]["BeneishM"]
    witness = beneish["calculation_witness"]
    witness["inputs"] = dict(inputs_values)
    witness["input_fact_ids"] = dict(fact_ids_map)
    score = beneish_score(**inputs_values)
    witness["result"] = score
    beneish["raw_score"] = score
    beneish["score"] = round(score, 2)
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    # Non-gated mode
    report_nongated = semantic_audit(run_dir, require_source_backed=False)
    assert report_nongated.valid, report_nongated.errors
    assert report_nongated.levels["calculation_replay"] == "passed"
    assert report_nongated.witnesses_replayed == 1
    assert report_nongated.inputs_verified == 8
    assert report_nongated.inputs_unverified == 0

    # Gated mode
    report_gated = semantic_audit(run_dir, require_source_backed=True)
    assert report_gated.valid, report_gated.errors
    assert report_gated.levels["calculation_replay"] == "passed"
    assert report_gated.overall_state == "verified"
    assert report_gated.witnesses_replayed == 1
    assert report_gated.inputs_verified == 8
    assert report_gated.inputs_unverified == 0
    assert not report_gated.unavailable

    # Verify counters in to_dict()
    cov_dict = report_gated.to_dict()["coverage"]
    assert cov_dict["witnesses_attempted"] >= 1
    assert cov_dict["witnesses_replayed"] == 1
    assert cov_dict["inputs_verified"] == 8
    assert cov_dict["inputs_unverified"] == 0


# ----------------------------------------------------------------------
# 5. Mixed Sourced / Unsourced Compatibility
# ----------------------------------------------------------------------


def test_mixed_sourced_unsourced_compatibility(tmp_path):
    """
    Mixed inputs: some cite verified facts and match, others have no cited fact:
    - Non-gated mode succeeds (arithmetic matches, unsourced disclosed in unavailable).
    - Gated mode fails (unverified inputs rejected).
    """
    run_dir = tmp_path / "run_mixed"
    _create_mock_run(run_dir)
    evidence_dir = run_dir / "evidence"

    # Only supply facts for dsri and gmi (2 out of 8)
    facts_list = [
        {
            "schema_version": "financial_fact_v1",
            "id": "fact_zero_dsri",
            "company": TICKER,
            "concept": "Index_DSRI",
            "unit": "ratio",
            "value": 1.0,
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "filing_accession": "0000320193-25-000001",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "us_gaap",
            "extractor_version": "v1",
            "kind": "reported",
        },
        {
            "schema_version": "financial_fact_v1",
            "id": "fact_zero_gmi",
            "company": TICKER,
            "concept": "Index_GMI",
            "unit": "ratio",
            "value": 1.0,
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "filing_accession": "0000320193-25-000001",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "us_gaap",
            "extractor_version": "v1",
            "kind": "reported",
        },
    ]
    _create_evidence_pack(evidence_dir, embedded_facts=facts_list)

    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    beneish = a3_data["artifacts"]["BeneishM"]
    witness = beneish["calculation_witness"]
    witness["input_fact_ids"] = {
        "dsri": "fact_zero_dsri",
        "gmi": "fact_zero_gmi",
    }
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    # Non-gated mode
    report_nongated = semantic_audit(run_dir, require_source_backed=False)
    assert report_nongated.valid, report_nongated.errors
    assert report_nongated.levels["calculation_replay"] == "passed"
    assert report_nongated.witnesses_replayed == 1
    assert report_nongated.inputs_verified == 2
    assert report_nongated.inputs_unverified == 6
    assert len(report_nongated.unavailable) == 6

    # Gated mode must fail
    report_gated = semantic_audit(run_dir, require_source_backed=True)
    assert not report_gated.valid
    assert report_gated.levels["calculation_replay"] == "failed"
    assert any("require-source-backed" in err for err in report_gated.errors)


# ----------------------------------------------------------------------
# 6. Arbitrary Extra Input Labels and Unsupported Formula
# ----------------------------------------------------------------------


def test_witness_arbitrary_extra_input_labels_rejected(tmp_path):
    """A witness with arbitrary extra input labels is rejected and never establishes coverage."""
    run_dir = tmp_path / "run_extra_labels"
    _create_mock_run(run_dir)

    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    beneish = a3_data["artifacts"]["BeneishM"]
    witness = beneish["calculation_witness"]
    witness["inputs"]["arbitrary_bonus_ratio"] = 42.0
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    report = semantic_audit(run_dir)
    assert not report.valid
    assert report.levels["calculation_replay"] == "failed"
    assert any("unexpected input labels" in err for err in report.errors)


def test_unsupported_formula_never_establishes_coverage(tmp_path):
    """An unsupported formula cannot be replayed and cannot establish source coverage."""
    run_dir = tmp_path / "run_unsupported_formula"
    _create_mock_run(run_dir)

    # Replace A3 Beneish with unavailable so Beneish doesn't establish coverage
    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    a3_data["artifacts"]["BeneishM"] = {
        "score": None,
        "raw_score": None,
        "classification": "Unavailable",
        "feasible": False,
        "unavailable_reason": "No data.",
    }
    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)

    # Introduce an unsupported formula in A2
    a2_path = run_dir / "a2_envelope.json"
    a2_data = json.loads(a2_path.read_text(encoding="utf-8"))
    a2_data["artifacts"]["mystery_calc"] = {
        "calculation_witness": {
            "formula_id": "unsupported_black_box_v9",
            "formula_version": "1.0",
            "calculator_id": "black_box",
            "provenance": "deterministic",
            "inputs": {"x": 10.0, "y": 20.0},
            "result": 30.0,
        }
    }
    a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
    _reseal(a2_path)
    _rebind_manifest(run_dir, "A2", a2_path)
    _cascade_references(run_dir, "A2")

    # Non-gated mode: unsupported formula is not replayed
    report_nongated = semantic_audit(run_dir, require_source_backed=False)
    assert report_nongated.witnesses_replayed == 0
    assert report_nongated.levels["calculation_replay"] == "not_replayable"

    # Gated mode fails
    report_gated = semantic_audit(run_dir, require_source_backed=True)
    assert not report_gated.valid
    assert any("no deterministic calculation witnesses replayed" in err for err in report_gated.errors)


# ----------------------------------------------------------------------
# 7. Legacy A3 Beneish Preservation
# ----------------------------------------------------------------------


def test_legacy_beneish_reported_metrics_preserved_when_recomputable(tmp_path):
    """
    Legacy A3 Beneish where 8 indices are present under beneish['indices']
    is recomputed and preserved as replayed.
    """
    run_dir = tmp_path / "run_legacy_beneish"
    _create_mock_run(run_dir)

    a3_path = run_dir / "a3_envelope.json"
    a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
    beneish = a3_data["artifacts"]["BeneishM"]
    indices = dict(beneish["calculation_witness"]["inputs"])
    raw = beneish["raw_score"]
    score = beneish["score"]

    # Transform into legacy shape: no calculation_witness, but indices present
    beneish.pop("calculation_witness", None)
    beneish["indices"] = indices
    beneish["raw_score"] = raw
    beneish["score"] = score
    beneish["calculator_version"] = "1.0.0"
    beneish["calculator_id"] = "beneish_calculator"

    a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
    _reseal(a3_path)
    _rebind_manifest(run_dir, "A3", a3_path)
    _cascade_references(run_dir, "A3")

    report = semantic_audit(run_dir, require_source_backed=False)
    assert report.valid, report.errors
    assert report.levels["calculation_replay"] == "passed"
    assert report.witnesses_replayed == 1
