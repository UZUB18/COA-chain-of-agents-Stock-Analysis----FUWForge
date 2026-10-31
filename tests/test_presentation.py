"""
Tests for HTML presentation generation (Slice 4).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.presentation import (
    DEFAULT_PALETTE,
    contrast_ratio,
    get_readable_text_color,
    is_valid_hex_color,
    render_html_report,
    resolve_brand_palette,
)
from src.run_semantic_audit import semantic_audit


def test_contrast_math_and_palette_resolution():
    # Valid and invalid hex codes
    assert is_valid_hex_color("#1e3a8a")
    assert is_valid_hex_color("#fff")
    assert not is_valid_hex_color("blue")
    assert not is_valid_hex_color("#12345")
    assert not is_valid_hex_color("#1234567")

    # Black and white contrast ratio is 21:1
    bw_ratio = contrast_ratio("#000000", "#ffffff")
    assert bw_ratio > 20.0

    # Readable text foreground
    assert get_readable_text_color("#ffffff") == "#0f172a"
    assert get_readable_text_color("#000000") == "#ffffff"

    # Default palette
    pal = resolve_brand_palette()
    assert pal["primary"] == DEFAULT_PALETTE["primary"]
    assert pal["label"] == "Institutional Default Palette"
    assert not pal["is_approximate"]

    # Custom valid brand palette
    custom_pal = resolve_brand_palette(primary="#0a58ca", secondary="#198754", label="Custom Brand")
    assert custom_pal["primary"] == "#0a58ca"
    assert custom_pal["secondary"] == "#198754"
    assert custom_pal["label"] == "Custom Brand"
    assert custom_pal["is_approximate"]


def test_render_html_report_mock():
    eval_mock_dir = PROJECT_ROOT / "runs" / "eval_mock"
    assert eval_mock_dir.is_dir()

    with tempfile.TemporaryDirectory(prefix="fuwforge_pres_") as tmp:
        out_html = Path(tmp) / "report.html"
        result_path = render_html_report(eval_mock_dir, output_path=out_html)
        assert result_path.is_file()
        content = result_path.read_text(encoding="utf-8")

        # HTML landmarks and required structures
        assert "<!DOCTYPE html>" in content
        assert '<header class="site-header"' in content
        assert '<main role="main">' in content
        assert '<footer class="site-footer"' in content
        assert "Microsoft Corp (MSFT)" in content
        assert "PENDING HUMAN REVIEW" in content
        assert "Institutional Default Palette" in content

        # Check sections
        assert 'id="metrics"' in content
        assert 'id="forensics"' in content
        assert 'id="debate"' in content
        assert 'id="summary"' in content
        assert 'id="gaps"' in content
        assert 'id="sources"' in content

        # Check debate content
        assert "Enterprise Cloud Expansion" in content


def test_csp_and_no_remote_assets():
    eval_mock_dir = PROJECT_ROOT / "runs" / "eval_mock"
    with tempfile.TemporaryDirectory(prefix="fuwforge_pres_") as tmp:
        out_html = Path(tmp) / "report.html"
        render_html_report(eval_mock_dir, output_path=out_html)
        content = out_html.read_text(encoding="utf-8")

        # Strict CSP header present
        assert "Content-Security-Policy" in content
        assert "default-src 'none'" in content
        assert "style-src 'unsafe-inline'" in content

        # No external resources (no external stylesheets, scripts, or remote images)
        assert "<link rel=\"stylesheet\"" not in content
        assert "<script src=" not in content
        assert "<img src=\"http" not in content


def test_xss_escaping_in_html():
    from src.presentation import markdown_to_semantic_html

    hostile_input = """# Header <script>alert(1)</script>
