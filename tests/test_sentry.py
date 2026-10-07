"""
Tests for FUWForge Jev Thesis Sentry:
1. Thesis state extraction from completed run directories.
2. Document evaluation (benign vs contradictory).
3. Report formatting and machine-readable output.
4. CLI monitor integration and exit codes (0 = intact, 1 = breached).
5. Optional live Jev API test (marked with @pytest.mark.live).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.sentry import (
    extract_thesis_state,
    evaluate_document,
    format_sentry_report,
    SentryResult,
)


def _cli(args):
    return subprocess.run(
        [sys.executable, "-m", "src.controller", *args],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_extract_thesis_state_from_mock_run():
    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    assert run_dir.is_dir(), "eval_mock run directory is expected to exist"

    ticker, company, bull_cases, danger_zone = extract_thesis_state(run_dir)
    assert ticker == "MSFT"
    assert company == "Microsoft Corp"
    assert len(bull_cases) >= 3
    assert any(b.get("id") == "B1" for b in bull_cases)
    assert len(danger_zone) >= 3


def test_extract_thesis_state_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # Empty directory should raise or return defaults
        ticker, company, bull_cases, danger_zone = extract_thesis_state(tmp_dir)
        assert ticker == "UNKNOWN"
        assert bull_cases == []
        assert danger_zone == []


def test_evaluate_document_mock_benign():
    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    res = evaluate_document(
        run_dir=run_dir,
        doc_text="Company published routine corporate sustainability update.",
        provider="mock",
    )

    assert isinstance(res, SentryResult)
    assert res.ticker == "MSFT"
    assert not res.requires_rerun
    assert res.action == "ignore"
    assert res.materiality == "noise"
    assert all(score < 0.20 for score in res.contradiction_scores.values())
    assert len(res.threatened_theses) == 0


def test_evaluate_document_mock_contradiction():
    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    res = evaluate_document(
        run_dir=run_dir,
        doc_text="Major cloud outage and severe customer churn in enterprise cloud segment.",
        provider="mock",
    )

    assert isinstance(res, SentryResult)
    assert res.requires_rerun
    assert res.action == "trigger_deep_rerun"
    assert res.materiality in ("critical", "meaningful")
    assert res.contradiction_scores.get("B1", 0) >= 0.75
    assert any(item.get("id") == "B1" for item in res.threatened_theses)


def test_format_sentry_report():
    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    res = evaluate_document(
        run_dir=run_dir,
        doc_text="Cloud outage and severe churn.",
        provider="mock",
    )
    report = format_sentry_report(res)

    assert "FUWForge Jev Thesis Sentry" in report
    assert "Microsoft Corp (MSFT)" in report
    assert "THESIS BREACHED" in report
    assert "[B1]" in report
    assert "ACTION REQUIRED" in report


def test_cli_monitor_benign():
    res = _cli(
        [
            "monitor",
            "runs/eval_mock",
            "--doc",
            "Routine marketing announcement video released.",
            "--provider",
            "mock",
        ]
    )
    assert res.returncode == 0, res.stderr
    assert "THESIS INTACT" in res.stdout
    assert "No pipeline re-analysis needed" in res.stdout


def test_cli_monitor_breach():
    res = _cli(
        [
            "monitor",
            "runs/eval_mock",
            "--doc",
            "Massive cloud outage and customer churn drops enterprise retention by 30%.",
            "--provider",
            "mock",
        ]
    )
    assert res.returncode == 1, "Breached thesis must exit with code 1"
    assert "THESIS BREACHED" in res.stdout
    assert "ACTION REQUIRED" in res.stdout


def test_cli_monitor_json_and_out_file():
    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / "sentry.json"
        res = _cli(
            [
                "monitor",
                "runs/eval_mock",
                "--doc",
                "Quarterly routine updates.",
                "--provider",
                "mock",
                "--json",
                "--out",
                str(out_file),
            ]
        )
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["ticker"] == "MSFT"
        assert not data["requires_rerun"]
        assert out_file.exists()
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        assert saved["ticker"] == "MSFT"


def test_evaluate_document_opencode_and_openrouter_mocked(monkeypatch):
    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    mock_resp = json.dumps({
        "materiality": "critical",
        "materiality_prob": 0.95,
        "action": "trigger_deep_rerun",
        "action_prob": 0.92,
        "contradiction_scores": {"B1": 0.95, "B2": 0.10, "B3": 0.05},
        "reasoning": "Cloud outage contradicts B1",
    })

    from src.agents.model import OpenCodeGoBackend, OpenRouterBackend

    monkeypatch.setattr(OpenCodeGoBackend, "generate", lambda self, prompt: mock_resp)
    monkeypatch.setattr(OpenRouterBackend, "generate", lambda self, prompt: mock_resp)

    # 1. Test OpenCode Go provider
    res_go = evaluate_document(
        run_dir=run_dir,
        doc_text="Azure outage disrupts service.",
        provider="opencode",
        model="deepseek-v4.1-flash",
    )
    assert res_go.requires_rerun
    assert "opencode" in res_go.provider
    assert res_go.contradiction_scores["B1"] == 0.95
    assert any(item.get("id") == "B1" for item in res_go.threatened_theses)

    # 2. Test OpenRouter provider
    res_or = evaluate_document(
        run_dir=run_dir,
        doc_text="Azure outage disrupts service.",
        provider="openrouter",
        model="google/gemini-2.0-flash-001",
    )
    assert res_or.requires_rerun
    assert "openrouter" in res_or.provider
    assert res_or.contradiction_scores["B1"] == 0.95


def test_cli_monitor_long_inline_text(capsys):
    from src.controller import build_parser

    parser = build_parser()
    long_doc = "A" * 350 + " routine benign quarterly dividend declaration."
    args = parser.parse_args(
        ["monitor", "runs/eval_mock", "--doc", long_doc, "--provider", "mock"]
    )
    args.func(args)
    captured = capsys.readouterr()
    assert "THESIS INTACT" in captured.out


@pytest.mark.live
def test_live_opencode_sentry_execution():
    from src.agents.model import OpenCodeGoBackend

    backend = OpenCodeGoBackend(model="deepseek-v4.1-flash")
    if not backend.api_key:
        pytest.skip("OpenCode Go API key not found in env or auth.json")

    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    res = evaluate_document(
        run_dir=run_dir,
        doc_text="Microsoft reported routine product update without issues.",
        provider="opencode",
        model="deepseek-v4.1-flash",
    )
    assert "opencode" in res.provider
    assert res.latency_ms > 0


@pytest.mark.live
def test_live_jev_execution():
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        pytest.skip("TYPESAFE_API_KEY environment variable not set.")

    run_dir = PROJECT_ROOT / "runs" / "eval_mock"
    res = evaluate_document(
        run_dir=run_dir,
        doc_text="Microsoft Azure revenue accelerated +35% YoY driven by enterprise AI demand.",
        provider="jev",
        api_key=api_key,
    )
    assert isinstance(res, SentryResult)
    assert res.provider == "jev"
    assert res.latency_ms > 0
    assert not res.requires_rerun
