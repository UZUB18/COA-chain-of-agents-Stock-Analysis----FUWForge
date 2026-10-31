"""
Tests for evidence-facts audit connectivity and source coverage gate.

Verifies:
1. Reconstruction of typed facts from verified EvidencePack snapshots (including SEC
   CompanyFacts and typed fact ledgers).
2. Offline production reproduction: 1 pack fact -> 1 audit fact (previously 0).
3. Preservation of typed errors on snapshot corruption, checksum mismatches,
   and conflicting fact identities (no silent fallback to 'no facts').
4. Prevention of model-generated envelope facts replacing or contradicting source facts.
5. Missing evidence handling when manifest claims verified evidence.
6. The --require-source-backed CLI gate on verify-run:
   - Fails unsourced numeric calculation bindings with clear text and JSON diagnostics.
   - Preserves ordinary --strict handling of honest unavailable inputs.
   - Preserves mock exploratory use.
   - Succeeds when all calculation inputs are verified and source-backed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evidence import EvidencePack
from src.facts import AccountingBasis, Fact, FactKind
from src.run_audit import build_run_manifest, semantic_audit, verify_run_semantic
from src.run_semantic_audit import _load_fact_ledger
from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score
from src.utils.checksum import checksum_file, envelope_checksum, envelope_content_checksum, sha256_digest


TICKER = "SEM"
ANALYSIS_DATE = "2026-02-01"


def _good_run(tmp: Path, ticker: str = TICKER) -> Path:
    """Produce a real mock run with all standard roles."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Semantic Co",
        ticker=ticker,
        analysis_date=ANALYSIS_DATE,
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("semantic_run", params, output_root=tmp)
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
    """Propagate a changed checksum through downstream envelope references."""
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


def _sec_companyfacts_payload(ticker: str = TICKER, cik: int = 320193) -> Dict[str, Any]:
    return {
        "cik": cik,
        "entityName": f"{ticker} Inc",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "description": "Total revenue",
                    "units": {
                        "USD": [
                            {
                                "end": "2024-12-31",
                                "val": 1000000.0,
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-01-15",
                                "frame": "CY2024",
                                "accn": "0000320193-25-000001",
                            }
                        ]
                    },
                }
            }
        },
    }


