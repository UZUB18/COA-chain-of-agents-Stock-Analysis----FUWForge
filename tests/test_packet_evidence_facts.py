"""
Tests for verified evidence facts bridge in research packet export.

Verifies:
1. Standard real EvidencePack snapshot with SEC CompanyFacts and typed fact
   attachments retains claim fact IDs, and validate_packet/export_packet succeeds.
2. Fact projection schema aliases: real Fact instant/availability_time/filing_accession
   are projected to schema period_instant/filing_date/accession, without extra or
   forbidden keys, and without inventing unbacked 'verified' labels.
3. Corruption fails: attachment hash corruption, manifest pack_checksum mismatch,
   conflicting fact IDs in snapshot, and model-invented facts when evidence snapshot exists
   all cause clear rejection (ValueError), never broad catch or silent downgrade.
4. No-evidence mock run behavior remains fully compatible.
5. Ticker and cutoff are derived correctly.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
import pytest

from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.research_packet import (
    PACKET_SCHEMA_VERSION,
    build_packet,
    export_packet,
    file_sha256,
    validate_packet,
)
from src.sec_facts import parse_sec_company_facts
from src.utils.checksum import checksum_file, envelope_content_checksum, sha256_digest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TICKER = "PKT"
ANALYSIS_DATE = "2026-02-01"


def _build_mock_run(output_dir: Path, ticker: str = TICKER, company: str = "Packet Corp") -> Path:
    """Run a complete mock multi-agent analysis."""
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company=company,
        ticker=ticker,
        analysis_date=ANALYSIS_DATE,
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("packet_test_run", params, output_root=output_dir)
    return output_dir


def _reseal(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["content_checksum"] = envelope_content_checksum(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _rebind_manifest(run_dir: Path, agent: str, path: Path) -> None:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest.setdefault("roles", {})[agent] = data["content_checksum"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


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


# ==============================================================================
# 1. Standard Real EvidencePack Snapshot with SEC CompanyFacts & Typed Attachments
# ==============================================================================


def test_standard_evidence_pack_export_succeeds_and_retains_claim_facts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"

        # Attachment 1: Raw SEC company facts
        sec_payload = _sec_companyfacts_payload(ticker=TICKER)
        sec_facts = parse_sec_company_facts(sec_payload, ticker=TICKER)
        assert len(sec_facts) >= 1
        sec_fact_id = sec_facts[0].id

        att1 = _write_attachment_file(
            evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
        )

        # Attachment 2: Typed fact ledger
        typed_fact = {
            "schema_version": "financial_fact_v1",
            "id": "fact_pkt_fcf_2024",
            "company": TICKER,
            "concept": "FreeCashFlow",
            "unit": "USD",
            "currency": "USD",
            "value": 450000.0,
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
        att2 = _write_attachment_file(
            evidence_dir, "financial_facts.json", json.dumps([typed_fact]).encode(), "fact_ledger"
        )

        ev_json = _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att1, att2])

        # Compute pack checksum and record in run_manifest.json
        from src.evidence import EvidencePack
        pack = EvidencePack(ev_json, ticker=TICKER, analysis_date=ANALYSIS_DATE)
        manifest_path = run_dir / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["evidence"] = {
            "pack_checksum": pack.checksum,
            "state": "source_bytes_verified",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # In A6B envelope, cite the verified fact IDs in BullCase
        a6b_path = run_dir / "a6b_envelope.json"
        a6b_data = json.loads(a6b_path.read_text(encoding="utf-8"))
        a6b_data["artifacts"]["BullCase"] = [
            {
                "id": "B1",
                "thesis": "Revenue expansion and cash generation",
                "evidence": ["A2.PEER_LIST", sec_fact_id],
                "fact_ids": [sec_fact_id, "fact_pkt_fcf_2024"],
                "impact": "Topline revenue growth",
            }
        ]
        a6b_path.write_text(json.dumps(a6b_data, indent=2), encoding="utf-8")
        _reseal(a6b_path)
        _rebind_manifest(run_dir, "A6B", a6b_path)

        # Build packet
        packet = build_packet(run_dir)

        # Verify fact IDs are retained in packet.facts
        pkt_fact_ids = {f["id"] for f in packet["facts"]}
        assert sec_fact_id in pkt_fact_ids, f"SEC fact ID '{sec_fact_id}' missing from packet facts"
        assert "fact_pkt_fcf_2024" in pkt_fact_ids, "Typed fact ID missing from packet facts"

        # Verify claim references the fact IDs
        bull_claim = next(c for c in packet["claims"] if c["id"] == "B1")
        assert sec_fact_id in bull_claim["fact_ids"]
        assert "fact_pkt_fcf_2024" in bull_claim["fact_ids"]

        # Validate packet succeeds cleanly
        errors = validate_packet(packet, run_dir=run_dir)
        assert not errors, f"Packet validation failed with errors: {errors}"

        # Export packet succeeds
        out_file = Path(tmp_dir) / "exported_evidence_packet.json"
        res = export_packet(run_dir, out_file)
        assert out_file.is_file()
        assert res["sha256"] == file_sha256(out_file)

        exported_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(exported_data["facts"]) >= 2
        exported_ids = {f["id"] for f in exported_data["facts"]}
        assert sec_fact_id in exported_ids
        assert "fact_pkt_fcf_2024" in exported_ids


# ==============================================================================
# 2. Schema Aliases Projection (instant / availability_time / filing_accession)
# ==============================================================================


def test_schema_aliases_projection_and_metadata_preservation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"

        # Typed fact with instant, availability_time, filing_accession
        instant_fact = {
            "schema_version": "financial_fact_v1",
            "id": "fact_instant_test",
            "company": TICKER,
            "concept": "CashAndCashEquivalentsAtCarryingValue",
            "unit": "USD",
            "currency": "USD",
            "value": 1250000.0,
            "filing_accession": "0000320193-25-000099",
            "availability_time": "2025-01-15T18:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": {"url": "https://sec.gov", "form": "10-K", "file": "doc_10k.txt"},
            "accounting_basis": "US-GAAP",
            "extractor_version": "v1.2",
            "kind": "reported",
            "instant": "2024-12-31",
            "dimensions": {"segment": "commercial"},
            "metadata": {"note": "test metadata"},
        }
        att = _write_attachment_file(
            evidence_dir, "facts.facts.json", json.dumps([instant_fact]).encode(), "fact_ledger"
        )
        _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att])

        packet = build_packet(run_dir)
        projected = next(f for f in packet["facts"] if f["id"] == "fact_instant_test")

        # Correct aliases mapped to research_packet_v1.json schema properties
        assert projected["period_instant"] == "2024-12-31"
        assert projected["filing_date"] == "2025-01-15T18:00:00Z"
        assert projected["accession"] == "0000320193-25-000099"

        # Real Fact field names that are NOT in schema must NOT appear as top-level keys
        assert "instant" not in projected
        assert "availability_time" not in projected
        assert "filing_accession" not in projected

        # Must not label fact as verified solely on presence (no extra 'verified' property)
        assert "verified" not in projected
        assert "verification" not in projected

        # Values and dimensions preserved
        assert projected["value"] == 1250000.0
        assert projected["company"] == TICKER
        assert projected["concept"] == "CashAndCashEquivalentsAtCarryingValue"
        assert projected["dimensions"] == {"segment": "commercial"}
        assert projected["metadata"] == {"note": "test metadata"}

        # Validate schema conformance
        errors = validate_packet(packet, run_dir=run_dir)
        assert not errors, f"Validation errors: {errors}"


# ==============================================================================
# 3. Corruption & Conflicting Fact Rejection (Never Silent Downgrade)
# ==============================================================================


def test_attachment_bytes_corruption_fails_export():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"
        sec_payload = _sec_companyfacts_payload(ticker=TICKER)
        att = _write_attachment_file(
            evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
        )
        _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att])

        # Corrupt the attachment bytes on disk
        (evidence_dir / "companyfacts.json").write_bytes(b'{"corrupted": true}')

        out_file = Path(tmp_dir) / "corrupt_export.json"
        with pytest.raises(ValueError, match="Fact ledger verification failed|checksum mismatch"):
            export_packet(run_dir, out_file)


def test_manifest_checksum_mismatch_fails_export():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"
        sec_payload = _sec_companyfacts_payload(ticker=TICKER)
        att = _write_attachment_file(
            evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
        )
        _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att])

        manifest_path = run_dir / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["evidence"] = {
            "pack_checksum": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
            "state": "source_bytes_verified",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        out_file = Path(tmp_dir) / "bad_checksum_export.json"
        with pytest.raises(ValueError, match="checksum mismatch"):
            export_packet(run_dir, out_file)


def test_conflicting_fact_ids_in_snapshot_fails_export():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"
        fact_a = {
            "schema_version": "financial_fact_v1",
            "id": "fact_conflict_id",
            "company": TICKER,
            "concept": "Revenues",
            "unit": "USD",
            "value": 1000.0,
            "filing_accession": "accn1",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "US-GAAP",
            "extractor_version": "v1",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
        }
        fact_b = dict(fact_a)
        fact_b["value"] = 9999.0  # Conflicting value for same ID

        att1 = _write_attachment_file(
            evidence_dir, "facts1.json", json.dumps([fact_a]).encode(), "fact_ledger"
        )
        att2 = _write_attachment_file(
            evidence_dir, "facts2.json", json.dumps([fact_b]).encode(), "fact_ledger"
        )
        _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att1, att2])

        out_file = Path(tmp_dir) / "conflict_export.json"
        with pytest.raises(ValueError, match="conflicting fact identity|conflicting definitions"):
            export_packet(run_dir, out_file)


def test_model_cannot_invent_facts_when_evidence_snapshot_exists():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"
        sec_payload = _sec_companyfacts_payload(ticker=TICKER)
        att = _write_attachment_file(
            evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
        )
        _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att])

        # Envelope A2 injects an invented fact not present in the verified snapshot
        a2_path = run_dir / "a2_envelope.json"
        a2_data = json.loads(a2_path.read_text(encoding="utf-8"))
        a2_data.setdefault("artifacts", {})["facts"] = [
            {
                "id": "fact_invented_by_model",
                "company": TICKER,
                "concept": "Revenues",
                "unit": "USD",
                "value": 500.0,
                "filing_accession": "invented",
                "availability_time": "2025-01-15T00:00:00Z",
                "retrieval_time": "2025-01-20T00:00:00Z",
                "source_location": "https://sec.gov",
                "accounting_basis": "US-GAAP",
                "extractor_version": "v1",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
            }
        ]
        a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
        _reseal(a2_path)
        _rebind_manifest(run_dir, "A2", a2_path)

        out_file = Path(tmp_dir) / "invented_export.json"
        with pytest.raises(ValueError, match="not found in the verified evidence snapshot"):
            export_packet(run_dir, out_file)


def test_model_cannot_replace_verified_facts_with_conflict():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        evidence_dir = run_dir / "evidence"
        typed_fact = {
            "schema_version": "financial_fact_v1",
            "id": "fact_real_123",
            "company": TICKER,
            "concept": "OperatingExpenses",
            "unit": "USD",
            "value": 200000.0,
            "filing_accession": "accn",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "US-GAAP",
            "extractor_version": "v1",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
        }
        att = _write_attachment_file(
            evidence_dir, "facts.json", json.dumps([typed_fact]).encode(), "fact_ledger"
        )
        _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att])

        # Envelope A2 injects same ID but different value
        conflicting_fact = dict(typed_fact)
        conflicting_fact["value"] = 9999999.0
        a2_path = run_dir / "a2_envelope.json"
        a2_data = json.loads(a2_path.read_text(encoding="utf-8"))
        a2_data.setdefault("artifacts", {})["facts"] = [conflicting_fact]
        a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
        _reseal(a2_path)
        _rebind_manifest(run_dir, "A2", a2_path)

        out_file = Path(tmp_dir) / "conflict_model_export.json"
        with pytest.raises(ValueError, match="conflicts with verified source fact"):
            export_packet(run_dir, out_file)


# ==============================================================================
# 4. No-Evidence Mock Compatibility & Ticker / Cutoff Derivation
# ==============================================================================


def test_no_evidence_mock_run_remains_compatible():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="MOCK", company="Mock Corp")

        packet = build_packet(run_dir)
        assert packet["schema_version"] == PACKET_SCHEMA_VERSION
        assert packet["facts"] == []
        assert any("No typed financial fact ledger was attached" in lim for lim in packet["limitations"])

        errors = validate_packet(packet, run_dir=run_dir)
        assert not errors

        out_file = Path(tmp_dir) / "mock_packet.json"
        res = export_packet(run_dir, out_file)
        assert out_file.is_file()
        assert res["sha256"] == file_sha256(out_file)


def test_ticker_and_cutoff_derivation_from_evidence_pack():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        evidence_dir = run_dir / "evidence"

        # Standalone evidence pack without envelopes or manifest
        typed_fact = {
            "schema_version": "financial_fact_v1",
            "id": "fact_derived_test",
            "company": "NVDA",
            "concept": "Revenues",
            "unit": "USD",
            "value": 50000000.0,
            "filing_accession": "accn_nvda",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "US-GAAP",
            "extractor_version": "v1",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
        }
        att = _write_attachment_file(
            evidence_dir, "facts.json", json.dumps([typed_fact]).encode(), "fact_ledger"
        )
        _create_evidence_pack(evidence_dir, ticker="NVDA", analysis_date="2025-01-20", attachments=[att])

        packet = build_packet(run_dir)
        assert packet["company"]["ticker"] == "NVDA"
        assert len(packet["facts"]) == 1
        assert packet["facts"][0]["id"] == "fact_derived_test"


# ==============================================================================
# 5. Packet Validation Bypass Regressions (Reconstructed Ledger Strict Check)
# ==============================================================================


def _setup_run_with_verified_evidence(tmp_path: Path) -> tuple[Path, Dict[str, Any]]:
    run_dir = tmp_path / "run"
    _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")
    evidence_dir = run_dir / "evidence"
    sec_payload = _sec_companyfacts_payload(ticker=TICKER)
    att1 = _write_attachment_file(
        evidence_dir, "companyfacts.json", json.dumps(sec_payload).encode(), "sec_companyfacts"
    )
    typed_fact = {
        "schema_version": "financial_fact_v1",
        "id": "fact_pkt_fcf_2024",
        "company": TICKER,
        "concept": "FreeCashFlow",
        "unit": "USD",
        "currency": "USD",
        "value": 450000.0,
        "filing_accession": "0000320193-25-000001",
        "availability_time": "2025-01-15T00:00:00Z",
        "retrieval_time": "2025-01-20T00:00:00Z",
        "source_location": "https://sec.gov",
        "accounting_basis": "US-GAAP",
        "extractor_version": "v1",
        "kind": "reported",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "dimensions": {"segment": "commercial"},
        "metadata": {"note": "audited"},
    }
    att2 = _write_attachment_file(
        evidence_dir, "financial_facts.json", json.dumps([typed_fact]).encode(), "fact_ledger"
    )
    ev_json = _create_evidence_pack(evidence_dir, ticker=TICKER, attachments=[att1, att2])
    from src.evidence import EvidencePack
    pack = EvidencePack(ev_json, ticker=TICKER, analysis_date=ANALYSIS_DATE)
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence"] = {
        "pack_checksum": pack.checksum,
        "state": "source_bytes_verified",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    packet = build_packet(run_dir)
    return run_dir, packet


def test_regression_packet_validation_rejects_altered_fact_value():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        assert not validate_packet(packet, run_dir=run_dir)

        # 1. Mutate numeric value
        tampered_pkt = copy.deepcopy(packet)
        target_fact = next(f for f in tampered_pkt["facts"] if f["id"] == "fact_pkt_fcf_2024")
        target_fact["value"] = 999999.0
        errs = validate_packet(tampered_pkt, run_dir=run_dir)
        assert any("value mismatch" in e.lower() and "fact_pkt_fcf_2024" in e for e in errs)

        out_file = Path(tmp_dir) / "tampered_export.json"
        with pytest.raises(ValueError, match="value mismatch"):
            export_packet(run_dir, out_file, packet=tampered_pkt)

        # 2. Mutate value from numeric to None (availability change)
        tampered_pkt2 = copy.deepcopy(packet)
        target_fact2 = next(f for f in tampered_pkt2["facts"] if f["id"] == "fact_pkt_fcf_2024")
        target_fact2["value"] = None
        errs2 = validate_packet(tampered_pkt2, run_dir=run_dir)
        assert any("value mismatch" in e.lower() and "fact_pkt_fcf_2024" in e for e in errs2)


def test_regression_packet_validation_rejects_forged_fact():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        assert not validate_packet(packet, run_dir=run_dir)

        # Inject forged fact not present in reconstructed ledger
        forged_pkt = copy.deepcopy(packet)
        forged_pkt["facts"].append(
            {
                "id": "fact_forged_invented_999",
                "company": TICKER,
                "concept": "Revenues",
                "value": 1234567.0,
                "unit": "USD",
            }
        )
        errs = validate_packet(forged_pkt, run_dir=run_dir)
        assert any(
            "unknown fact" in e.lower() and "fact_forged_invented_999" in e
            for e in errs
        )

        out_file = Path(tmp_dir) / "forged_export.json"
        with pytest.raises(ValueError, match="unknown fact"):
            export_packet(run_dir, out_file, packet=forged_pkt)


def test_regression_packet_validation_rejects_missing_fact():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        assert not validate_packet(packet, run_dir=run_dir)

        # Omit a reconstructed fact from exported packet
        omitted_pkt = copy.deepcopy(packet)
        removed_fact = omitted_pkt["facts"].pop(0)
        removed_id = removed_fact["id"]

        errs = validate_packet(omitted_pkt, run_dir=run_dir)
        assert any(
            "missing fact" in e.lower() and removed_id in e
            for e in errs
        )

        out_file = Path(tmp_dir) / "missing_export.json"
        with pytest.raises(ValueError, match="missing fact"):
            export_packet(run_dir, out_file, packet=omitted_pkt)


def test_regression_packet_validation_rejects_altered_provenance_and_period_fields():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        assert not validate_packet(packet, run_dir=run_dir)

        fid = "fact_pkt_fcf_2024"

        # Field mutations to test
        field_mutations = [
            ("accession", "0000320193-25-999999", "accession mismatch"),
            ("filing_date", "1999-01-01T00:00:00Z", "filing_date mismatch"),
            ("period_start", "2023-01-01", "period_start mismatch"),
            ("period_end", "2024-11-30", "period_end mismatch"),
            ("period_instant", "2024-12-31", "period_instant mismatch"),
            ("unit", "EUR", "unit mismatch"),
            ("currency", "EUR", "currency mismatch"),
            ("concept", "OperatingIncomeLoss", "concept mismatch"),
            ("company", "OTHER_CO", "company mismatch"),
            ("accounting_basis", "IFRS", "accounting_basis mismatch"),
            ("kind", "forecast", "kind mismatch"),
            ("source_location", "https://tampered.location", "source_location mismatch"),
            ("dimensions", {"segment": "retail"}, "dimensions mismatch"),
            ("metadata", {"note": "tampered_note"}, "metadata mismatch"),
            ("supersedes", "fact_old_123", "supersedes mismatch"),
            ("missing_reason", "explicitly missing", "missing_reason mismatch"),
            ("extractor_version", "v9.9", "extractor_version mismatch"),
        ]

        for field, new_val, expected_err in field_mutations:
            tampered = copy.deepcopy(packet)
            target = next(f for f in tampered["facts"] if f["id"] == fid)
            target[field] = new_val
            errs = validate_packet(tampered, run_dir=run_dir)
            assert any(
                expected_err in e.lower() and fid in e for e in errs
            ), f"Expected '{expected_err}' for field '{field}', got: {errs}"

            out_file = Path(tmp_dir) / f"tampered_{field}_export.json"
            with pytest.raises(ValueError, match=expected_err):
                export_packet(run_dir, out_file, packet=tampered)


def test_structural_only_validate_packet_without_run_dir_preserves_compatibility():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))

        # Without run_dir, validate_packet performs structural-only checks and passes
        errors = validate_packet(packet)
        assert not errors

        # Does not claim source truth verification without context:
        # If a fact's value is modified in a standalone packet without run_dir,
        # structural validation succeeds because source truth cannot be verified without run_dir context
        tampered_pkt = copy.deepcopy(packet)
        tampered_pkt["facts"][0]["value"] = 888888.0
        assert not validate_packet(tampered_pkt)

        # But structural defects (e.g. invalid type or forbidden keys) are still caught
        tampered_pkt["facts"][0]["value"] = "non-numeric"
        errs = validate_packet(tampered_pkt)
        assert len(errs) > 0


# ==============================================================================
# 6. Legacy Envelope-Only Artifacts & Source-Free Forged Facts Regressions
# ==============================================================================


def test_legacy_envelope_only_artifacts_project_and_validate_with_run_dir():
    """
    Runs without evidence/ or on-disk facts files, but containing legacy envelope-only
    artifacts['facts'] or artifacts['fact_ledger'], must project into packet facts
    and validate cleanly when run_dir is provided, rejecting tampered values.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker=TICKER, company="Packet Corp")

        # Inject legacy envelope-only fact into A2 envelope artifacts
        a2_path = run_dir / "a2_envelope.json"
        a2_data = json.loads(a2_path.read_text(encoding="utf-8"))
        legacy_fact = {
            "schema_version": "financial_fact_v1",
            "id": "fact_legacy_envelope_1",
            "company": TICKER,
            "concept": "OperatingIncomeLoss",
            "unit": "USD",
            "currency": "USD",
            "value": 750000.0,
            "filing_accession": "0000320193-25-000001",
            "availability_time": "2025-01-15T00:00:00Z",
            "retrieval_time": "2025-01-20T00:00:00Z",
            "source_location": "https://sec.gov",
            "accounting_basis": "US-GAAP",
            "extractor_version": "v1",
            "kind": "reported",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
        }
        a2_data.setdefault("artifacts", {})["facts"] = [legacy_fact]
        a2_path.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")
        _reseal(a2_path)
        _rebind_manifest(run_dir, "A2", a2_path)

        # Build packet - legacy envelope fact is projected
        packet = build_packet(run_dir)
        pkt_fact_ids = [f["id"] for f in packet["facts"]]
        assert "fact_legacy_envelope_1" in pkt_fact_ids

        # validate_packet with run_dir reconciles legacy envelope facts and succeeds
        errors = validate_packet(packet, run_dir=run_dir)
        assert not errors, f"Expected clean validation for legacy envelope run, got: {errors}"

        # Export succeeds
        out_file = Path(tmp_dir) / "legacy_export.json"
        res = export_packet(run_dir, out_file)
        assert out_file.is_file()
        assert res["sha256"] == file_sha256(out_file)

        # Mutating the legacy fact in the packet fails validation against run_dir
        tampered_pkt = copy.deepcopy(packet)
        target = next(f for f in tampered_pkt["facts"] if f["id"] == "fact_legacy_envelope_1")
        target["value"] = 9999999.0
        errs = validate_packet(tampered_pkt, run_dir=run_dir)
        assert any("value mismatch" in e.lower() and "fact_legacy_envelope_1" in e for e in errs)


