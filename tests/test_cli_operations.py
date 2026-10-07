"""
CLI workflow tests:
1. `--input` YAML supplies run parameters (with CLI flags overriding).
2. Invalid `--weights` overrides are rejected; valid ones change the scorecard.
3. `seal` + `verify-run` close the loop on controller-mode envelopes.
4. `screen` and `diff` consume comparison_fields across runs.
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

from src.controller import build_parser


def _cli(args):
    return subprocess.run(
        [sys.executable, "-m", "src.controller", *args],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_run_with_input_file_and_weight_override():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_input_"))
    try:
        input_file = tmp / "input.yaml"
        input_file.write_text(
            "inputs:\n"
            "  company: Input Corp\n"
            "  ticker: INPUT\n"
            "  analysis_date: 2026-02-01\n"
            "  depth: Standard\n",
            encoding="utf-8",
        )
        res = _cli(
            [
                "run",
                str(tmp / "run_in"),
                "--input",
                str(input_file),
                "--provider",
                "mock",
            ]
        )
        assert res.returncode == 0, res.stderr
        assert (tmp / "run_in" / "INPUT_2026-02-01_report.md").exists()

        # CLI flags override the input file.
        res2 = _cli(
            [
                "run",
                str(tmp / "run_override"),
                "--input",
                str(input_file),
                "--ticker",
                "OVERRIDE",
                "--depth",
                "Quick",
                "--provider",
                "mock",
            ]
        )
        assert res2.returncode == 0, res2.stderr
        assert (tmp / "run_override" / "OVERRIDE_2026-02-01_report.md").exists()

        # Missing ticker anywhere is an error.
        empty = tmp / "empty.yaml"
        empty.write_text("inputs: {}\n", encoding="utf-8")
        res3 = _cli(
            ["run", str(tmp / "run_bad"), "--input", str(empty), "--provider", "mock"]
        )
        assert res3.returncode != 0
        assert "ticker" in (res3.stderr + res3.stdout)

        # Invalid weights are rejected before any agent runs.
        res4 = _cli(
            [
                "run",
                str(tmp / "run_weights"),
                "--ticker",
                "BADW",
                "--weights",
                '{"Financials": 0.9}',
            ]
        )
        assert res4.returncode != 0
        assert "weights" in (res4.stderr + res4.stdout).lower()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_weights_override_changes_scorecard():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_weights_"))
    try:
        res = _cli(
            [
                "run",
                str(tmp),
                "--ticker",
                "WEIGHTED",
                "--analysis-date",
                "2026-02-01",
                "--provider",
                "mock",
                "--weights",
                '{"Financials": 0.3, "Moat": 0.15, "Growth": 0.15, "Valuation": 0.15, "MgmtGov": 0.1, "RiskInverse": 0.1, "ESG": 0.025, "Market": 0.025}',
            ]
        )
        assert res.returncode == 0, res.stderr
        a7 = json.loads((tmp / "a7_envelope.json").read_text(encoding="utf-8"))
        assert a7["artifacts"]["Scorecard"]["Financials"]["weight"] == 0.3
        assert abs(a7["artifacts"]["Scorecard"]["Overall"]["score"] - 7.9) < 0.01
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_seal_and_verify_controller_envelopes():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_seal_"))
    try:
        res = _cli(
            [
                "run",
                str(tmp),
                "--ticker",
                "SEALME",
                "--analysis-date",
                "2026-02-01",
                "--provider",
                "mock",
            ]
        )
        assert res.returncode == 0, res.stderr
        for path in tmp.glob("*_envelope.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("content_checksum", None)
            data.pop("upstream", None)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        assert _cli(["verify-run", str(tmp)]).returncode == 0
        assert _cli(["verify-run", str(tmp), "--strict"]).returncode != 0

        seal = _cli(["seal", str(tmp)])
        assert seal.returncode == 0, seal.stderr
        assert _cli(["verify-run", str(tmp), "--strict"]).returncode == 0

        # Modifying an envelope causes normal seal to refuse, but --force succeeds
        a2_file = tmp / "a2_envelope.json"
        a2_data = json.loads(a2_file.read_text(encoding="utf-8"))
        a2_data["artifacts"]["MoatTrend"] = "Stable"
        a2_file.write_text(json.dumps(a2_data, indent=2), encoding="utf-8")

        refuse = _cli(["seal", str(tmp)])
        assert refuse.returncode != 0
        assert "refusing to re-seal" in (refuse.stderr + refuse.stdout)

        forced = _cli(["seal", str(tmp), "--force"])
        assert forced.returncode == 0, forced.stderr
        assert _cli(["verify-run", str(tmp), "--strict"]).returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_assemble_auto_discovers_params():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_assemble_"))
    try:
        # Run a baseline mock run
        res = _cli(
            [
                "run",
                str(tmp),
                "--ticker",
                "AUTOPARAM",
                "--company",
                "AutoParam Inc",
                "--analysis-date",
                "2026-03-01",
                "--provider",
                "mock",
            ]
        )
        assert res.returncode == 0, res.stderr

        # Remove the report so assemble recreates it
        report_file = tmp / "AUTOPARAM_2026-03-01_report.md"
        assert report_file.exists()
        report_file.unlink()

        # Assemble without --ticker or --input flags; should auto-discover from envelopes
        res_assemble = _cli(["assemble", str(tmp)])
        assert res_assemble.returncode == 0, res_assemble.stderr
        assert report_file.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_screen_and_diff_commands(capsys):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_cli_screen_"))
    try:
        for ticker in ("ALPHA", "BETA"):
            res = _cli(
                [
                    "run",
                    str(tmp / ticker),
                    "--ticker",
                    ticker,
                    "--analysis-date",
                    "2026-02-01",
                    "--provider",
                    "mock",
                ]
            )
            assert res.returncode == 0, res.stderr

        # Test screen on explicit dirs
        parser = build_parser()
        args = parser.parse_args(["screen", str(tmp / "ALPHA"), str(tmp / "BETA")])
        args.func(args)
        output = capsys.readouterr().out
        assert "ALPHA" in output and "BETA" in output
        assert "quality_score_10" in output

        # Test screen discovering runs inside a parent directory
        args_parent = parser.parse_args(["screen", str(tmp)])
        args_parent.func(args_parent)
        parent_out = capsys.readouterr().out
        assert "ALPHA" in parent_out and "BETA" in parent_out

        args = parser.parse_args(
            ["screen", str(tmp / "ALPHA"), str(tmp / "BETA"), "--format", "csv"]
        )
        args.func(args)
        csv_out = capsys.readouterr().out
        assert csv_out.splitlines()[0].startswith("ticker,")

        args = parser.parse_args(["diff", str(tmp / "ALPHA"), str(tmp / "BETA")])
        args.func(args)
        diff_out = capsys.readouterr().out
        assert "Scorecard" in diff_out
        assert "Comparison fields" in diff_out
        assert "Provenance differences" in diff_out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
