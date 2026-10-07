# CLI utilities for controller mode validation, assembly, and run auditing.

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import load_mapping
from src.contracts import COMPARISON_FIELD_KEYS, SCORECARD_CATEGORIES
from src.envelope import Envelope
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.run_audit import verify_run
from src.utils.checksum import envelope_content_checksum
from src.utils.finance import generate_comparison_fields, resolve_weights
from src.validators import validate_checksum


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _today_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


# ------------------------------------------------------------------ inputs


def _load_input(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    input_path = Path(path)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    data = load_mapping(input_path)
    if not isinstance(data, dict):
        raise SystemExit(f"Input file must contain a mapping: {input_path}")
    if isinstance(data.get("inputs"), dict):
        data = data["inputs"]
    return data


def _resolve_weights_arg(raw: Any) -> Any:
    """Accept 'default', a JSON object string, a mapping, or a YAML/JSON file path."""
    if raw is None:
        return "default"
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() == "default":
            return "default"
        if text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"--weights is not valid JSON: {exc}") from exc
        else:
            path = Path(text)
            if not path.exists():
                raise SystemExit(
                    "--weights must be 'default', a JSON object, or a path to a YAML/JSON file."
                )
            raw = load_mapping(path)
    if not isinstance(raw, dict):
        raise SystemExit("Weights must be a mapping of scorecard category -> weight.")
    resolved, errors = resolve_weights(raw)
    if errors:
        raise SystemExit("Invalid weights:\n- " + "\n- ".join(errors))
    return resolved


def _build_params(args: argparse.Namespace) -> Tuple[OrchestratorParams, bool]:
    input_path = getattr(args, "input", None)
    run_dir = Path(args.run_dir) if getattr(args, "run_dir", None) else None
    if not input_path and run_dir and (run_dir / "input.yaml").exists():
        input_path = str(run_dir / "input.yaml")
    data = _load_input(input_path)
    if not data and run_dir and run_dir.is_dir():
        for ef in sorted(run_dir.glob("*_envelope.json")):
            try:
                env_dict = json.loads(ef.read_text(encoding="utf-8"))
                if isinstance(env_dict.get("params"), dict) and env_dict["params"].get("ticker"):
                    data = dict(env_dict["params"])
                    break
            except Exception:
                pass

    ticker = args.ticker if args.ticker is not None else data.get("ticker")
    if not ticker:
        raise SystemExit("--ticker is required (or provide `ticker` via --input).")
    company = (
        args.company if args.company is not None else (data.get("company") or ticker)
    )
    analysis_date = (
        args.analysis_date
        if args.analysis_date is not None
        else (data.get("analysis_date") or _today_date())
    )
    depth = args.depth if args.depth is not None else (data.get("depth") or "Standard")
    focus = args.focus if args.focus is not None else (data.get("focus") or [])
    weights_raw = (
        args.weights if args.weights is not None else data.get("weights", "default")
    )

    strict = bool(getattr(args, "strict", False))
    if strict:
        os.environ["FUWFORGE_STRICT"] = "1"

    params = OrchestratorParams(
        company=company,
        ticker=str(ticker),
        analysis_date=str(analysis_date),
        depth=str(depth),
        focus=list(focus),
        weights=_resolve_weights_arg(weights_raw),
    )
    return params, strict


# ------------------------------------------------------------------ commands


def cmd_validate(args: argparse.Namespace) -> None:
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT)
    data = json.loads(Path(args.envelope).read_text(encoding="utf-8"))
    result = orchestrator.validator.validate(data)
    if not result.valid:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(result.errors))

    if args.expected_checksum:
        envelope = Envelope.from_dict(data)
        checksum_result = validate_checksum(envelope, args.expected_checksum)
        if not checksum_result.valid:
            raise SystemExit(f"Checksum validation failed: {checksum_result.errors[0]}")

    print("Envelope validation succeeded.")