<img src=x onerror=alert('xss')>
| Column <tag> | Other |
| --- | --- |
| <script>alert(2)</script> | Safe |
"""
    rendered = markdown_to_semantic_html(hostile_input)
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "&lt;img src=x onerror=alert(" in rendered
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in rendered


def test_cli_render_html():
    with tempfile.TemporaryDirectory(prefix="fuwforge_cli_html_") as tmp:
        out_file = Path(tmp) / "custom_out.html"
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "render-html",
                "runs/eval_mock",
                "--out",
                str(out_file),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert cli.returncode == 0, cli.stderr
        assert out_file.is_file()
        assert "<!DOCTYPE html>" in out_file.read_text(encoding="utf-8")


def test_pipeline_default_generates_html_and_verifies():
    with tempfile.TemporaryDirectory(prefix="fuwforge_pipe_html_") as tmp:
        run_dir = Path(tmp) / "run"
        params = OrchestratorParams(
            company="Presentation Co",
            ticker="PRES",
            analysis_date="2026-02-01",
            depth="Quick",
            focus=[],
            weights="default",
        )
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        res = orchestrator.run("pres_test_run", params, output_root=run_dir)

        # Output contains html_report
        assert "html_report" in res.get("outputs", {})
        html_file = Path(res["outputs"]["html_report"])
        assert html_file.is_file()

        # Audit succeeds with html_report bound to manifest
        report = semantic_audit(run_dir)
        assert report.valid, report.errors
        assert report.levels["final_artifacts"] == "passed"


def test_pipeline_no_html_flag():
    with tempfile.TemporaryDirectory(prefix="fuwforge_pipe_no_html_") as tmp:
        run_dir = Path(tmp) / "run"
        params = OrchestratorParams(
            company="No HTML Co",
            ticker="NOHT",
            analysis_date="2026-02-01",
            depth="Quick",
            focus=[],
            weights="default",
            html_report=False,
        )
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        res = orchestrator.run("no_html_run", params, output_root=run_dir)

        assert "html_report" not in res.get("outputs", {})
        assert not list(run_dir.glob("*_report.html"))

        # Run still passes full audit cleanly
        report = semantic_audit(run_dir)
        assert report.valid, report.errors
        assert report.levels["final_artifacts"] == "passed"


def test_html_tamper_fails_final_artifacts_audit():
    with tempfile.TemporaryDirectory(prefix="fuwforge_html_tamper_") as tmp:
        run_dir = Path(tmp) / "run"
        params = OrchestratorParams(
            company="Tamper Co",
            ticker="TAMP",
            analysis_date="2026-02-01",
            depth="Quick",
            focus=[],
            weights="default",
        )
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        res = orchestrator.run("tamper_test_run", params, output_root=run_dir)
        html_file = Path(res["outputs"]["html_report"])

        # Tamper with the HTML file after sealing
        html_file.write_text(html_file.read_text(encoding="utf-8") + "<!-- post-seal tamper -->", encoding="utf-8")

        report = semantic_audit(run_dir)
        assert not report.valid
        assert report.levels["final_artifacts"] == "failed"
        assert any("final html_report bytes do not match" in err for err in report.errors)


def test_forensic_labels_are_not_invented_or_coloured_safe():
    from src.presentation import PresentationModel, generate_html_report

    def render(altman, beneish):
        return generate_html_report(PresentationModel(
            ticker="T", company="T", analysis_date="2026-01-01", run_id="r", provider="p",
            model="m", checksum="", generated_at="", notices=[], report_html="",
            artifacts={"A3": {"artifacts": {"AltmanZ": altman, "BeneishM": beneish}}},
            summary={}, data_gaps=[], sources=[], palette=DEFAULT_PALETTE,
        ))

    html_out = render({"score": 1.2, "classification": "Distress Zone"},
                      {"score": -1.0, "classification": "High Manipulation Risk"})
    assert 'status-danger">Distress Zone' in html_out
    assert 'status-danger">High Manipulation Risk' in html_out
    # A score without a calculator classification must not gain a reassuring label.
    html_out = render({"score": 4.0}, {"score": -3.0})
    assert "Safe Zone" not in html_out and "Unlikely Manipulator" not in html_out


def test_markdown_to_semantic_html_lists_and_blockquotes():
    from src.presentation import markdown_to_semantic_html

    # Ordered list closes with </ol>, not </ul>
    md_ol = "1. First\n2. Second"
    html_ol = markdown_to_semantic_html(md_ol)
    assert "<ol>" in html_ol and "</ol>" in html_ol
    assert "</ul>" not in html_ol

    # Bullet list followed immediately by numbered list transitions cleanly
    md_trans = "- Bullet A\n- Bullet B\n1. Number 1\n2. Number 2"
    html_trans = markdown_to_semantic_html(md_trans)
    assert "<ul>\n<li>Bullet A</li>\n<li>Bullet B</li>\n</ul>" in html_trans
    assert "<ol>\n<li>Number 1</li>\n<li>Number 2</li>\n</ol>" in html_trans

    # Blockquotes render correctly in body
    md_quote = "> This is a quote"
    html_quote = markdown_to_semantic_html(md_quote)
    assert "<blockquote><p>This is a quote</p></blockquote>" in html_quote


def test_metric_values_html_escaped():
    from src.presentation import PresentationModel, generate_html_report

    xss_str = "<script>alert(1)</script>"
    model = PresentationModel(
        ticker="XSS", company="XSS Corp", analysis_date="2026-01-01", run_id="r", provider="p",
        model="m", checksum="", generated_at="", notices=[], report_html="",
        artifacts={
            "A1": {"artifacts": {"shares_basic": xss_str, "market_cap_check": {}}},
            "A7": {"artifacts": {"comparison_fields": {"quality_score_10": xss_str, "valuation_score_10": xss_str}}},
        },
        summary={}, data_gaps=[], sources=[], palette=DEFAULT_PALETTE,
    )
    html_out = generate_html_report(model)
    assert "<script>" not in html_out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out