def test_forged_packet_facts_in_source_free_run_rejected_when_run_dir_provided():
    """
    In an otherwise source-free run (empty fact ledger), validate_packet(packet, run_dir=run_dir)
    must always compare canonical facts against run_dir and reject forged facts.
    Omitting run_dir performs structural-only validation and does not reject for unknown fact.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="MOCK", company="Mock Corp")

        packet = build_packet(run_dir)
        assert packet["facts"] == []

        # Clean packet validates cleanly with run_dir
        assert not validate_packet(packet, run_dir=run_dir)

        # Inject forged fact into packet
        forged_pkt = copy.deepcopy(packet)
        forged_pkt["facts"].append(
            {
                "id": "fact_forged_source_free",
                "company": "MOCK",
                "concept": "Revenues",
                "value": 1000000.0,
                "unit": "USD",
            }
        )

        # Validation with run_dir MUST reject forged fact even when ledger is empty
        errs = validate_packet(forged_pkt, run_dir=run_dir)
        assert any(
            "unknown fact" in e.lower() and "fact_forged_source_free" in e
            for e in errs
        ), f"Expected unknown fact error, got: {errs}"

        out_file = Path(tmp_dir) / "forged_export.json"
        with pytest.raises(ValueError, match="unknown fact"):
            export_packet(run_dir, out_file, packet=forged_pkt)

        # When run_dir is omitted (structural-only), unknown-fact run_dir reconciliation is skipped
        assert not any("unknown fact" in e.lower() for e in validate_packet(forged_pkt))


# ==============================================================================
# 7. Exact Finite Numeric Equality & Value Edge Cases
# ==============================================================================


def test_exact_finite_numeric_equality_rejects_nextafter_mutation():
    """
    _compare_projected_facts and validate_packet(..., run_dir=run_dir) enforce exact
    finite numeric equality and reject tiny nextafter floating point mutations.
    """
    import math

    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        orig_val = 450000.0
        target_fact = next(f for f in packet["facts"] if f["id"] == "fact_pkt_fcf_2024")
        assert target_fact["value"] == orig_val

        # 1. Mutate by nextafter upwards (+eps)
        mutated_up = math.nextafter(orig_val, math.inf)
        assert mutated_up != orig_val

        tampered_pkt_up = copy.deepcopy(packet)
        target_up = next(f for f in tampered_pkt_up["facts"] if f["id"] == "fact_pkt_fcf_2024")
        target_up["value"] = mutated_up

        errs_up = validate_packet(tampered_pkt_up, run_dir=run_dir)
        assert any("value mismatch" in e.lower() and "fact_pkt_fcf_2024" in e for e in errs_up)

        # 2. Mutate by nextafter downwards (-eps)
        mutated_down = math.nextafter(orig_val, -math.inf)
        assert mutated_down != orig_val

        tampered_pkt_down = copy.deepcopy(packet)
        target_down = next(f for f in tampered_pkt_down["facts"] if f["id"] == "fact_pkt_fcf_2024")
        target_down["value"] = mutated_down

        errs_down = validate_packet(tampered_pkt_down, run_dir=run_dir)
        assert any("value mismatch" in e.lower() and "fact_pkt_fcf_2024" in e for e in errs_down)


def test_numeric_equality_accepts_int_vs_float():
    """
    Exact finite numeric equality permits int-vs-float equality (e.g. 450000 == 450000.0).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        tampered_pkt = copy.deepcopy(packet)
        target = next(f for f in tampered_pkt["facts"] if f["id"] == "fact_pkt_fcf_2024")
        target["value"] = int(450000)  # int instead of float
        assert isinstance(target["value"], int)

        errs = validate_packet(tampered_pkt, run_dir=run_dir)
        assert not errs, f"Expected int-vs-float equality to pass, got errors: {errs}"


