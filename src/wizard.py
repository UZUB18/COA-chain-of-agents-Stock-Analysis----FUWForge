"""Interactive guided CLI wizard for FUWForge Stock Analysis.

Guides the user step-by-step through configuration, preflight diagnostics,
execution, forensic audit verification, and sealed research packet export.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import dotenv_values

from src.controller import (
    ALLOW_DEFAULTS_HELP,
    PROJECT_ROOT,
    build_parser,
    cmd_export_packet,
    cmd_run,
    cmd_verify_run,
)
from src.resources import get_default_runs_dir


def _check_diagnostics() -> Dict[str, Any]:
    """Inspect environment and local project configuration."""
    env_file = PROJECT_ROOT / ".env"
    local_env = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
    gemini_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or local_env.get("GEMINI_API_KEY") or local_env.get("GOOGLE_API_KEY") or "").strip()
    sec_user_agent = (os.environ.get("SEC_USER_AGENT") or local_env.get("SEC_USER_AGENT") or "").strip()

    return {
        "gemini_ready": bool(gemini_key),
        "gemini_key_len": len(gemini_key),
        "sec_ready": bool(sec_user_agent and "@" in sec_user_agent),
        "sec_user_agent": sec_user_agent,
        "runs_dir": get_default_runs_dir(PROJECT_ROOT),
    }


def _print_banner(diag: Dict[str, Any]) -> None:
    print("=" * 78)
    print("  FUWForge v2.1.0 — Guided Stock Analysis Assistant")
    print("=" * 78)
    print("Environment Diagnostics:")
    if diag["gemini_ready"]:
        print("  [✓] Gemini API Key:      Detected (Google AI Studio Live Mode available)")
    else:
        print("  [!] Gemini API Key:      Not detected (Mock simulation mode available)")
    if diag["sec_ready"]:
        print(f"  [✓] SEC User-Agent:      Configured ({diag['sec_user_agent']})")
    else:
        print("  [!] SEC User-Agent:      Not set (Automatic SEC intake requires contact email)")
    print("  [✓] Forensic Replay:     Deterministic calculation witness & audit online")
    print("=" * 78)


def _prompt_text(
    prompt: str,
    default: Optional[str] = None,
    validator: Optional[Callable[[str], bool]] = None,
    error_msg: Optional[str] = None,
) -> str:
    hint = f" [default: {default}]" if default else ""
    while True:
        try:
            resp = input(f"{prompt}{hint}: ").strip()
        except EOFError:
            return default or ""
        if not resp and default is not None:
            return default
        if not resp:
            print("  This field cannot be empty. Please enter a value.")
            continue
        if validator and not validator(resp):
            print(f"  {error_msg or 'Invalid value. Please try again.'}")
            continue
        return resp


def _prompt_choice(title: str, options: List[Tuple[str, str]], default_index: int = 1) -> str:
    print(f"\n{title}:")
    for i, (key, label) in enumerate(options, 1):
        def_tag = " (default)" if i == default_index else ""
        print(f"  [{i}] {label}{def_tag}")
    while True:
        try:
            choice = input(f"Select [1-{len(options)}, default: {default_index}]: ").strip()
        except EOFError:
            return options[default_index - 1][0]
        if not choice:
            return options[default_index - 1][0]
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        for key, _ in options:
            if choice.lower() == key.lower():
                return key
        print(f"  Invalid selection. Please choose a number between 1 and {len(options)}.")


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            resp = input(f"{prompt} {hint}: ").strip().lower()
        except EOFError:
            return default
        if not resp:
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print("  Please answer 'y' or 'n'.")


def build_wizard_args(answers: Dict[str, Any]) -> argparse.Namespace:
    """Build a complete Namespace compatible with cmd_run from guided answers."""
    ticker = answers["ticker"]
    company = answers.get("company") or ticker
    analysis_date = answers.get("analysis_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    provider = answers.get("provider", "mock")
    model = answers.get("model")
    depth = answers.get("depth", "Standard")
    strict_answer = answers.get("strict")
    strict = strict_answer if isinstance(strict_answer, bool) else provider != "mock"
    run_dir = answers.get("run_dir")
    source_mode = answers.get("source_mode", "direct")
    evidence_pack = answers.get("evidence_pack")

    tokens = ["run"]
    if run_dir:
        tokens.append(str(run_dir))
    tokens.extend(
        [
            "--ticker",
            str(ticker),
            "--company",
            str(company),
            "--analysis-date",
            str(analysis_date),
            "--provider",
            str(provider),
            "--depth",
            str(depth),
        ]
    )
    if model:
        tokens.extend(["--model", str(model)])
    if strict:
        tokens.append("--strict")
    elif provider != "mock":
        tokens.append("--allow-defaults")
    if answers.get("independent_bear", False):
        tokens.append("--independent-bear")
    if source_mode == "auto":
        tokens.append("--auto-sources")
    elif source_mode == "evidence":
        tokens.extend(["--evidence-pack", str(evidence_pack), "--no-web-search"])
    elif source_mode == "direct":
        tokens.append("--no-web-search")

    return build_parser().parse_args(tokens)


def run_wizard() -> None:
    """Main interactive wizard workflow."""
    diag = _check_diagnostics()
    _print_banner(diag)

    # 1. Target Ticker
    print("\n--- Step 1: Target Security ---")
    ticker = _prompt_text(
        "Enter Stock Ticker Symbol [e.g. AAPL, NVDA, AMD, MSFT]",
        validator=lambda s: bool(re.fullmatch(r"[A-Za-z0-9.-]{1,10}", s)),
        error_msg="Ticker must be 1-10 alphanumeric characters (e.g. NVDA, AAPL).",
    ).upper()

    # 2. Company Name
    company = _prompt_text(
        "Enter Company Name (optional)",
        default=ticker,
    )

    # 3. Analysis Date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    analysis_date = _prompt_text(
        "Analysis Date (YYYY-MM-DD)",
        default=today,
        validator=lambda s: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)),
        error_msg="Date must be formatted as YYYY-MM-DD (e.g. 2026-10-12).",
    )

    # 4. Provider Selection
    print("\n--- Step 2: Execution Engine ---")
    provider_options = [
        ("gemini", "gemini — Live Google AI Studio (Gemini 3.8 Flash / 2.5 Pro)"),
        ("mock", "mock   — Offline deterministic simulation (free, instant testing)"),
    ]
    default_prov_idx = 1 if diag["gemini_ready"] else 2
    provider = _prompt_choice("Select Execution Provider", provider_options, default_index=default_prov_idx)

    # 5. Model & Source Intake Selection
    model = None
    source_mode = "direct"
    evidence_pack = None

    if provider == "gemini":
        model_options = [
            ("gemini-3.8-flash", "gemini-3.8-flash (Recommended: fast, high-reasoning, low latency)"),
            ("gemini-2.5-pro", "gemini-2.5-pro (Deep multi-turn synthesis)"),
            ("custom", "Enter custom model name"),
        ]
        chosen_model = _prompt_choice("Select Gemini Model", model_options, default_index=1)
        if chosen_model == "custom":
            model = _prompt_text("Enter Gemini model ID [e.g. gemini-3.1-pro-preview]")
        else:
            model = chosen_model

        source_options = [
            ("auto", "Automatic SEC EDGAR filings + Web grounding (--auto-sources) [Recommended]"),
            ("direct", "Direct model reasoning without live SEC downloads (--no-web-search)"),
            ("evidence", "Use pre-existing Evidence Pack file (.json)"),
        ]
        default_src_idx = 1 if diag["sec_ready"] else 2
        source_mode = _prompt_choice("Select Evidence & Source Intake Mode", source_options, default_index=default_src_idx)

        if source_mode == "auto" and not diag["sec_ready"]:
            print("\n  [!] SEC fair-access requires a contact header (e.g. 'YourName yourname@domain.com').")
            contact = _prompt_text("Enter your SEC Contact Email/Organization", validator=lambda s: "@" in s, error_msg="Contact must include a valid email address.")
            os.environ["SEC_USER_AGENT"] = contact
            diag["sec_ready"] = True
        elif source_mode == "evidence":
            evidence_pack = _prompt_text("Enter path to evidence pack JSON", validator=lambda s: Path(s).is_file(), error_msg="File does not exist.")

    # 6. Depth & Governance
    print("\n--- Step 3: Depth & Forensic Rigor ---")
    depth_options = [
        ("Standard", "Standard (comprehensive 9-agent chain with all core forensic checks)"),
        ("Deep", "Deep (extended multi-year financial forensic lookback)"),
        ("Quick", "Quick (accelerated screening)"),
    ]
    depth = _prompt_choice("Select Analysis Depth", depth_options, default_index=1)

    strict = _prompt_yes_no(
        "Enable Strict Contract Mode? (fails closed if any model artifact is missing)",
        default=provider != "mock",
    )
    if provider != "mock" and not strict:
        print(f"\n  [!] {ALLOW_DEFAULTS_HELP}")
    if provider == "gemini" and strict and source_mode == "direct":
        print(
            "\n  [!] Strict live analysis requires an Evidence Pack or Automatic "
            "source intake; direct reasoning alone does not satisfy the strict evidence contract."
        )
    independent_bear = _prompt_yes_no("Run Independent Adversarial Bear Case agent? (runs unassisted bearish analysis)", default=False)

    # 7. Output Directory
    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_run_dir = f"runs/{ticker.lower()}_{timestamp_slug}"
    print("\n--- Step 4: Storage & Output ---")
    run_dir = _prompt_text("Run Output Directory", default=default_run_dir)

    # Summary Review
    print("\n" + "=" * 78)
    print("  Run Configuration Review")
    print("=" * 78)
    print(f"  Ticker:             {ticker}")
    print(f"  Company Name:       {company}")
    print(f"  Analysis Date:      {analysis_date}")
    print(f"  Engine / Model:     {provider} ({model or 'default'})")
    print(f"  Source Intake:      {source_mode.upper()} {'(--auto-sources)' if source_mode == 'auto' else '(--no-web-search)' if source_mode == 'direct' else evidence_pack}")
    print(f"  Analysis Depth:     {depth}")
    print(f"  Governance:         Strict: {'Yes' if strict else 'No'} | Independent Bear: {'Yes' if independent_bear else 'No'}")
    print(f"  Output Directory:   {run_dir}")
    print("=" * 78)

    confirm = _prompt_yes_no("Ready to launch analysis?", default=True)
    if not confirm:
        print("\nAnalysis aborted by user.")
        return

    # Build Arguments & Execute
    answers = {
        "ticker": ticker,
        "company": company,
        "analysis_date": analysis_date,
        "provider": provider,
        "model": model,
        "depth": depth,
        "strict": strict,
        "independent_bear": independent_bear,
        "run_dir": run_dir,
        "source_mode": source_mode,
        "evidence_pack": evidence_pack,
    }
    args = build_wizard_args(answers)

    print("\n" + "=" * 78)
    print(f"  Executing FUWForge Chain of Agents Analysis for {ticker}...")
    print("=" * 78 + "\n")

    try:
        cmd_run(args)
    except Exception as exc:
        print(f"\n[ERROR] Analysis run failed: {exc}", file=sys.stderr)
        return

    # Run Automatic Post-Run Forensic Audit
    print("\n" + "=" * 78)
    print("  Running Forensic Audit & Cryptographic Seal Verification...")
    print("=" * 78 + "\n")
    audit_args = build_parser().parse_args(["verify-run", str(run_dir)])
    try:
        cmd_verify_run(audit_args)
    except Exception as exc:
        print(f"[!] Audit encountered warning or error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    # Post-Run Interactive Next Actions
    resolved_dir = Path(run_dir).resolve()
    report_candidates = list(resolved_dir.glob(f"{ticker}_*_report.md")) or list(resolved_dir.glob("*_report.md"))
    report_file = report_candidates[0] if report_candidates else None

    print("\n" + "=" * 78)
    print("  Analysis Completed Successfully")
    print("=" * 78)
    if report_file:
        print(f"  Final Report:       {report_file}")
    print(f"  Artifacts JSON:     {resolved_dir / f'{ticker}_{analysis_date}_artifacts.json'}")
    print(f"  Run Summary:        {resolved_dir / 'run_summary.json'}")
    print("=" * 78)

    next_options = [
        ("packet", "Export a cryptographically sealed Research Packet (.json)"),
        ("view", "Preview the Executive Summary & Final Report in terminal"),
        ("exit", "Finish and exit"),
    ]
    action = _prompt_choice("Next Actions", next_options, default_index=1)

    if action == "packet":
        packet_path = resolved_dir / f"{ticker.lower()}_packet.json"
        pkt_args = build_parser().parse_args(
            [
                "export-packet",
                str(resolved_dir),
                "--output",
                str(packet_path),
            ]
        )
        try:
            cmd_export_packet(pkt_args)
            print(f"\n[✓] Research packet exported: {packet_path}")
        except Exception as exc:
            print(f"\n[!] Packet export failed: {exc}")
    elif action == "view" and report_file and report_file.is_file():
        print("\n" + "-" * 78)
        print(f"  Executive Report Preview: {report_file.name}")
        print("-" * 78 + "\n")
        try:
            content = report_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            # Show first 50 lines cleanly
            preview = "\n".join(lines[:50])
            print(preview)
            if len(lines) > 50:
                print(f"\n... [{len(lines) - 50} more lines in {report_file}]")
        except Exception as exc:
            print(f"Could not read report file: {exc}")

    print(f"\nDone! All outputs are saved in {resolved_dir}.\n")


if __name__ == "__main__":
    try:
        run_wizard()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(0)
