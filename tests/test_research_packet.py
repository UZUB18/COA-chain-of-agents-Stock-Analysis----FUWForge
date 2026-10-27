"""
Tests for versioned research-packet export (Task 6).

Verifies that:
- A complete run exports and validates cleanly (explicit audit state, bounded content,
  no transcripts or raw filings, unapproved status).
- An incomplete or halted run is exportable, but its audit state and limitations
  explicitly indicate incompleteness.
- Wrong company identity is detected and rejected.
- A claim referencing a missing fact ID is detected and rejected.
- An unsupported schema version is rejected.
- Export writes deterministic bytes, changing the packet changes the external SHA-256,
  and the external hash is not self-referentially embedded inside the packet.
- CLI subcommand `export-packet` functions end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.research_packet import (
    APPROVAL_STATUS_PENDING,
    DISCLAIMER_TEXT,
    PACKET_SCHEMA_VERSION,
    build_packet,
    export_packet,
    file_sha256,
    validate_packet,
)


def _build_mock_run(output_dir: Path, ticker: str = "PKT", company: str = "Packet Corp") -> Path:
    """Run a complete mock multi-agent analysis."""
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company=company,
        ticker=ticker,
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("packet_test_run", params, output_root=output_dir)
    return output_dir


def test_complete_run_exports_and_validates_cleanly():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="PKT", company="Packet Corp")

        packet = build_packet(run_dir)

        # Basic packet structure
        assert packet["schema_version"] == PACKET_SCHEMA_VERSION
        assert packet["company"]["ticker"] == "PKT"
        assert packet["company"]["name"] == "Packet Corp"
        assert packet["analysis_date"] == "2026-02-01"
        assert packet["approval_status"] == APPROVAL_STATUS_PENDING
        assert packet["disclaimer"] == DISCLAIMER_TEXT

        # Explicit audit state
        assert "audit" in packet
        assert packet["audit"]["overall_state"] == "verified"
        assert packet["audit"]["complete"] is True
        assert packet["audit"]["levels"]["envelope_integrity"] == "passed"
        assert packet["audit"]["levels"]["calculation_replay"] == "passed"

        # Bounded content
        assert isinstance(packet["facts"], list)
        assert isinstance(packet["calculations"], list)
        assert len(packet["calculations"]) >= 1  # At least BeneishM witness from A3
        assert packet["calculations"][0]["formula_id"] == "beneish_m_score"

        assert isinstance(packet["claims"], list)
        assert len(packet["claims"]) >= 1

        assert isinstance(packet["sources"], list)
        for src in packet["sources"]:
            assert "text" not in src
            assert "raw_text" not in src
            assert "body" not in src
            assert "transcript" not in src

        assert isinstance(packet["versions"], dict)
        assert packet["versions"]["packet_schema_version"] == PACKET_SCHEMA_VERSION
        assert "beneish_m_score" in packet["versions"]

        assert isinstance(packet["limitations"], list)
        assert len(packet["limitations"]) >= 1
        assert DISCLAIMER_TEXT in packet["limitations"]

        # Validation
        errors = validate_packet(packet, run_dir=run_dir)
        assert not errors, f"Validation failed: {errors}"

        # Export
        out_file = Path(tmp_dir) / "exported_packet.json"
        res = export_packet(run_dir, out_file)
        assert out_file.is_file()
        assert res["path"] == str(out_file)
        assert res["sha256"].startswith("sha256:")
        assert res["sha256"] == file_sha256(out_file)

        # Verify exported content matches and has no embedded self-hash
        exported_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert exported_data["schema_version"] == PACKET_SCHEMA_VERSION
        assert "content_checksum" not in exported_data
        assert "export_sha256" not in exported_data


def test_packet_states_when_the_backend_was_the_mock_provider():
    """
    Regression: an exported packet carried no trace of the backend.

    The packet is the artifact that leaves the run directory for a human
    reviewer, and it said nothing about the mock provider: zero occurrences of
    "mock", "synthetic" or "provider", and no depth. A fixture-generated packet
    was therefore indistinguishable from a live one. The limitation is
    schema-safe (``limitations`` is a list of strings).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="MOCKPKT", company="Fixture Corp")
        packet = build_packet(run_dir)
        joined = " ".join(packet["limitations"]).lower()
        assert "mock provider" in joined
        assert "not research" in joined
        assert not validate_packet(packet, run_dir=run_dir)


