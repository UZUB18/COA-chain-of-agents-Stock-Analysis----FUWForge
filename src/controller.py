# CLI utilities for controller mode validation and assembly.

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from src.envelope import Envelope
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.validators import validate_checksum


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def cmd_validate(args: argparse.Namespace) -> None:
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT)
    data = json.loads(Path(args.envelope).read_text(encoding="utf-8"))
    result = orchestrator.validator.validate(data)
    if not result.valid:
        raise SystemExit(f"Validation failed:\n- " + "\n- ".join(result.errors))

    if args.expected_checksum:
        envelope = Envelope.from_dict(data)
        checksum_result = validate_checksum(envelope, args.expected_checksum)
        if not checksum_result.valid:
            raise SystemExit(f"Checksum validation failed: {checksum_result.errors[0]}")

    print("Envelope validation succeeded.")


def cmd_assemble(args: argparse.Namespace) -> None:
    focus_tags: List[str] = args.focus or []
    params = OrchestratorParams(
        company=args.company,
        ticker=args.ticker,
        analysis_date=args.analysis_date,
        depth=args.depth,
        focus=focus_tags,
        weights=args.weights,
    )
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT)
    summary = orchestrator.run(
        run_id=args.run_id,
        params=params,
        mode="controller",
        output_root=Path(args.run_dir).resolve(),
    )
    print("Run assembled successfully.")
    print(json.dumps(summary, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FUWForge (Fired Up Wealth) controller utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate an agent envelope.")
    validate_parser.add_argument("envelope", help="Path to the JSON envelope file.")
    validate_parser.add_argument("--expected-checksum", help="Checksum emitted by A1.")
    validate_parser.set_defaults(func=cmd_validate)

    assemble_parser = subparsers.add_parser("assemble", help="Assemble a run from controller mode outputs.")
    assemble_parser.add_argument("run_dir", help="Directory containing a*_envelope.json files.")
    assemble_parser.add_argument("--run-id", required=True, help="Unique run identifier.")
    assemble_parser.add_argument("--company", required=True, help="Company name.")
    assemble_parser.add_argument("--ticker", required=True, help="Ticker symbol.")
    assemble_parser.add_argument("--analysis-date", required=True, help="Analysis date (YYYY-MM-DD).")
    assemble_parser.add_argument("--depth", default="Standard", help="Depth mode (Quick|Standard|Deep).")
    assemble_parser.add_argument("--focus", nargs="*", default=[], help="Optional focus tags.")
    assemble_parser.add_argument("--weights", default="default", help="Scorecard weight override or 'default'.")
    assemble_parser.set_defaults(func=cmd_assemble)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
