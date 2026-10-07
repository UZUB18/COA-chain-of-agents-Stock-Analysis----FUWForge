"""
End-to-end integration tests for FUWForge automated multi-agent pipeline.
Validates:
1. Automated execution with synthetic / mock data (A1–A7, A6B, A6R).
2. Randomized artificial data generation with scorecard & financial consistency.
3. Canonical section ordering (0–18) and depth mode skipping (Quick mode).
4. Focus tag routing (e.g. AI strategy placement).
5. A1 GOD gate failure halting the run.
6. CLI 'run' command invocation.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.registry import default_registry, mock_registry
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams


def test_automated_pipeline_standard():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_auto_std_"))
    try:
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        params = OrchestratorParams(
            company="Acme Cloud Systems",
            ticker="ACME",
            analysis_date="2025-01-31",
            depth="Standard",
            focus=["ai_strategy"],
            weights="default",
        )

        summary = orchestrator.run(
            run_id="run_std_01",
            params=params,
            mode="automated",
            output_root=temp_dir,
        )

        # 1. Output files exist
        assert "outputs" in summary
        report_file = Path(summary["outputs"]["report"])
        artifacts_file = Path(summary["outputs"]["artifacts"])
        provenance_file = Path(summary["outputs"]["provenance"])

        assert report_file.exists(), "Report markdown was not generated."
        assert artifacts_file.exists(), "Aggregated artifacts JSON was not generated."
        assert provenance_file.exists(), "Provenance log was not generated."

        # 2. Individual agent envelopes exist in output directory
        for agent_id in ["a1", "a2", "a3", "a4", "a5", "a6b", "a6r", "a6", "a7"]:
            env_path = temp_dir / f"{agent_id}_envelope.json"
            assert env_path.exists(), f"Missing individual envelope: {env_path.name}"
            env_data = json.loads(env_path.read_text(encoding="utf-8"))
            assert env_data["schema_version"] == "2.1"
            assert env_data["gates"]["passed"] is True

        # 3. Canonical section ordering
        content = report_file.read_text(encoding="utf-8")
        markers = [
            "# Section 0:",
            "# Section 1:",
            "# Section 2:",
            "# Section 3:",
            "### AI Infrastructure",
            "# Section 4:",
            "# Section 5:",
            "# Section 6:",
            "# Section 7:",
            "# Section 8:",
            "# Section 9:",
            "# Section 10:",
            "# Section 11:",
            "# Section 12:",
            "# Section 13:",
            "# Section 14:",
            "# Section 15:",
            "# Section 16:",
            "# Section 17:",
            "# Section 18:",
            "### Raw Data Intake",
        ]
        last_pos = -1
        for m in markers:
            pos = content.find(m)
            assert pos != -1, f"Missing section marker: '{m}'"
            assert pos > last_pos, f"Order violation: '{m}' pos {pos} <= last_pos {last_pos}"
            last_pos = pos

        # 4. Checksum consistency
        checksum = summary["checksum"]
        assert checksum and checksum.startswith("sha256:"), f"Invalid checksum: {checksum}"
        artifacts_data = json.loads(artifacts_file.read_text(encoding="utf-8"))
        for aid, env in artifacts_data.items():
            assert env["metrics"]["data_checksum"] == checksum, f"{aid} checksum mismatched"

        # 5. Runtime metrics collected
        metrics_summary = summary["metrics"]
        for aid in ["A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"]:
            assert aid in metrics_summary
            assert metrics_summary[aid]["status"] == "passed"
            assert metrics_summary[aid]["latency_ms"] > 0
            assert metrics_summary[aid]["tokens"] > 0

        print("  [PASS] test_automated_pipeline_standard")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_automated_pipeline_random_data():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_auto_rand_"))
    try:
        registry = mock_registry(randomize=True)
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, registry=registry)
        params = OrchestratorParams(
            company="Randomized Dynamics",
            ticker="RAND",
            analysis_date="2025-01-31",
            depth="Standard",
            focus=[],
            weights="default",
        )

        summary = orchestrator.run(
            run_id="run_rand_01",
            params=params,
            mode="automated",
            output_root=temp_dir,
        )

        a1_data = json.loads((temp_dir / "a1_envelope.json").read_text(encoding="utf-8"))
        cap_check = a1_data["artifacts"]["market_cap_check"]
        expected_cap = round(cap_check["price"] * cap_check["shares"], 2)
        assert abs(cap_check["cap"] - expected_cap) < 1.0, "Market cap check math inconsistent"

        a7_data = json.loads((temp_dir / "a7_envelope.json").read_text(encoding="utf-8"))
        scorecard = a7_data["artifacts"]["Scorecard"]
        calculated_total = sum(v["weighted"] for k, v in scorecard.items() if k != "Overall")
        overall_score = scorecard["Overall"]["score"]
        assert abs(calculated_total - overall_score) < 0.05, f"Scorecard math discrepancy: {calculated_total} vs {overall_score}"

        print("  [PASS] test_automated_pipeline_random_data")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_automated_pipeline_quick_depth():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_auto_quick_"))
    try:
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        params = OrchestratorParams(
            company="Quick Test Co",
            ticker="QTC",
            analysis_date="2025-01-31",
            depth="Quick",
            focus=[],
            weights="default",
        )

        summary = orchestrator.run(
            run_id="run_quick_01",
            params=params,
            mode="automated",
            output_root=temp_dir,
        )

        report_content = Path(summary["outputs"]["report"]).read_text(encoding="utf-8")
        assert "# Section 13:" not in report_content, "Section 13 should be filtered out in Quick mode"
        assert "# Section 17:" not in report_content, "Section 17 should be filtered out in Quick mode"
        assert "# Section 18:" not in report_content, "Section 18 should be filtered out in Quick mode"
        assert "# Section 0:" in report_content, "Section 0 must be present in Quick mode"
        assert "# Section 16:" in report_content, "Section 16 must be present in Quick mode"

        print("  [PASS] test_automated_pipeline_quick_depth")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_gate_failure_halts_run():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_gate_fail_"))
    try:
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        params = OrchestratorParams(
            company="Incomplete Data Inc",
            ticker="FAIL",
            analysis_date="2025-01-31",
            depth="Standard",
            focus=[],
            weights="default",
        )
        params.__dict__["fail_gate"] = True

        gate_failed = False
        try:
            orchestrator.run(
                run_id="run_fail_01",
                params=params,
                mode="automated",
                output_root=temp_dir,
            )
        except RuntimeError as exc:
            gate_failed = True
            assert "A1 gate failure halts the run" in str(exc)

        assert gate_failed, "Orchestrator did not halt on A1 gate failure."
        assert (temp_dir / "a1_envelope.json").exists(), "A1 failure envelope should still be saved"
        a1_data = json.loads((temp_dir / "a1_envelope.json").read_text(encoding="utf-8"))
        assert a1_data["gates"]["passed"] is False
        assert "MISSING_DATA_TABLE" in a1_data["sections_md"]

        # Missing data report markdown must be generated
        missing_md = temp_dir / "FAIL_2025-01-31_missing_data.md"
        assert missing_md.exists(), "Missing data report markdown was not written"
        assert "| Metric / Filing |" in missing_md.read_text(encoding="utf-8")

        # Provenance log must document the failure
        prov_file = temp_dir / "FAIL_2025-01-31_provenance.log"
        assert prov_file.exists(), "Provenance log should exist even on gate failure"
        prov_content = prov_file.read_text(encoding="utf-8")
        assert "A1 GOD gate FAILED" in prov_content

        print("  [PASS] test_gate_failure_halts_run")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_controller_cli_run_command():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_cli_run_"))
    try:
        cmd = [
            sys.executable,
            "-m",
            "src.controller",
            "run",
            str(temp_dir),
            "--run-id",
            "cli_test_run",
            "--ticker",
            "CLITEST",
            "--company",
            "CLI Test Corp",
            "--analysis-date",
            "2025-01-31",
            "--depth",
            "Standard",
            "--provider",
            "mock",
        ]
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res.returncode == 0, f"CLI run failed with exit code {res.returncode}:\n{res.stderr}"
        assert "Automated run completed successfully." in res.stdout
        assert (temp_dir / "CLITEST_2025-01-31_report.md").exists()

        print("  [PASS] test_controller_cli_run_command")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_controller_cli_defaults():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_cli_def_"))
    try:
        cmd = [
            sys.executable,
            "-m",
            "src.controller",
            "run",
            str(temp_dir),
            "--ticker",
            "DEFTEST",
            "--provider",
            "mock",
        ]
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert res.returncode == 0, f"CLI run with defaults failed: {res.stderr}"
        assert "Automated run completed successfully." in res.stdout
        generated_reports = list(temp_dir.glob("DEFTEST_*_report.md"))
        assert len(generated_reports) == 1, "Expected report generated with default analysis date"

        print("  [PASS] test_controller_cli_defaults")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("Running FUWForge automated multi-agent pipeline tests...")
    test_automated_pipeline_standard()
    test_automated_pipeline_random_data()
    test_automated_pipeline_quick_depth()
    test_gate_failure_halts_run()
    test_controller_cli_run_command()
    test_controller_cli_defaults()
    print("ALL AUTOMATED PIPELINE TESTS PASSED!")