def test_packet_omits_the_mock_limitation_for_a_live_backend():
    """Falsification partner: the limitation is backend-driven, not constant."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="LIVEPKT", company="Live Corp")
        manifest_path = run_dir / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["configuration"]["provider"] = "gemini"
        manifest["configuration"]["model"] = "gemini-test-model"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        packet = build_packet(run_dir)
        joined = " ".join(packet["limitations"]).lower()
        assert "mock provider" not in joined


def test_incomplete_run_is_exportable_with_accurate_audit_state():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="INC", company="Incomplete Corp")

        # Simulate incomplete run by deleting A7 and A6
        (run_dir / "a7_envelope.json").unlink()
        (run_dir / "a6_envelope.json").unlink()

        packet = build_packet(run_dir)

        # Audit state must reflect incompleteness
        assert packet["audit"]["complete"] is False
        assert packet["audit"]["levels"]["completeness"] == "incomplete"
        assert packet["audit"]["overall_state"] in ("incomplete", "failed")

        # Limitations must note incompleteness
        assert any("incomplete" in lim.lower() for lim in packet["limitations"])

        # Packet must still validate (it truthfully represents an incomplete run)
        errors = validate_packet(packet, run_dir=run_dir)
        assert not errors, f"Validation should succeed for truthful incomplete packet: {errors}"

        # Export works
        out_file = Path(tmp_dir) / "incomplete_packet.json"
        res = export_packet(run_dir, out_file)
        assert out_file.is_file()
        assert res["sha256"] == file_sha256(out_file)


def test_halted_run_is_exportable_with_accurate_audit_state():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        params = OrchestratorParams(
            company="Halt Corp",
            ticker="HALT",
            analysis_date="2026-02-01",
            depth="Standard",
            focus=[],
            weights="default",
        )
        params.__dict__["fail_gate"] = True
        with pytest.raises(RuntimeError):
            orchestrator.run("halted_run", params, output_root=run_dir)

        packet = build_packet(run_dir)
        assert packet["audit"]["complete"] is False
        assert packet["audit"]["levels"]["completeness"] == "incomplete"
        assert any("incomplete" in lim.lower() or "error" in lim.lower() for lim in packet["limitations"])

        out_file = Path(tmp_dir) / "halted_packet.json"
        res = export_packet(run_dir, out_file)
        assert out_file.is_file()
        assert res["sha256"] == file_sha256(out_file)


def test_halted_manifest_run_is_exportable():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="HALTED2", company="Halted Corp 2")

        # Set status to halted in manifest
        manifest_path = run_dir / "run_manifest.json"
        if manifest_path.is_file():
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            m["status"] = "halted"
            manifest_path.write_text(json.dumps(m, indent=2), encoding="utf-8")

        packet = build_packet(run_dir)
        assert packet["audit"]["halted"] is True
        assert packet["status"] == "halted"
        assert any("halted" in lim.lower() for lim in packet["limitations"])


def test_wrong_company_identity_is_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="AAA", company="Triple A")

        packet = build_packet(run_dir)
        # Deliberately change packet company
        packet["company"]["ticker"] = "ZZZ"
        packet["company"]["name"] = "Wrong Company"

        errors = validate_packet(packet, run_dir=run_dir)
        assert any("ticker mismatch" in e.lower() for e in errors)
        assert any("name mismatch" in e.lower() for e in errors)

        out_file = Path(tmp_dir) / "bad_export.json"
        with pytest.raises(ValueError, match="Research packet validation failed"):
            export_packet(run_dir, out_file, packet=packet)


def test_missing_fact_id_reference_in_claim_is_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="FACT", company="Fact Test Corp")

        packet = build_packet(run_dir)

        # Inject a claim citing a non-existent fact ID
        packet["claims"].append(
            {
                "id": "bad_claim_1",
                "category": "bull_thesis",
                "statement": "Revenue increased dramatically",
                "fact_ids": ["fact_non_existent_12345"],
            }
        )

        errors = validate_packet(packet, run_dir=run_dir)
        assert any("fact_non_existent_12345" in e for e in errors)

        # Adding a fact resolves only structural claim references, not provenance.
        packet["facts"].append(
            {
                "id": "fact_non_existent_12345",
                "company": "FACT",
                "concept": "Revenues",
                "value": 1000000.0,
            }
        )
        contextual_errors = validate_packet(packet, run_dir=run_dir)
        assert any(
            "unknown fact 'fact_non_existent_12345'" in e
            for e in contextual_errors
        )
        structural_errors = validate_packet(packet)
        assert not any("fact_non_existent_12345" in e for e in structural_errors)


def test_fact_company_mismatch_is_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="TICK1", company="Tick One")

        packet = build_packet(run_dir)
        packet["facts"].append(
            {
                "id": "fact_other_company",
                "company": "WRONG_TICKER",
                "concept": "NetIncomeLoss",
                "value": 50000.0,
            }
        )

        errors = validate_packet(packet, run_dir=run_dir)
        assert any("WRONG_TICKER" in e for e in errors)


def test_unsupported_schema_version_is_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="VER", company="Version Corp")

        packet = build_packet(run_dir)
        packet["schema_version"] = "research_packet_v999"

        errors = validate_packet(packet, run_dir=run_dir)
        assert any("Unsupported schema version" in e for e in errors)


def test_approval_claim_is_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="APP", company="Approval Corp")

        packet = build_packet(run_dir)
        packet["approval_status"] = "approved"

        errors = validate_packet(packet, run_dir=run_dir)
        assert any("approval" in e.lower() for e in errors)


def test_unbounded_source_is_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="SRC", company="Source Corp")

        packet = build_packet(run_dir)
        packet["sources"].append(
            {
                "id": "src_with_full_text",
                "url": "https://example.com/10k.htm",
                "text": "Full SEC 10-K transcript with thousands of words...",
            }
        )

        errors = validate_packet(packet, run_dir=run_dir)
        assert any("unbounded" in e.lower() or "additionalproperties" in e.lower() for e in errors)


def test_deterministic_export_bytes_and_hash_change():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="DET", company="Deterministic Co")

        out1 = Path(tmp_dir) / "out1.json"
        out2 = Path(tmp_dir) / "out2.json"

        packet = build_packet(run_dir)
        # Freeze timestamp to check byte determinism for same packet payload
        packet["generated_at"] = "2026-02-01T12:00:00Z"

        res1 = export_packet(run_dir, out1, packet=packet)
        res2 = export_packet(run_dir, out2, packet=packet)

        # Same packet produces identical bytes and hashes
        assert out1.read_bytes() == out2.read_bytes()
        assert res1["sha256"] == res2["sha256"]

        # Changing packet content changes external hash
        packet["claims"].append(
            {
                "id": "extra_claim_test",
                "category": "note",
                "statement": "An extra claim note",
            }
        )
        out3 = Path(tmp_dir) / "out3.json"
        res3 = export_packet(run_dir, out3, packet=packet)
        assert res3["sha256"] != res1["sha256"]


def test_cli_export_packet_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="CLI", company="CLI Corp")

        out_file = Path(tmp_dir) / "cli_packet.json"

        cmd = [
            sys.executable,
            "-m",
            "src.controller",
            "export-packet",
            str(run_dir),
            "--output",
            str(out_file),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        assert proc.returncode == 0, f"CLI export failed: {proc.stderr}\n{proc.stdout}"
        assert out_file.is_file()
        assert "Exported research packet" in proc.stdout
        assert "External SHA-256" in proc.stdout
        assert "verified" in proc.stdout

        # Verify output file
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["company"]["ticker"] == "CLI"


def test_cli_export_packet_strict_on_incomplete_run():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run_dir = Path(tmp_dir) / "run"
        _build_mock_run(run_dir, ticker="STRICT", company="Strict Corp")

        # Delete envelope to make run incomplete
        (run_dir / "a7_envelope.json").unlink()

        out_file = Path(tmp_dir) / "strict_fail.json"
        cmd = [
            sys.executable,
            "-m",
            "src.controller",
            "export-packet",
            str(run_dir),
            "--output",
            str(out_file),
            "--strict",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        assert proc.returncode != 0
        assert "Strict export failed" in proc.stderr