def _create_evidence_pack(
    evidence_dir: Path,
    ticker: str = TICKER,
    analysis_date: str = ANALYSIS_DATE,
    attachments: Optional[List[Dict[str, Any]]] = None,
    embedded_facts: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Create a verified EvidencePack directory structure."""
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
        "attachments": attachments or [],
    }
    if embedded_facts is not None:
        pack_data["facts"] = embedded_facts

    ev_json_path = evidence_dir / "evidence.json"
    ev_json_path.write_text(json.dumps(pack_data, indent=2), encoding="utf-8")
    return ev_json_path


def _write_attachment_file(evidence_dir: Path, filename: str, content: bytes, kind: str) -> Dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / filename
    target.write_bytes(content)
    return {
        "file": filename,
        "kind": kind,
        "sha256": sha256_digest(content),
    }


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "src.controller", *args]
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)


@pytest.mark.parametrize("evidence_state", ["valid", "missing", "invalid"])
def test_manifest_evidence_state_comes_from_an_actual_pack_load(
    tmp_path, evidence_state
):
    claimed_checksum = "sha256:" + "0" * 64
    evidence_dir = tmp_path / "evidence"
    if evidence_state == "valid":
        _create_evidence_pack(evidence_dir)
    elif evidence_state == "invalid":
        evidence_dir.mkdir()
        (evidence_dir / "evidence.json").write_text("{}", encoding="utf-8")

    manifest = build_run_manifest(
        tmp_path,
        {},
        run_id=f"evidence-{evidence_state}",
        ticker=TICKER,
        analysis_date=ANALYSIS_DATE,
        status="halted",
        summary={"evidence_checksum": claimed_checksum},
    )

    if evidence_state == "valid":
        pack = EvidencePack(
            evidence_dir / "evidence.json",
            ticker=TICKER,
            analysis_date=ANALYSIS_DATE,
        )
        assert manifest["evidence"]["pack_checksum"] == pack.checksum
        assert manifest["evidence"]["state"] == "source_bytes_verified"
    else:
        assert manifest["evidence"]["pack_checksum"] == claimed_checksum
        assert manifest["evidence"]["state"] == "source_bytes_unavailable"


# ----------------------------------------------------------------------
# 1. Production Reproduction & Loader Regression
# ----------------------------------------------------------------------


def test_offline_production_reproduction_pack_facts_loaded_into_audit(tmp_path):
    """
    Offline production reproduction:
    An EvidencePack snapshot under run/evidence contains parsed SEC CompanyFacts.
    Previously, _load_fact_ledger loaded 0 audit facts and reported no error.
    Now, facts must be reconstructed from the verified evidence snapshot.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    evidence_dir = run_dir / "evidence"

    # Attach raw SEC company facts
    sec_payload = _sec_companyfacts_payload()
    att = _write_attachment_file(
        evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
    )
    _create_evidence_pack(evidence_dir, attachments=[att])

    problems: List[str] = []
    ledger = _load_fact_ledger(
        run_dir,
        envelopes={},
        problems=problems,
        ticker=TICKER,
        analysis_date=ANALYSIS_DATE,
    )

    assert not problems, problems
    assert len(ledger) >= 1, "Fact ledger must contain reconstructed facts from evidence snapshot"
    fact = next(iter(ledger.values()))
    assert fact.company == TICKER
    assert fact.concept == "Revenues"
    assert fact.value == 1000000.0


def test_typed_fact_ledger_attachment_reconstructed(tmp_path):
    """A .facts.json or kind='fact_ledger' attachment is reconstructed into facts_map."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    evidence_dir = run_dir / "evidence"

    typed_fact = {
        "schema_version": "financial_fact_v1",
        "id": "fact_sem_typed_rev",
        "company": TICKER,
        "concept": "Revenues",
        "unit": "USD",
        "currency": "USD",
        "value": 500000.0,
        "filing_accession": "0000320193-25-000001",
        "availability_time": "2025-01-15T00:00:00Z",
        "retrieval_time": "2025-01-20T00:00:00Z",
        "source_location": "https://sec.gov",
        "accounting_basis": "us_gaap",
        "extractor_version": "v1",
        "kind": "reported",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
    }
    att = _write_attachment_file(
        evidence_dir, "financial_facts.json", json.dumps([typed_fact]).encode(), "fact_ledger"
    )
    _create_evidence_pack(evidence_dir, attachments=[att])

    problems: List[str] = []
    ledger = _load_fact_ledger(
        run_dir,
        envelopes={},
        problems=problems,
        ticker=TICKER,
        analysis_date=ANALYSIS_DATE,
    )

    assert not problems, problems
    assert "fact_sem_typed_rev" in ledger
    assert ledger["fact_sem_typed_rev"].value == 500000.0


def test_sec_companyfacts_raw_attachment_bound_and_validated_in_witness(tmp_path):
    """
    A raw SEC CompanyFacts attachment is parsed and bound to a calculation witness;
    tampered claimed value fails audit, matching value passes.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_sec_witness_"))
    try:
        _good_run(tmp)
        evidence_dir = tmp / "evidence"
        sec_payload = _sec_companyfacts_payload()
        att = _write_attachment_file(
            evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
        )
        _create_evidence_pack(evidence_dir, attachments=[att])

        ledger = _load_fact_ledger(tmp, {}, ticker=TICKER, analysis_date=ANALYSIS_DATE)
        sec_fact_id = next(iter(ledger.keys()))

        a2_path = tmp / "a2_envelope.json"
        a2_data = json.loads(a2_path.read_text(encoding="utf-8"))
        a2_data["artifacts"]["revenue_check"] = {
            "calculation_witness": {
                "formula_id": "test_identity",
                "formula_version": "1.0",
                "calculator_id": "test_calc",
                "provenance": "deterministic",
                "inputs": {"rev": 1000000.0},
                "input_fact_ids": {"rev": sec_fact_id},
                "result": 1000000.0,
            }
        }
        a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
        _reseal(a2_path)
        _rebind_manifest(tmp, "A2", a2_path)
        _cascade_references(tmp, "A2")

        report = semantic_audit(tmp)
        assert report.valid, report.errors
        assert report.levels["calculation_replay"] == "passed"

        # Tampered value fails
        a2_data["artifacts"]["revenue_check"]["calculation_witness"]["inputs"]["rev"] = 999999.0
        a2_data["artifacts"]["revenue_check"]["calculation_witness"]["result"] = 999999.0
        a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
        _reseal(a2_path)
        _rebind_manifest(tmp, "A2", a2_path)
        _cascade_references(tmp, "A2")

        report_bad = semantic_audit(tmp)
        assert not report_bad.valid
        assert report_bad.levels["calculation_replay"] == "failed"
        assert any("does not equal the referenced fact's value" in err for err in report_bad.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
# 2. Snapshot Corruption & Missing Evidence
# ----------------------------------------------------------------------


def test_snapshot_corruption_attachment_bytes_fails_audit(tmp_path):
    """
    Corrupting attachment bytes in the evidence snapshot must produce typed errors,
    failing calculation replay instead of silently becoming 'no facts'.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_corrupt_"))
    try:
        _good_run(tmp)
        evidence_dir = tmp / "evidence"
        sec_payload = _sec_companyfacts_payload()
        att = _write_attachment_file(
            evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
        )
        _create_evidence_pack(evidence_dir, attachments=[att])

        # Tamper with the attachment file bytes
        (evidence_dir / "companyfacts.json").write_bytes(b"tampered content")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] == "failed"
        assert report.levels["source_reconstruction"] == "source_bytes_unavailable"
        assert any("could not be loaded" in err or "checksum mismatch" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_corruption_manifest_checksum_mismatch_fails_audit(tmp_path):
    """
    If manifest records an evidence pack checksum that does not match the actual
    evidence pack snapshot, audit must fail with a checksum mismatch error.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_cksum_"))
    try:
        _good_run(tmp)
        evidence_dir = tmp / "evidence"
        _create_evidence_pack(evidence_dir)

        # Update manifest to declare a bogus pack_checksum
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["evidence"] = {
            "pack_checksum": "0000000000000000000000000000000000000000000000000000000000000000",
            "state": "source_bytes_verified",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] == "failed"
        assert any("checksum mismatch" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_evidence_directory_when_manifest_claims_verified_fails(tmp_path):
    """
    If run manifest claims verified evidence bytes, but the evidence directory
    is missing, audit must fail loudly rather than reporting 'no facts'.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_noev_"))
    try:
        _good_run(tmp)
        # Ensure no evidence dir exists
        shutil.rmtree(tmp / "evidence", ignore_errors=True)

        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["evidence"] = {
            "pack_checksum": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "state": "source_bytes_verified",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] == "failed"
        assert report.levels["source_reconstruction"] == "source_bytes_unavailable"
        assert "source_reconstruction" in report.failed_levels()
        assert any("missing from run directory" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_evidence_directory_missing_evidence_json_fails(tmp_path):
    """An evidence/ directory without evidence.json is a defect, not empty evidence."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_noevjson_"))
    try:
        _good_run(tmp)
        (tmp / "evidence").mkdir(exist_ok=True)
        (tmp / "evidence" / "orphan.txt").write_text("data")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] == "failed"
        assert any("evidence.json is missing" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
# 3. Conflicting Fact Identities & Model Trust Boundary
# ----------------------------------------------------------------------


def test_conflicting_fact_identities_in_snapshot_rejected(tmp_path):
    """
    If the evidence snapshot contains conflicting definitions for the same fact ID,
    the audit must reject them rather than silently picking one.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    evidence_dir = run_dir / "evidence"

    fact_a = {
        "schema_version": "financial_fact_v1",
        "id": "fact_conflict_1",
        "company": TICKER,
        "concept": "Revenues",
        "unit": "USD",
        "value": 100.0,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "filing_accession": "0000320193-25-000001",
        "availability_time": "2025-01-15T00:00:00Z",
        "retrieval_time": "2025-01-20T00:00:00Z",
        "source_location": "https://sec.gov",
        "accounting_basis": "us_gaap",
        "extractor_version": "v1",
    }
    fact_b = dict(fact_a, value=200.0)  # Same ID, conflicting value!

    att = _write_attachment_file(
        evidence_dir, "ledger_b.json", json.dumps([fact_b]).encode(), "fact_ledger"
    )
    _create_evidence_pack(evidence_dir, attachments=[att], embedded_facts=[fact_a])

    problems: List[str] = []
    _load_fact_ledger(
        run_dir,
        envelopes={},
        problems=problems,
        ticker=TICKER,
        analysis_date=ANALYSIS_DATE,
    )

    assert any("conflicting fact identity" in p for p in problems)


def test_model_cannot_replace_source_facts_with_conflicting_envelope(tmp_path):
    """
    If the model generates an envelope with a fact that contradicts a verified
    source fact from the evidence snapshot, the audit must reject the model fact.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_model_replace_"))
    try:
        _good_run(tmp)
        evidence_dir = tmp / "evidence"
        fact_source = {
            "schema_version": "financial_fact_v1",
            "id": "fact_sem_dsri",
            "company": TICKER,
            "concept": "DaysSalesInReceivablesIndex",
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
        }
        _create_evidence_pack(evidence_dir, embedded_facts=[fact_source])

        # Envelope attempts to redefine fact_sem_dsri with value 999.0
        a3_path = tmp / "a3_envelope.json"
        a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
        fact_tampered = dict(fact_source, value=999.0)
        a3_data["artifacts"]["facts"] = [fact_tampered]
        a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
        _reseal(a3_path)
        _rebind_manifest(tmp, "A3", a3_path)
        _cascade_references(tmp, "A3")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] == "failed"
        assert any("model cannot replace source facts" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_model_cannot_introduce_unsourced_facts_when_evidence_present(tmp_path):
    """
    When an evidence snapshot is present, the model envelope cannot introduce
    new facts that were not in the verified snapshot.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_unsourced_env_"))
    try:
        _good_run(tmp)
        evidence_dir = tmp / "evidence"
        _create_evidence_pack(evidence_dir)

        # A3 embeds an arbitrary unverified fact
        a3_path = tmp / "a3_envelope.json"
        a3_data = json.loads(a3_path.read_text(encoding="utf-8"))
        a3_data["artifacts"]["facts"] = [
            {
                "schema_version": "financial_fact_v1",
                "id": "fact_hallucinated_1",
                "company": TICKER,
                "concept": "Revenues",
                "unit": "USD",
                "value": 42.0,
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "filing_accession": "0000320193-25-000001",
                "availability_time": "2025-01-15T00:00:00Z",
                "retrieval_time": "2025-01-20T00:00:00Z",
                "source_location": "https://sec.gov",
                "accounting_basis": "us_gaap",
                "extractor_version": "v1",
            }
        ]
        a3_path.write_text(json.dumps(a3_data, indent=2), encoding="utf-8")
        _reseal(a3_path)
        _rebind_manifest(tmp, "A3", a3_path)
        _cascade_references(tmp, "A3")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["calculation_replay"] == "failed"
        assert any("not found in the verified evidence snapshot" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
# 4. Source Coverage Gate (--require-source-backed) & CLI
# ----------------------------------------------------------------------


def test_cli_require_source_backed_fails_unsourced_mock_run():
    """
    A standard mock run has recomputed arithmetic but unsourced inputs.
    Without --require-source-backed: verify-run passes (and --strict passes).
    WITH --require-source-backed: verify-run fails with diagnostic and nonzero exit.
    WITH --require-source-backed --json: returns machine-readable failed audit.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_cli_gate_"))
    try:
        _good_run(tmp)

        # 1. Normal verify-run passes
        res_normal = _run_cli("verify-run", str(tmp))
        assert res_normal.returncode == 0, res_normal.stderr
        assert "[unavailable]" in res_normal.stdout

        # 2. Ordinary --strict passes (honest unavailable inputs not escalated)
        res_strict = _run_cli("verify-run", str(tmp), "--strict")
        assert res_strict.returncode == 0, res_strict.stderr

        # 3. --require-source-backed fails
        res_gated = _run_cli("verify-run", str(tmp), "--require-source-backed")
        assert res_gated.returncode != 0
        assert "Run audit FAILED" in res_gated.stdout
        assert "require-source-backed" in res_gated.stdout

        # 4. --require-source-backed combined with --strict fails
        res_both = _run_cli("verify-run", str(tmp), "--require-source-backed", "--strict")
        assert res_both.returncode != 0

        # 5. Machine-readable JSON output
        res_json = _run_cli("verify-run", str(tmp), "--require-source-backed", "--json")
        assert res_json.returncode != 0
        data = json.loads(res_json.stdout)
        assert data["overall_state"] == "failed"
        assert "calculation_replay" in data["failed_levels"]
        assert any("require-source-backed" in err for err in data["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_require_source_backed_succeeds_when_fully_sourced():
    """
    When all numeric calculation inputs cite verified facts from the evidence
    snapshot and match, verify-run --require-source-backed succeeds (exit code 0).
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cov_cli_sourced_"))
    try:
        _good_run(tmp)
        evidence_dir = tmp / "evidence"

        # Construct 8 facts for Beneish M-Score inputs
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
            f_id = f"fact_sem_{key}"
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

        # Wire A3 calculation witness to cite all facts
        a3_path = tmp / "a3_envelope.json"
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
        _rebind_manifest(tmp, "A3", a3_path)
        _cascade_references(tmp, "A3")

        # Audit with require_source_backed in python
        report = semantic_audit(tmp, require_source_backed=True)
        assert report.valid, report.errors
        assert report.overall_state == "verified"
        assert report.levels["calculation_replay"] == "passed"
        assert not report.unavailable

        # Verify CLI --require-source-backed succeeds
        res_gated = _run_cli("verify-run", str(tmp), "--require-source-backed")
        assert res_gated.returncode == 0, res_gated.stderr
        assert "Audit passed" in res_gated.stdout

        # Verify CLI with both --strict and --require-source-backed succeeds
        res_both = _run_cli("verify-run", str(tmp), "--require-source-backed", "--strict")
        assert res_both.returncode == 0, res_both.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
