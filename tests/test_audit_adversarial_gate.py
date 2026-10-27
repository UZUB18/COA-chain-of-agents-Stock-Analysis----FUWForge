"""Adversarial source-gate regressions; all facts are synthetic offline fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.run_semantic_audit import AuditReport, _audit_graph, semantic_audit
from src.envelope import Envelope
from tests.test_audit_zero_coverage import (
    ANALYSIS_DATE, TICKER, _create_mock_run, _create_evidence_pack,
    _reseal, _rebind_manifest, _cascade_references,
)


def _fact(fid, value, *, unit="ratio", company=TICKER, available="2025-01-15T00:00:00Z"):
    return {
        "schema_version": "financial_fact_v1", "id": fid, "company": company,
        "concept": fid.upper(), "unit": unit, "value": value,
        "period_start": "2024-01-01", "period_end": "2024-12-31",
        "filing_accession": "0000320193-25-000001", "availability_time": available,
        "retrieval_time": "2025-01-20T00:00:00Z", "source_location": "https://sec.gov",
        "accounting_basis": "us_gaap", "extractor_version": "synthetic-test-v1",
    }


def _write_artifacts(run_dir, agent, data):
    path = run_dir / f"{agent.lower()}_envelope.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reseal(path)
    _rebind_manifest(run_dir, agent, path)
    _cascade_references(run_dir, agent)


def _unavailable_core(run_dir):
    data = json.loads((run_dir / "a3_envelope.json").read_text())
    data["artifacts"]["BeneishM"] = {
        "score": None, "raw_score": None, "classification": "Unavailable", "feasible": False,
        "unavailable_reason": "Synthetic fixture has no core financial statements.",
    }
    _write_artifacts(run_dir, "A3", data)


def test_self_declared_envelope_facts_without_source_snapshot_cannot_pass_gate(tmp_path):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a3_envelope.json").read_text())
    witness = data["artifacts"]["BeneishM"]["calculation_witness"]
    ids = {key: f"self_declared_{key}" for key in witness["inputs"]}
    data["artifacts"]["facts"] = [_fact(ids[key], value) for key, value in witness["inputs"].items()]
    witness["input_fact_ids"] = ids
    _write_artifacts(tmp_path, "A3", data)
    assert semantic_audit(tmp_path).valid  # legacy, explicitly nongated compatibility
    report = semantic_audit(tmp_path, require_source_backed=True)
    assert not report.valid
    assert report.levels["source_reconstruction"] == "not_applicable"
    assert any("verified source bytes" in error for error in report.errors)


def test_valid_derived_input_does_not_replay_unknown_output_formula(tmp_path):
    _create_mock_run(tmp_path)
    _unavailable_core(tmp_path)
    _create_evidence_pack(tmp_path / "evidence", embedded_facts=[
        _fact("rev_current", 200.0, unit="USD"), _fact("rev_previous", 100.0, unit="USD"),
    ])
    data = json.loads((tmp_path / "a2_envelope.json").read_text())
    data["artifacts"]["black_box"] = {"calculation_witness": {
        "formula_id": "unsupported_black_box_v9", "formula_version": "1.0",
        "calculator_id": "synthetic-test", "provenance": "deterministic",
        "inputs": {"derived_ratio": 2.0}, "input_fact_ids": {"derived_ratio": "rev_current"},
        "derivations": {"derived_ratio": {"expression": "a / b", "operand_fact_ids": ["rev_current", "rev_previous"]}},
        "result": 999999.0,
    }}
    _write_artifacts(tmp_path, "A2", data)
    report = semantic_audit(tmp_path)
    assert report.witnesses_replayed == 0
    assert report.levels["calculation_replay"] != "passed"
    assert any("unsupported formula" in error for error in report.unavailable + report.errors)


def test_unrelated_identity_cannot_replace_source_backed_core_forensics(tmp_path):
    _create_mock_run(tmp_path)
    _unavailable_core(tmp_path)
    _create_evidence_pack(tmp_path / "evidence", embedded_facts=[_fact("borrowed_rev", 1000000.0, unit="USD")])
    data = json.loads((tmp_path / "a2_envelope.json").read_text())
    data["artifacts"]["borrowed_metric"] = {"calculation_witness": {
        "formula_id": "identity", "formula_version": "1.0", "calculator_id": "synthetic-test",
        "provenance": "deterministic", "inputs": {"rev": 1000000.0},
        "input_fact_ids": {"rev": "borrowed_rev"}, "result": 1000000.0,
    }}
    _write_artifacts(tmp_path, "A2", data)
    assert semantic_audit(tmp_path).valid
    report = semantic_audit(tmp_path, require_source_backed=True)
    assert report.witnesses_replayed == 1  # identity replay is real, but not core coverage
    assert report.inputs_verified == 1
    assert not report.valid
    assert any("A3 BeneishM" in error for error in report.errors)


@pytest.mark.parametrize("company,available,fragment", [
    ("OTHER", "2025-01-15T00:00:00Z", "company"),
    (TICKER, "2027-01-15T00:00:00Z", "cutoff"),
])
def test_graph_uses_run_identity_and_cutoff_when_envelope_params_absent(tmp_path, company, available, fragment):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a2_envelope.json").read_text())
    data.pop("params", None)
    data["artifacts"]["metric"] = {"calculation_witness": {
        "formula_id": "identity", "formula_version": "1.0", "calculator_id": "synthetic-test",
        "provenance": "deterministic", "inputs": {"rev": 1.0},
        "input_fact_ids": {"rev": "foreign_fact"}, "result": 1.0,
    }}
    env = Envelope.from_dict(data)
    ledger = {"foreign_fact": _fact("foreign_fact", 1.0, company=company, available=available)}
    errors = _audit_graph({"A2": env}, tmp_path, AuditReport(), fact_ledger=ledger,
                          expected_company=TICKER, cutoff=ANALYSIS_DATE)
    assert any("A2 contract:" in error and fragment in error.lower() for error in errors), errors


@pytest.mark.parametrize("location,bad", [
    ("input", 10**400), ("score", float("nan")), ("raw_score", float("nan")),
    ("raw_score", True), ("result", float("nan")),
])
def test_core_replay_handles_nonfinite_and_overflow_without_false_pass(tmp_path, location, bad):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a3_envelope.json").read_text())
    beneish = data["artifacts"]["BeneishM"]
    if location == "input":
        beneish["calculation_witness"]["inputs"]["dsri"] = bad
    elif location == "result":
        beneish["calculation_witness"]["result"] = bad
    else:
        beneish[location] = bad
    _write_artifacts(tmp_path, "A3", data)
    report = semantic_audit(tmp_path)
    assert not report.valid
    assert report.levels["calculation_replay"] == "failed"
    assert report.witnesses_replayed == 0


@pytest.mark.parametrize("provenance,result", [("deterministic", 999.0), ("unavailable", None)])
def test_nested_witnesses_are_audited_without_borrowing_core_coverage(tmp_path, provenance, result):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a3_envelope.json").read_text())
    witness = data["artifacts"]["BeneishM"]["calculation_witness"]
    ids = {key: f"core_{key}" for key in witness["inputs"]}
    witness["input_fact_ids"] = ids
    _create_evidence_pack(tmp_path / "evidence", embedded_facts=[
        _fact(ids[key], value) for key, value in witness["inputs"].items()
    ])
    _write_artifacts(tmp_path, "A3", data)
    assert semantic_audit(tmp_path, require_source_backed=True).valid
    a2 = json.loads((tmp_path / "a2_envelope.json").read_text())
    a2["artifacts"]["nested"] = [{"submetric": {"calculation_witness": {
        "formula_id": "identity", "formula_version": "1.0", "calculator_id": "synthetic-test",
        "provenance": provenance, "inputs": {"a": 1.0}, "result": result,
        "notes": "Synthetic missing input disclosure.",
    }}}]
    _write_artifacts(tmp_path, "A2", a2)
    report = semantic_audit(tmp_path, require_source_backed=True)
    assert not report.valid
    assert report.to_dict()["coverage"]["core_witnesses_replayed"] == 1
    assert report.to_dict()["coverage"]["core_inputs_verified"] == 8
    assert any("nested[0].submetric" in error for error in report.errors)


@pytest.mark.parametrize("provenance,result", [("deterministic", 999.0), ("unavailable", None)])
def test_list_of_list_hidden_bad_witness_fails_gate(tmp_path, provenance, result):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a3_envelope.json").read_text())
    witness = data["artifacts"]["BeneishM"]["calculation_witness"]
    ids = {key: f"core_{key}" for key in witness["inputs"]}
    witness["input_fact_ids"] = ids
    _create_evidence_pack(tmp_path / "evidence", embedded_facts=[
        _fact(ids[key], value) for key, value in witness["inputs"].items()
    ])
    _write_artifacts(tmp_path, "A3", data)
    assert semantic_audit(tmp_path, require_source_backed=True).valid
    a2 = json.loads((tmp_path / "a2_envelope.json").read_text())
    a2["artifacts"]["nested"] = [[{"submetric": {"calculation_witness": {
        "formula_id": "identity", "formula_version": "1.0", "calculator_id": "synthetic-test",
        "provenance": provenance, "inputs": {"a": 1.0}, "result": result,
        "notes": "Synthetic missing input disclosure.",
    }}}]]
    _write_artifacts(tmp_path, "A2", a2)
    report = semantic_audit(tmp_path, require_source_backed=True)
    assert not report.valid
    assert report.to_dict()["coverage"]["core_witnesses_replayed"] == 1
    assert report.to_dict()["coverage"]["core_inputs_verified"] == 8
    assert any("nested[0][0].submetric" in error for error in report.errors)


def test_scan_depth_exhaustion_is_explicit_audit_error(tmp_path):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a3_envelope.json").read_text())
    witness = data["artifacts"]["BeneishM"]["calculation_witness"]
    ids = {key: f"core_{key}" for key in witness["inputs"]}
    witness["input_fact_ids"] = ids
    _create_evidence_pack(tmp_path / "evidence", embedded_facts=[
        _fact(ids[key], value) for key, value in witness["inputs"].items()
    ])
    _write_artifacts(tmp_path, "A3", data)
    assert semantic_audit(tmp_path, require_source_backed=True).valid

    deep = {"calculation_witness": {
        "formula_id": "identity", "formula_version": "1.0", "calculator_id": "synthetic-test",
        "provenance": "deterministic", "inputs": {"a": 1.0}, "result": 999.0,
    }}
    for _ in range(12):
        deep = {"layer": deep}
    a2 = json.loads((tmp_path / "a2_envelope.json").read_text())
    a2["artifacts"]["deep"] = deep
    _write_artifacts(tmp_path, "A2", a2)

    report = semantic_audit(tmp_path, require_source_backed=True)
    assert not report.valid
    assert report.levels["graph_and_contracts"] == "failed"
    assert report.levels["calculation_replay"] == "failed"
    assert any("scan depth limit exceeded" in error for error in report.errors)


@pytest.mark.parametrize("provenance", ["deterministic", "model_reported_unverified"])
@pytest.mark.parametrize("missing_field", ["calculator_id", "formula_id", "provenance"])
def test_direct_witness_missing_metadata_cannot_evade_discovery(tmp_path, provenance, missing_field):
    _create_mock_run(tmp_path)
    data = json.loads((tmp_path / "a3_envelope.json").read_text())
    witness = data["artifacts"]["BeneishM"]["calculation_witness"]
    ids = {key: f"core_{key}" for key in witness["inputs"]}
    witness["input_fact_ids"] = ids
    _create_evidence_pack(tmp_path / "evidence", embedded_facts=[
        _fact(ids[key], value) for key, value in witness["inputs"].items()
    ])
    _write_artifacts(tmp_path, "A3", data)
    assert semantic_audit(tmp_path, require_source_backed=True).valid

    a2 = json.loads((tmp_path / "a2_envelope.json").read_text())
    direct = {
        "formula_id": "identity", "calculator_id": "synthetic-test",
        "formula_version": "1.0", "provenance": provenance,
        "inputs": {"a": 1.0}, "result": 999.0,
    }
    direct.pop(missing_field)
    a2["artifacts"]["direct"] = direct
    _write_artifacts(tmp_path, "A2", a2)
    report = semantic_audit(tmp_path, require_source_backed=True)
    assert not report.valid
    assert report.core_witnesses_replayed == 1
    assert report.core_inputs_verified == 8
    assert any("direct" in error for error in report.errors)

