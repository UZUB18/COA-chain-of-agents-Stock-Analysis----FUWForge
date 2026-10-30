"""Unit tests for the FUWForge guided CLI wizard."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from src import wizard as wizard_module
from src.controller import build_parser, cmd_run
from src.run_audit import AuditReport
from src.wizard import (
    _check_diagnostics,
    _prompt_choice,
    _prompt_text,
    _prompt_yes_no,
    build_wizard_args,
    run_wizard,
)

_OMITTED = object()


def _diagnostics(tmp_path, *, gemini_ready=False, sec_ready=False):
    return {
        "gemini_ready": gemini_ready,
        "gemini_key_len": 11 if gemini_ready else 0,
        "sec_ready": sec_ready,
        "sec_user_agent": "Test admin@test.com" if sec_ready else "",
        "runs_dir": tmp_path,
    }


def test_check_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-12345")
    monkeypatch.setenv("SEC_USER_AGENT", "TestOrg admin@test.com")
    diag = _check_diagnostics()
    assert diag["gemini_ready"] is True
    assert diag["sec_ready"] is True
    assert diag["sec_user_agent"] == "TestOrg admin@test.com"


def test_prompt_text_defaults_and_validation(monkeypatch):
    inputs = iter(["", "bad", "NVDA"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    # 1. Empty input falls back to default
    res = _prompt_text("Prompt", default="AAPL")
    assert res == "AAPL"

    # 2. Invalid input reprompts until validator passes
    res_val = _prompt_text("Prompt", validator=lambda s: s == "NVDA", error_msg="Error")
    assert res_val == "NVDA"


def test_prompt_choice(monkeypatch):
    options = [("a", "Option A"), ("b", "Option B")]

    # Default on enter
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_choice("Choose", options, default_index=2) == "b"

    # Select by numeric index
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert _prompt_choice("Choose", options) == "a"

    # Select by key name
    monkeypatch.setattr("builtins.input", lambda _: "b")
    assert _prompt_choice("Choose", options) == "b"


def test_prompt_yes_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes_no("Confirm?", default=True) is True
    assert _prompt_yes_no("Confirm?", default=False) is False

    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert _prompt_yes_no("Confirm?", default=False) is True

    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert _prompt_yes_no("Confirm?", default=True) is False


def test_build_wizard_args():
    answers = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "analysis_date": "2026-10-12",
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "depth": "Standard",
        "strict": True,
        "independent_bear": True,
        "run_dir": "runs/test_custom",
        "source_mode": "auto",
        "evidence_pack": None,
    }
    args = build_wizard_args(answers)
    assert isinstance(args, argparse.Namespace)
    assert args.ticker == "AAPL"
    assert args.company == "Apple Inc."
    assert args.provider == "gemini"
    assert args.model == "gemini-3.8-flash"
    assert args.strict is True
    assert args.allow_defaults is False
    assert args.independent_bear is True
    assert args.auto_sources is True
    assert args.web_search is None
    assert args.command == "run"
    assert args.func is cmd_run
    assert args.recovery_choice is None
    assert args.web_max_sources == 4
    assert args.no_url_context is False


@pytest.mark.parametrize(
    "provider,strict,expected_strict,expected_allow_defaults",
    [
        pytest.param("gemini", _OMITTED, True, False, id="gemini-omitted"),
        pytest.param("mock", _OMITTED, False, False, id="mock-omitted"),
        pytest.param("gemini", None, True, False, id="gemini-none"),
        pytest.param("mock", None, False, False, id="mock-none"),
        pytest.param("gemini", True, True, False, id="gemini-true"),
        pytest.param("gemini", False, False, True, id="gemini-false"),
        pytest.param("mock", True, True, False, id="mock-true"),
        pytest.param("mock", False, False, False, id="mock-false"),
        pytest.param("gemini", 0, True, False, id="gemini-other-falsy"),
        pytest.param("mock", 0, False, False, id="mock-other-falsy"),
    ],
)
def test_build_wizard_args_strict_matrix(
    provider, strict, expected_strict, expected_allow_defaults
):
    answers = {
        "ticker": "AAPL",
        "provider": provider,
        "source_mode": "auto",
    }
    if strict is not _OMITTED:
        answers["strict"] = strict
    args = build_wizard_args(answers)

    assert args.command == "run"
    assert args.func is cmd_run
    assert args.strict is expected_strict
    assert args.allow_defaults is expected_allow_defaults


def test_build_wizard_args_source_modes_preserve_paid_intake_flags(tmp_path):
    common = {
        "ticker": "AAPL",
        "provider": "gemini",
        "strict": True,
    }

    automatic = build_wizard_args({**common, "source_mode": "auto"})
    assert automatic.auto_sources is True
    assert automatic.web_search is None

    direct = build_wizard_args({**common, "source_mode": "direct"})
    assert direct.auto_sources is False
    assert direct.web_search is False

    evidence_path = tmp_path / "evidence.json"
    evidence = build_wizard_args(
        {
            **common,
            "source_mode": "evidence",
            "evidence_pack": str(evidence_path),
        }
    )
    assert evidence.auto_sources is False
    assert evidence.web_search is False
    assert evidence.evidence_pack == str(evidence_path)


def test_wizard_parser_subcommands():
    parser = build_parser()
    for cmd in ("wizard", "guided", "interactive"):
        args = parser.parse_args([cmd])
        assert args.command == cmd
        assert callable(args.func)


def test_run_wizard_prints_real_audit_result(monkeypatch, capsys, tmp_path):
    run_dir = str(tmp_path / "audit_run")
    inputs = iter(
        [
            "MSFT",
            "",
            "",
            "2",
            "1",
            "n",
            "n",
            run_dir,
            "y",
            "3",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(
        wizard_module,
        "_check_diagnostics",
        lambda: _diagnostics(tmp_path),
    )
    monkeypatch.setattr(wizard_module, "cmd_run", lambda _args: None)

    report = AuditReport(
        levels={
            "envelope_integrity": "verified",
            "source_reconstruction": "not_available",
            "graph_and_contracts": "verified",
            "calculation_replay": "verified",
            "final_artifacts": "verified",
            "completeness": "complete",
        },
        complete=True,
        witnesses_replayed=1,
        inputs_verified=1,
    )
    monkeypatch.setattr(
        "src.run_audit.semantic_audit",
        lambda _run_dir, require_source_backed: report,
    )

    captured = {}
    real_verify_run = wizard_module.cmd_verify_run

    def capture_verify_run(args):
        captured["args"] = args
        real_verify_run(args)

    monkeypatch.setattr(wizard_module, "cmd_verify_run", capture_verify_run)

    run_wizard()

    output = capsys.readouterr().out
    assert "Overall state: verified" in output
    assert captured["args"].command == "verify-run"
    assert captured["args"].strict is False
    assert captured["args"].require_source_backed is False
    assert captured["args"].integrity_only is False
    assert captured["args"].json is False


def test_run_wizard_unexpected_audit_error_exits_without_success(
    monkeypatch, capsys, tmp_path
):
    run_dir = str(tmp_path / "failed_audit_run")
    inputs = iter(["MSFT", "", "", "2", "1", "n", "n", run_dir, "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(
        wizard_module,
        "_check_diagnostics",
        lambda: _diagnostics(tmp_path),
    )
    monkeypatch.setattr(wizard_module, "cmd_run", lambda _args: None)

    def fail_audit(_args):
        raise RuntimeError("audit crashed")

    monkeypatch.setattr(wizard_module, "cmd_verify_run", fail_audit)

    with pytest.raises(SystemExit) as exc:
        run_wizard()

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert "Audit encountered warning or error: audit crashed" in captured.err
    assert "Analysis Completed Successfully" not in captured.out


def test_run_wizard_real_provider_defaults_to_strict(monkeypatch, capsys, tmp_path):
    run_dir = str(tmp_path / "strict_run")
    inputs = iter(["AAPL", "", "", "", "", "", "", "", "", run_dir, "y", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(
        wizard_module,
        "_check_diagnostics",
        lambda: _diagnostics(tmp_path, gemini_ready=True, sec_ready=True),
    )
    parsed = []
    monkeypatch.setattr(wizard_module, "cmd_run", lambda args: parsed.append(args))
    monkeypatch.setattr(wizard_module, "cmd_verify_run", lambda _args: None)

    run_wizard()

    assert len(parsed) == 1
    assert parsed[0].strict is True
    assert parsed[0].allow_defaults is False
    assert parsed[0].auto_sources is True
    assert parsed[0].web_search is None
    capsys.readouterr()


def test_run_wizard_lenient_opt_out_prints_shared_warning(monkeypatch, capsys, tmp_path):
    run_dir = str(tmp_path / "lenient_run")
    inputs = iter(["AAPL", "", "", "", "", "", "", "n", "", run_dir, "y", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(
        wizard_module,
        "_check_diagnostics",
        lambda: _diagnostics(tmp_path, gemini_ready=True, sec_ready=True),
    )
    parsed = []
    monkeypatch.setattr(wizard_module, "cmd_run", lambda args: parsed.append(args))
    monkeypatch.setattr(wizard_module, "cmd_verify_run", lambda _args: None)

    run_wizard()

    output = capsys.readouterr().out
    assert (
        "Legacy diagnostic mode: permit flagged placeholders; not verified research "
        "(--strict takes precedence)."
    ) in output
    assert parsed[0].strict is False
    assert parsed[0].allow_defaults is True


def test_run_wizard_strict_direct_explains_required_source_modes(monkeypatch, capsys, tmp_path):
    run_dir = str(tmp_path / "strict_direct_run")
    inputs = iter(["AAPL", "", "", "", "", "", "", "", "", run_dir, "y", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(
        wizard_module,
        "_check_diagnostics",
        lambda: _diagnostics(tmp_path, gemini_ready=True),
    )
    parsed = []
    monkeypatch.setattr(wizard_module, "cmd_run", lambda args: parsed.append(args))
    monkeypatch.setattr(wizard_module, "cmd_verify_run", lambda _args: None)

    run_wizard()

    output = capsys.readouterr().out
    assert (
        "Strict live analysis requires an Evidence Pack or Automatic source intake"
        in output
    )
    assert parsed[0].strict is True
    assert parsed[0].web_search is False
    assert parsed[0].auto_sources is False


def test_run_wizard_packet_action_uses_json_parser(monkeypatch, capsys, tmp_path):
    run_dir = str(tmp_path / "packet_run")
    inputs = iter(["MSFT", "", "", "2", "1", "n", "n", run_dir, "y", "1"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr(
        wizard_module,
        "_check_diagnostics",
        lambda: _diagnostics(tmp_path),
    )
    monkeypatch.setattr(wizard_module, "cmd_run", lambda _args: None)
    monkeypatch.setattr(wizard_module, "cmd_verify_run", lambda _args: None)
    captured = []
    monkeypatch.setattr(
        wizard_module, "cmd_export_packet", lambda args: captured.append(args)
    )

    run_wizard()

    output = capsys.readouterr().out
    assert "Research Packet (.json)" in output
    assert len(captured) == 1
    assert captured[0].command == "export-packet"
    assert captured[0].output.endswith(".json")
    assert captured[0].packet_version == "research_packet_v1"
    assert captured[0].strict is False
    assert captured[0].func.__name__ == "cmd_export_packet"


def test_run_wizard_end_to_end_mock(monkeypatch, capsys, tmp_path):
    run_dir = str(tmp_path / "wizard_test_run")
    # Simulated interactive user responses:
    # 1. Ticker: "MSFT"
    # 2. Company: [Enter -> default "MSFT"]
    # 3. Date: [Enter -> default today]
    # 4. Provider: "2" (mock)
    # 5. Depth: "1" (Standard)
    # 6. Strict: "n" (False)
    # 7. Independent Bear: "n" (False)
    # 8. Run Dir: run_dir
    # 9. Confirm launch: "y"
    # 10. Post-run action: "3" (exit)
    simulated_inputs = iter([
        "MSFT",
        "",
        "",
        "2",
        "1",
        "n",
        "n",
        run_dir,
        "y",
        "3",
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(simulated_inputs))

    run_wizard()

    # Verify that the run directory was populated and completed
    assert Path(run_dir).is_dir()
    assert (Path(run_dir) / "run_summary.json").is_file()
    assert (Path(run_dir) / "run_manifest.json").is_file()
    output = capsys.readouterr().out
    assert "Overall state: verified" in output
    assert "Audit passed." in output