def cmd_assemble(args: argparse.Namespace) -> None:
    params, strict = _build_params(args)
    run_id = args.run_id or Path(args.run_dir).name or f"run_{params.ticker.lower()}"
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, strict=strict)
    summary = orchestrator.run(
        run_id=run_id,
        params=params,
        mode="controller",
        output_root=Path(args.run_dir).resolve(),
    )
    print("Run assembled successfully.")
    print(json.dumps(summary, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    params, strict = _build_params(args)
    # Real research is fail-closed by default. Legacy placeholders require an
    # explicit diagnostic opt-in, never merely forgetting --strict.
    if args.provider != "mock" and not getattr(args, "allow_defaults", False):
        strict = True
    run_id = (
        args.run_id
        or f"run_{params.ticker.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    evidence_pack = Path(args.evidence_pack) if args.evidence_pack else None
    web_setting = getattr(args, "web_search", None)
    web_enabled = args.provider == "gemini" if web_setting is None else web_setting
    if web_enabled and args.provider != "gemini":
        raise ValueError("--web-search requires --provider gemini; other providers never incur Gemini research charges.")
    web_max_sources = getattr(args, "web_max_sources", 4)
    if not 1 <= web_max_sources <= 6:
        raise ValueError("--web-max-sources must be between 1 and 6.")
    if web_enabled and not (evidence_pack or getattr(args, "auto_sources", False)):
        raise ValueError("Gemini web search requires --auto-sources or --evidence-pack for financial inputs; use --no-web-search to disable it.")
    source_root = (Path(args.run_dir).resolve() if args.run_dir else PROJECT_ROOT / "runs" / run_id)
    if getattr(args, "auto_sources", False):
        from src.public_data import collect_public_sources

        evidence_pack = collect_public_sources(params.ticker, params.analysis_date, params.depth, source_root / "retrieved_inputs", getattr(args, "cik", None))
    if web_enabled:
        from src.agents.model import GeminiApiBackend
        from src.web_research import collect_web_sources

        evidence_pack = collect_web_sources(
            ticker=params.ticker, company=params.company, analysis_date=params.analysis_date,
            evidence_path=evidence_pack, output_dir=source_root / "web_research",
            backend=GeminiApiBackend(model=args.model or os.environ.get("FUWFORGE_MODEL")), max_sources=web_max_sources,
            url_context=not getattr(args, "no_url_context", False),
        )
        print("Gemini web intake completed (one shared request; no Deep Research).")
    orchestrator = FUWForgeOrchestrator(
        PROJECT_ROOT,
        provider=args.provider,
        model=args.model,
        randomize=args.random_data,
        strict=strict,
        evidence_pack=evidence_pack,
    )
    output_root = Path(args.run_dir).resolve() if args.run_dir else None
    summary = orchestrator.run(
        run_id=run_id,
        params=params,
        mode="automated",
        output_root=output_root,
    )
    print("Automated run completed successfully.")
    print(json.dumps(summary, indent=2))


def cmd_verify_run(args: argparse.Namespace) -> None:
    result = verify_run(Path(args.run_dir), strict=args.strict)
    for warning in result.warnings:
        print(f"[warn] {warning}")
    if not result.valid:
        print("Run verification FAILED:")
        for error in result.errors:
            print(f"- {error}")
        raise SystemExit(1)
    print(f"Run verification succeeded ({len(result.warnings)} warning(s)).")


def cmd_seal(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    files = sorted(run_dir.glob("*_envelope.json"))
    if not files:
        raise SystemExit(f"No *_envelope.json files found in {run_dir}.")
    sealed = 0
    force = bool(getattr(args, "force", False))
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        actual = envelope_content_checksum(data)
        recorded = data.get("content_checksum")
        if recorded and recorded != actual and not force:
            raise SystemExit(
                f"{path.name}: content checksum mismatch; refusing to re-seal a modified envelope (use --force to override)."
            )
        if recorded == actual:
            continue
        data["content_checksum"] = actual
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        sealed += 1
    print(f"Sealed {sealed} envelope(s) in {run_dir}.")


def _load_a7(
    run_dir: Path,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
    envelope: Optional[Dict[str, Any]] = None
    artifacts_files = sorted(run_dir.glob("*_artifacts.json"))
    if artifacts_files:
        payload = json.loads(artifacts_files[0].read_text(encoding="utf-8"))
        envelope = payload.get("A7")
    elif (run_dir / "a7_envelope.json").exists():
        envelope = json.loads((run_dir / "a7_envelope.json").read_text(encoding="utf-8"))
    else:
        print(f"[warn] {run_dir}: no *_artifacts.json or a7_envelope.json found; skipping.")
        return None

    if not isinstance(envelope, dict):
        print(f"[warn] {run_dir}: artifacts bundle has no A7 envelope; skipping.")
        return None
    artifacts = envelope.get("artifacts") or {}
    fields = artifacts.get("comparison_fields")
    scorecard = (
        artifacts.get("Scorecard")
        if isinstance(artifacts.get("Scorecard"), dict)
        else {}
    )
    if not isinstance(fields, dict):
        if scorecard:
            overall = scorecard.get("Overall") or {}
            q_score = overall.get("score", 7.0)
            v_score = (scorecard.get("Valuation") or {}).get("score", 7.0)
            m_score = (scorecard.get("Moat") or {}).get("score", 7.0)
            fields = generate_comparison_fields(
                quality_score_10=float(q_score),
                valuation_score_10=float(v_score),
                expected_5y_irr_base_pct=10.0,
                downside_case_5y_irr_pct=-5.0,
                moat_score_10=float(m_score),
            )
        else:
            print(f"[warn] {run_dir}: A7 has no comparison_fields; skipping.")
            return None
    return envelope, fields, scorecard


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def cmd_screen(args: argparse.Namespace) -> None:
    run_paths: List[Path] = []
    for run in args.runs:
        p = Path(run)
        if p.is_dir():
            if list(p.glob("*_artifacts.json")) or (p / "a7_envelope.json").exists():
                run_paths.append(p)
            else:
                subdirs = [
                    sub
                    for sub in sorted(p.iterdir())
                    if sub.is_dir()
                    and (list(sub.glob("*_artifacts.json")) or (sub / "a7_envelope.json").exists())
                ]
                if subdirs:
                    run_paths.extend(subdirs)
                else:
                    run_paths.append(p)
        else:
            run_paths.append(p)

    rows: List[Dict[str, Any]] = []
    for run_path in run_paths:
        loaded = _load_a7(run_path)
        if not loaded:
            continue
        envelope, fields, scorecard = loaded
        ticker = (envelope.get("params") or {}).get("ticker") or run_path.name
        row: Dict[str, Any] = {"ticker": ticker}
        row.update({key: fields.get(key) for key in COMPARISON_FIELD_KEYS})
        row["rating_band"] = (scorecard.get("Overall") or {}).get("rating_band")
        rows.append(row)

    if not rows:
        raise SystemExit("No screenable runs found.")

    sort_key = args.sort
    rows.sort(key=lambda r: (r.get(sort_key) is None, -(r.get(sort_key) or 0.0)))

    columns = [
        "ticker",
        "quality_score_10",
        "valuation_score_10",
        "moat_score_10",
        "expected_5y_irr_base_pct",
        "downside_case_5y_irr_pct",
        "fcf_yield_ttm_pct",
        "roic_ttm_pct",
        "net_debt_to_ebitda",
        "fragility_score_10",
        "evidence_quality_score_10",
        "data_completeness_score_10",
        "rating_band",
    ]

    if args.format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_fmt(row.get(column)) for column in columns])
        output = buffer.getvalue()
    else:
        header = "| " + " | ".join(columns) + " |"
        divider = "| " + " | ".join("---" for _ in columns) + " |"
        lines = [header, divider]
        for row in rows:
            lines.append(
                "| " + " | ".join(_fmt(row.get(column)) for column in columns) + " |"
            )
        output = "\n".join(lines) + "\n"

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Wrote {len(rows)} row(s) to {args.out}.")
    else:
        print(output, end="")


def cmd_diff(args: argparse.Namespace) -> None:
    loaded_a = _load_a7(Path(args.run_a))
    loaded_b = _load_a7(Path(args.run_b))
    if not loaded_a or not loaded_b:
        raise SystemExit("Both runs must contain A7 artifacts with comparison_fields.")

    env_a, fields_a, score_a = loaded_a
    env_b, fields_b, score_b = loaded_b
    label_a = (
        f"{env_a.get('params', {}).get('ticker', Path(args.run_a).name)} ({args.run_a})"
    )
    label_b = (
        f"{env_b.get('params', {}).get('ticker', Path(args.run_b).name)} ({args.run_b})"
    )

    def delta(x: Any, y: Any) -> str:
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return f"{y - x:+g}"
        return "—"

    print(f"### Scorecard — A: {label_a} · B: {label_b}\n")
    print(f"| Category | A | B | Δ |")
    print("| --- | --- | --- | --- |")
    categories = list(SCORECARD_CATEGORIES) + ["Overall"]
    for category in categories:
        cell_a = score_a.get(category)
        cell_b = score_b.get(category)
        val_a = cell_a.get("score") if isinstance(cell_a, dict) else None
        val_b = cell_b.get("score") if isinstance(cell_b, dict) else None
        print(f"| {category} | {_fmt(val_a)} | {_fmt(val_b)} | {delta(val_a, val_b)} |")

    print(f"\n### Comparison fields\n")
    print("| Metric | A | B | Δ |")
    print("| --- | --- | --- | --- |")
    for key in COMPARISON_FIELD_KEYS:
        val_a = fields_a.get(key)
        val_b = fields_b.get(key)
        print(f"| {key} | {_fmt(val_a)} | {_fmt(val_b)} | {delta(val_a, val_b)} |")

    prov_a = sorted(Path(args.run_a).glob("*_provenance.log"))
    prov_b = sorted(Path(args.run_b).glob("*_provenance.log"))
    if prov_a and prov_b:
        lines_a = [
            line.strip()
            for line in prov_a[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        lines_b = [
            line.strip()
            for line in prov_b[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        def _parse_entry(line: str) -> str:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                return f"[{parts[1]}] {' | '.join(parts[2:-1])}"
            return line

        set_a = {_parse_entry(l) for l in lines_a}
        set_b = {_parse_entry(l) for l in lines_b}
        diff_a = sorted(set_a - set_b)
        diff_b = sorted(set_b - set_a)
        if diff_a or diff_b:
            print(
                f"\n### Provenance differences ({len(lines_a)} vs {len(lines_b)} entries)\n"
            )
            if diff_a:
                print(f"**Only in A ({len(diff_a)}):**")
                for item in diff_a[:8]:
                    print(f"- {item}")
                if len(diff_a) > 8:
                    print(f"- ... and {len(diff_a) - 8} more")
            if diff_b:
                print(f"\n**Only in B ({len(diff_b)}):**")
                for item in diff_b[:8]:
                    print(f"- {item}")
                if len(diff_b) > 8:
                    print(f"- ... and {len(diff_b) - 8} more")


def cmd_monitor(args: argparse.Namespace) -> None:
    from src.sentry import evaluate_document, format_sentry_report

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        raise SystemExit(f"Run directory not found: {run_dir}")

    doc_arg = args.doc
    if not doc_arg:
        raise SystemExit("--doc is required (provide a file path or text string).")

    doc_text = doc_arg
    if len(doc_arg) < 260:
        try:
            doc_path = Path(doc_arg)
            if doc_path.exists() and doc_path.is_file():
                doc_text = doc_path.read_text(encoding="utf-8")
        except OSError:
            pass

    result = evaluate_document(
        run_dir=run_dir,
        doc_text=doc_text,
        provider=args.provider,
        api_key=args.api_key,
        threshold=args.threshold,
        model=getattr(args, "model", None),
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_sentry_report(result))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    if result.requires_rerun:
        raise SystemExit(1)


def cmd_collect_sources(args: argparse.Namespace) -> None:
    from src.public_data import collect_public_sources

    path = collect_public_sources(args.ticker, args.analysis_date, args.depth, Path(args.out), args.cik)
    print(f"Collected public research evidence: {path}")


def cmd_fetch_sources(args: argparse.Namespace) -> None:
    from src.evidence import fetch_evidence

    path = fetch_evidence(Path(args.manifest), Path(args.out))
    print(f"Retrieved evidence pack: {path}")


# ------------------------------------------------------------------ parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input", default=None, help="YAML/JSON input file with run parameters."
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Unique run identifier (defaults to directory name or auto-ID).",
    )
    parser.add_argument(
        "--company", default=None, help="Company name (defaults to ticker if omitted)."
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Ticker symbol (required unless provided via --input).",
    )
    parser.add_argument(
        "--analysis-date",
        default=None,
        help="Analysis date YYYY-MM-DD (defaults to today).",
    )
    parser.add_argument(
        "--depth",
        default=None,
        help="Depth mode (Quick|Standard|Deep, default: Standard).",
    )
    parser.add_argument("--focus", nargs="*", default=None, help="Optional focus tags.")
    parser.add_argument(
        "--weights",
        default=None,
        help="'default', a JSON object, or a path to a YAML/JSON weights file.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on injected defaults and other soft contract warnings.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FUWForge (Fired Up Wealth) controller utilities."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate an agent envelope."
    )
    validate_parser.add_argument("envelope", help="Path to the JSON envelope file.")
    validate_parser.add_argument("--expected-checksum", help="Checksum emitted by A1.")
    validate_parser.set_defaults(func=cmd_validate)

    assemble_parser = subparsers.add_parser(
        "assemble", help="Assemble a run from controller mode outputs."
    )
    assemble_parser.add_argument(
        "run_dir", help="Directory containing a*_envelope.json files."
    )
    _add_run_arguments(assemble_parser)
    assemble_parser.set_defaults(func=cmd_assemble)

    run_parser = subparsers.add_parser(
        "run", help="Execute an automated end-to-end multi-agent run."
    )
    run_parser.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="Directory to save run outputs (defaults to runs/<run_id>).",
    )
    _add_run_arguments(run_parser)
    run_parser.add_argument(
        "--provider",
        default="mock",
        choices=[
            "mock",
            "agy",
            "opencode",
            "opencode-go",
            "opencode_go",
            "openrouter",
            "openai",
            "gemini",
        ],
        help="Agent execution provider (default: mock).",
    )
    run_parser.add_argument(
        "--model", default=None, help="Model name override for model providers."
    )
    run_parser.add_argument(
        "--random-data",
        action="store_true",
        help="Generate randomized data in mock provider.",
    )
    run_parser.add_argument(
        "--allow-defaults", action="store_true",
        help="Legacy diagnostic mode: permit flagged placeholders; not verified research (--strict takes precedence).",
    )
    source_mode = run_parser.add_mutually_exclusive_group()
    source_mode.add_argument(
        "--evidence-pack", default=None,
        help="Hash-checked evidence.json created by fetch-sources or collect-sources.",
    )
    source_mode.add_argument("--auto-sources", action="store_true", help="Collect date-appropriate public SEC filings and daily market history before analysis.")
    run_parser.add_argument("--cik", default=None, help="Optional SEC CIK override for public source discovery (ticker pairing is verified).")
    web_mode = run_parser.add_mutually_exclusive_group()
    web_mode.add_argument("--web-search", dest="web_search", action="store_true", help="One shared Gemini Search + URL Context intake before analysis (default for Gemini; requires financial evidence).")
    web_mode.add_argument("--no-web-search", dest="web_search", action="store_false", help="Disable all additional paid Gemini web intake calls.")
    run_parser.set_defaults(web_search=None)
    run_parser.add_argument("--web-max-sources", type=int, default=4, help="Maximum accepted dated web pages, 1–6 (default: 4); not a hard search-query/dollar cap.")
    run_parser.add_argument("--no-url-context", action="store_true", help="Disable URL Context in the shared intake; retain Search and runner downloads.")
    run_parser.set_defaults(func=cmd_run)

    fetch_parser = subparsers.add_parser("fetch-sources", help="Download explicit public source documents into a cached evidence pack.")
    fetch_parser.add_argument("--manifest", required=True, help="JSON manifest with ticker and document URLs/dates.")
    fetch_parser.add_argument("--out", required=True, help="New directory for retrieved bytes, text and evidence.json.")
    fetch_parser.set_defaults(func=cmd_fetch_sources)

    collect_parser = subparsers.add_parser("collect-sources", help="Discover and cache public SEC filings and date-bounded price history.")
    collect_parser.add_argument("--ticker", required=True)
    collect_parser.add_argument("--analysis-date", required=True, help="YYYY-MM-DD analysis cutoff.")
    collect_parser.add_argument("--depth", choices=["Quick", "Standard", "Deep"], default="Standard")
    collect_parser.add_argument("--cik", default=None, help="Optional SEC CIK; ticker pairing must match.")
    collect_parser.add_argument("--out", required=True, help="New directory for discovery traces and retrieved evidence.")
    collect_parser.set_defaults(func=cmd_collect_sources)

    verify_parser = subparsers.add_parser(
        "verify-run",
        help="Verify schemas, content checksums, lineage and provenance for a run directory.",
    )
    verify_parser.add_argument("run_dir", help="Run directory to verify.")
    verify_parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat missing integrity metadata as errors.",
    )
    verify_parser.set_defaults(func=cmd_verify_run)

    seal_parser = subparsers.add_parser(
        "seal", help="Seal controller-mode envelopes with content checksums."
    )
    seal_parser.add_argument(
        "run_dir", help="Directory containing a*_envelope.json files."
    )
    seal_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-sealing even if content checksum mismatches.",
    )
    seal_parser.set_defaults(func=cmd_seal)

    screen_parser = subparsers.add_parser(
        "screen", help="Rank completed runs using their standardized comparison fields."
    )
    screen_parser.add_argument("runs", nargs="+", help="Run directories to include.")
    screen_parser.add_argument(
        "--format",
        choices=["markdown", "csv"],
        default="markdown",
        help="Output format (default: markdown).",
    )
    screen_parser.add_argument(
        "--sort",
        default="quality_score_10",
        help="Comparison field to sort by, descending (default: quality_score_10).",
    )
    screen_parser.add_argument(
        "--out", default=None, help="Write output to a file instead of stdout."
    )
    screen_parser.set_defaults(func=cmd_screen)

    diff_parser = subparsers.add_parser(
        "diff", help="Compare two runs on scorecard and comparison fields."
    )
    diff_parser.add_argument("run_a", help="Baseline run directory.")
    diff_parser.add_argument("run_b", help="Comparison run directory.")
    diff_parser.set_defaults(func=cmd_diff)

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Evaluate a document using Jev / TypeSafe, Gemini / Google AI Studio, OpenCode Go, or OpenRouter.",
    )
    monitor_parser.add_argument("run_dir", help="Completed run directory.")
    monitor_parser.add_argument(
        "--doc", required=True, help="Path to document file or inline text string."
    )
    monitor_parser.add_argument(
        "--provider",
        default="auto",
        choices=[
            "auto",
            "jev",
            "gemini",
            "opencode",
            "opencode-go",
            "opencode_go",
            "openrouter",
            "mock",
        ],
        help="Evaluation provider (default: auto; uses Jev if configured, else mock; select gemini explicitly for Google AI Studio).",
    )
    monitor_parser.add_argument(
        "--model",
        default=None,
        help="Model name override for Jev, Gemini, OpenCode Go, or OpenRouter.",
    )
    monitor_parser.add_argument(
        "--threshold",
        type=float,
        default=0.75,
        help="Contradiction probability threshold to flag thesis threat (default: 0.75).",
    )
    monitor_parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key override.",
    )
    monitor_parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON."
    )
    monitor_parser.add_argument(
        "--out", default=None, help="Write JSON results to a specified file."
    )
    monitor_parser.set_defaults(func=cmd_monitor)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
