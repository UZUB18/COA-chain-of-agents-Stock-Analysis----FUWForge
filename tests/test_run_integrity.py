"""
Run integrity tests:
1. Automated runs produce content checksums, upstream lineage, run_summary.json
   and a report with run metadata front matter.
2. verify_run re-verifies checksums, lineage and provenance, and detects tampering.
3. Halted runs still write run_summary.json with a halt reason.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.run_audit import verify_run
from src.utils.checksum import envelope_content_checksum


def _run(root: Path, tmp: Path, ticker: str = "AUDIT") -> dict:
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Audit Co",
        ticker=ticker,
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    return orchestrator.run("integrity_run", params, output_root=tmp)


def test_run_artifacts_and_verification():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_integrity_"))
    try:
        summary = _run(PROJECT_ROOT, tmp)
        assert summary["status"] == "completed"
        assert summary["provider"] == "mock"

        summary_file = tmp / "run_summary.json"
        assert summary_file.exists(), "run_summary.json was not written"
        payload = json.loads(summary_file.read_text(encoding="utf-8"))
        assert payload["status"] == "completed"
        assert payload["checksum"].startswith("sha256:")

        # Report front matter identifies the run.
        report = (tmp / "AUDIT_2026-02-01_report.md").read_text(encoding="utf-8")
        assert report.startswith("---\n")
        assert "run_id: integrity_run" in report
        assert "frozen_checksum:" in report

        # Every envelope carries a content checksum and lineage.
        for agent_id in ["a1", "a2", "a3", "a4", "a5", "a6b", "a6r", "a6", "a7"]:
            data = json.loads(
                (tmp / f"{agent_id}_envelope.json").read_text(encoding="utf-8")
            )
            assert data["content_checksum"] == envelope_content_checksum(data)
        a7 = json.loads((tmp / "a7_envelope.json").read_text(encoding="utf-8"))
        # Lineage records what A7 actually consumed, in canonical (sorted)
        # order. Acceptance order is a storage choice and is not evidence of
        # causality: A6 sorts before A6B, and must still be recorded.
        assert a7["upstream"] == [
            {
                "agent": agent,
                "content_checksum": json.loads(
                    (tmp / f"{agent.lower()}_envelope.json").read_text(encoding="utf-8")
                )["content_checksum"],
            }
            for agent in sorted(["A1", "A2", "A3", "A4", "A5", "A6", "A6B", "A6R"])
        ]

        result = verify_run(tmp)
        assert result.valid, result.errors
        assert not result.warnings

        # Tampering with content invalidates the run.
        a4_path = tmp / "a4_envelope.json"
        tampered = json.loads(a4_path.read_text(encoding="utf-8"))
        tampered["sections_md"]["SECTION_6_LEADERSHIP_GOVERNANCE"] += "\nINJECTED"
        a4_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
        tamper_result = verify_run(tmp)
        assert not tamper_result.valid
        assert any("content checksum mismatch" in err for err in tamper_result.errors)

        # Restore the envelope, then verify the provenance chain tamper check.
        restored = json.loads(a4_path.read_text(encoding="utf-8"))
        restored["sections_md"]["SECTION_6_LEADERSHIP_GOVERNANCE"] = restored[
            "sections_md"
        ]["SECTION_6_LEADERSHIP_GOVERNANCE"].replace("\nINJECTED", "")
        a4_path.write_text(json.dumps(restored, indent=2), encoding="utf-8")
        assert verify_run(tmp).valid

        prov_path = tmp / "AUDIT_2026-02-01_provenance.log"
        lines = prov_path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("accepted", "rewritten")
        assert "rewritten" in lines[0], (
            "expected the first provenance entry to be tamperable"
        )
        prov_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        prov_result = verify_run(tmp)
        assert not prov_result.valid
        assert any(
            "provenance" in err and "mismatch" in err for err in prov_result.errors
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_halted_run_writes_summary():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_halted_"))
    try:
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        params = OrchestratorParams(
            company="Halt Co",
            ticker="HALT",
            analysis_date="2026-02-01",
            depth="Standard",
            focus=[],
            weights="default",
        )
        params.__dict__["fail_gate"] = True
        try:
            orchestrator.run("halted_run", params, output_root=tmp)
            raise AssertionError("expected the run to halt")
        except RuntimeError:
            pass
        payload = json.loads((tmp / "run_summary.json").read_text(encoding="utf-8"))
        assert payload["status"] == "halted"
        assert "A1 gate failure" in payload["halt_reason"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