def test_numeric_equality_rejects_booleans():
    """
    Booleans (True/False) must never be accepted as numeric float values.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))

        for bool_val in (True, False):
            tampered_pkt = copy.deepcopy(packet)
            target = next(f for f in tampered_pkt["facts"] if f["id"] == "fact_pkt_fcf_2024")
            target["value"] = bool_val

            # validate_packet with run_dir rejects boolean
            errs = validate_packet(tampered_pkt, run_dir=run_dir)
            assert any("must be numeric float" in e.lower() for e in errs)

            # validate_packet without run_dir (structural-only) also rejects boolean
            errs_struct = validate_packet(tampered_pkt)
            assert any("must be a numeric float" in e.lower() for e in errs_struct)


def test_numeric_equality_rejects_nan_and_inf():
    """
    Non-finite values (NaN, +Inf, -Inf) must be rejected without unhandled errors.
    """
    import math

    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))

        for non_finite in (float("nan"), float("inf"), float("-inf")):
            tampered_pkt = copy.deepcopy(packet)
            target = next(f for f in tampered_pkt["facts"] if f["id"] == "fact_pkt_fcf_2024")
            target["value"] = non_finite

            errs = validate_packet(tampered_pkt, run_dir=run_dir)
            assert any("must be finite" in e.lower() for e in errs)

            errs_struct = validate_packet(tampered_pkt)
            assert any("must be finite" in e.lower() for e in errs_struct)


def test_numeric_equality_and_project_fact_handles_10_pow_400():
    """
    10**400 and -10**400 must be handled without unhandled OverflowError in
    _project_fact, _compare_projected_facts, and validate_packet.
    _project_fact must not crash and must not fabricate silently valid numbers (e.g. None).
    """
    from src.research_packet import _compare_projected_facts, _project_fact

    # 1. _project_fact does not crash on 10**400 and preserves value without fabricating None or float
    big_fact = {"id": "f_big", "company": "PKT", "concept": "Revenues", "value": 10**400}
    proj_big = _project_fact(big_fact)
    assert proj_big is not None
    assert proj_big["value"] == 10**400

    # _project_fact does not convert booleans to 1.0/0.0
    bool_fact = {"id": "f_bool", "company": "PKT", "concept": "Revenues", "value": True}
    proj_bool = _project_fact(bool_fact)
    assert proj_bool is not None
    assert proj_bool["value"] is True

    # _project_fact does not convert NaN/Inf to None
    nan_fact = {"id": "f_nan", "company": "PKT", "concept": "Revenues", "value": float("nan")}
    proj_nan = _project_fact(nan_fact)
    assert proj_nan is not None
    import math
    assert math.isnan(proj_nan["value"])

    # 2. _compare_projected_facts rejects 10**400 without unhandled OverflowError
    diffs = _compare_projected_facts("f_big", proj_big, {"id": "f_big", "value": 450000.0})
    assert any("must be finite" in d.lower() or "value mismatch" in d.lower() for d in diffs)

    # 3. validate_packet rejects 10**400 without unhandled OverflowError
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir, packet = _setup_run_with_verified_evidence(Path(tmp_dir))
        tampered_pkt = copy.deepcopy(packet)
        target = next(f for f in tampered_pkt["facts"] if f["id"] == "fact_pkt_fcf_2024")
        target["value"] = 10**400

        errs = validate_packet(tampered_pkt, run_dir=run_dir)
        assert any("must be finite" in e.lower() for e in errs)

        errs_struct = validate_packet(tampered_pkt)
        assert any("must be finite" in e.lower() for e in errs_struct)
