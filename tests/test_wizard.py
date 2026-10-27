"""Unit tests for the FUWForge guided CLI wizard."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from src.controller import build_parser
from src.wizard import (
    _check_diagnostics,
    _prompt_choice,
    _prompt_text,
    _prompt_yes_no,
    build_wizard_args,
    run_wizard,
)


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
    assert args.independent_bear is True
    assert args.auto_sources is True
    assert args.web_search is True


def test_wizard_parser_subcommands():
    parser = build_parser()
    for cmd in ("wizard", "guided", "interactive"):
        args = parser.parse_args([cmd])
        assert args.command == cmd
        assert callable(args.func)


def test_run_wizard_end_to_end_mock(monkeypatch, tmp_path):
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
