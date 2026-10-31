"""
Unit tests for deterministic evaluation harness and research scorer (Task 8).
"""

import json
from pathlib import Path
import subprocess
import sys
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.score_research import (
    load_jsonl,
    score_case,
    score_predictions,
    format_markdown_report,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "eval"
CASES_FILE = Path(__file__).resolve().parent.parent / "evals" / "research_cases.jsonl"


class TestDeterministicScorer:
    """Test individual error modes and score breakdown categories."""

    @pytest.fixture
    def cases_by_id(self):
        cases = load_jsonl(CASES_FILE)
        return {c["id"]: c for c in cases}

    def test_clean_prediction_passes_all_checks(self, cases_by_id):
        case = cases_by_id["case_01_amended_filing"]
        pred_path = FIXTURES_DIR / "clean_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["passed"] is True
        assert res["categories"]["unsupported_claims"]["passed"] is True
        assert res["categories"]["arithmetic"]["passed"] is True
        assert res["categories"]["chronology"]["passed"] is True
        assert res["categories"]["source_references"]["passed"] is True
        assert res["sub_checks"]["missingness_ok"] is True
        assert res["sub_checks"]["injection_safe"] is True

        # Check cost & reviewer effort fields are populated
        cost = res["categories"]["cost"]
        assert cost["tokens_total"] == 540
        assert cost["cost_usd"] == pytest.approx(0.0012)
        assert cost["latency_ms"] == pytest.approx(650.0)

        effort = res["categories"]["reviewer_effort"]
        assert effort["open_items_count"] == 0
        assert effort["review_priority"] == "low"

    def test_persuasive_unsupported_prose_fails_unsupported_claims(self, cases_by_id):
        case = cases_by_id["case_08_unsupported_forward_claim"]
        pred_path = FIXTURES_DIR / "unsupported_prose_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["passed"] is False
        assert res["categories"]["unsupported_claims"]["passed"] is False
        assert len(res["categories"]["unsupported_claims"]["errors"]) > 0

        # Confirm specific unsupported claims were flagged
        errors = " ".join(res["categories"]["unsupported_claims"]["errors"])
        assert "45% market share by 2028" in errors or "no source attribution" in errors

    def test_numerically_wrong_formula_fails_arithmetic_despite_eloquent_prose(self, cases_by_id):
        case = cases_by_id["case_07_beneish_calculator_edge"]
        pred_path = FIXTURES_DIR / "arithmetic_error_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["passed"] is False
        assert res["categories"]["arithmetic"]["passed"] is False
        assert len(res["categories"]["arithmetic"]["errors"]) > 0

        err_msg = res["categories"]["arithmetic"]["errors"][0]
        assert "expected -2.0121" in err_msg
        assert "-4.5" in err_msg

    def test_correct_explicit_unknown_passes_missingness(self, cases_by_id):
        case = cases_by_id["case_05_missing_source_item"]
        pred_path = FIXTURES_DIR / "explicit_unknown_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["sub_checks"]["missingness_ok"] is True
        assert res["passed"] is True

        # Reviewer effort should indicate open items
        effort = res["categories"]["reviewer_effort"]
        assert effort["open_items_count"] >= 1
        assert "tata" in effort["open_items"]
        assert effort["review_priority"] == "high"

    def test_hallucinated_missing_value_fails_missingness(self, cases_by_id):
        case = cases_by_id["case_05_missing_source_item"]
        pred_path = FIXTURES_DIR / "hallucinated_unknown_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["sub_checks"]["missingness_ok"] is False
        assert res["passed"] is False

    def test_following_injected_instructions_fails_injection_safe(self, cases_by_id):
        case = cases_by_id["case_06_injected_instruction"]
        pred_path = FIXTURES_DIR / "injection_followed_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["passed"] is False
        assert res["sub_checks"]["injection_safe"] is False
        assert len(res["sub_checks"]["injection_errors"]) > 0

    def test_post_cutoff_fact_fails_chronology(self, cases_by_id):
        case = cases_by_id["case_09_post_cutoff_chronology_leak"]
        pred_path = FIXTURES_DIR / "chronology_violation_prediction.json"
        prediction = json.loads(pred_path.read_text(encoding="utf-8"))

        res = score_case(case, prediction)
        assert res["passed"] is False
        assert res["categories"]["chronology"]["passed"] is False
        assert len(res["categories"]["chronology"]["violations"]) > 0
        assert "after case cutoff 2023-08-01" in res["categories"]["chronology"]["violations"][0]

    def test_conflicting_units_1000x_error_fails_arithmetic(self, cases_by_id):
        case = cases_by_id["case_02_conflicting_units_1000x"]
        # Prediction with 1000x scaling error: 120,000 USD instead of 120,000,000 USD
        bad_pred = {
            "case_id": "case_02_conflicting_units_1000x",
            "claims": [{"claim_id": "c1", "text": "Revenues $120k", "source_id": "src_10k_2023", "status": "supported"}],
            "facts": {
                "Revenues": {"value": 120000.0, "source_id": "src_10k_2023", "availability_time": "2024-02-10T00:00:00Z"},
                "OperatingIncome": {"value": 40000000.0, "source_id": "src_10k_2023", "availability_time": "2024-02-10T00:00:00Z"},
            },
            "sources_used": ["src_10k_2023"],
            "missing_items": [],
        }
        res = score_case(case, bad_pred)
        assert res["categories"]["arithmetic"]["passed"] is False
        assert any("1000x mismatch" in e for e in res["categories"]["arithmetic"]["errors"])

    def test_share_basis_mismatch_fails_arithmetic(self, cases_by_id):
        case = cases_by_id["case_04_share_basis_mismatch"]
        # Prediction using basic shares (100M) for diluted EPS ($2.00 instead of $1.60)
        bad_pred = {
            "case_id": "case_04_share_basis_mismatch",
            "claims": [{"claim_id": "c1", "text": "Diluted EPS is $2.00", "source_id": "src_10k_2023", "status": "supported"}],
            "facts": {
                "NetIncome": {"value": 200.0, "source_id": "src_10k_2023"},
                "BasicShares": {"value": 100.0, "source_id": "src_10k_2023"},
                "DilutedShares": {"value": 125.0, "source_id": "src_10k_2023"},
                "BasicEPS": {"value": 2.00, "source_id": "src_10k_2023"},
                "DilutedEPS": {"value": 2.00, "source_id": "src_10k_2023"},
            },
            "calculations": {
                "diluted_eps": {"result": 2.00, "formula_id": "net_income_div_basic_shares"},
            },
            "sources_used": ["src_10k_2023"],
            "missing_items": [],
        }
        res = score_case(case, bad_pred)
        assert res["categories"]["arithmetic"]["passed"] is False
        assert any("expected 1.6" in e for e in res["categories"]["arithmetic"]["errors"])

    def test_superseded_source_usage_fails_source_references(self, cases_by_id):
        case = cases_by_id["case_01_amended_filing"]
        # Prediction cites superseded unamended 10-K for restated revenue
        bad_pred = {
            "case_id": "case_01_amended_filing",
            "claims": [{"claim_id": "c1", "text": "Revenue is $460M", "source_id": "src_10k_2022", "status": "supported"}],
            "facts": {
                "FY2022_Revenue": {"value": 460.0, "source_id": "src_10k_2022", "availability_time": "2023-02-15T00:00:00Z"},
                "FY2022_NetIncome": {"value": 35.0, "source_id": "src_10k_a_2022", "availability_time": "2023-06-20T00:00:00Z"},
            },
            "sources_used": ["src_10k_2022"],
            "missing_items": [],
        }
        res = score_case(case, bad_pred)
        assert res["categories"]["source_references"]["passed"] is False
        assert any("superseded" in e for e in res["categories"]["source_references"]["errors"])

    def test_claim_citation_chronology_check(self, cases_by_id):
        case = cases_by_id["case_09_post_cutoff_chronology_leak"]
        # Claim cites post-cutoff source even if not listed in sources_used
        pred = {
            "case_id": "case_09_post_cutoff_chronology_leak",
            "claims": [{"claim_id": "c1", "text": "Q3 2023 deliveries reached 80,000 units.", "source_id": "src_10q_q3_2023", "status": "supported"}],
            "facts": {
                "Q1_2023_Deliveries": {"value": 50000, "source_id": "src_10q_q1_2023", "availability_time": "2023-05-10T00:00:00Z"},
            },
            "sources_used": ["src_10q_q1_2023"],
            "missing_items": [],
        }
        res = score_case(case, pred)
        assert res["categories"]["chronology"]["passed"] is False
        assert any("published at 2023-11-15T00:00:00Z, after case cutoff" in v for v in res["categories"]["chronology"]["violations"])

    def test_claim_citation_superseded_source_check(self, cases_by_id):
        case = cases_by_id["case_01_amended_filing"]
        # Claim cites superseded source src_10k_2022
        pred = {
            "case_id": "case_01_amended_filing",
            "claims": [{"claim_id": "c1", "text": "Restated revenue is $460M", "source_id": "src_10k_2022", "status": "supported"}],
            "facts": {
                "FY2022_Revenue": {"value": 460.0, "source_id": "src_10k_a_2022", "availability_time": "2023-06-20T00:00:00Z"},
                "FY2022_NetIncome": {"value": 35.0, "source_id": "src_10k_a_2022", "availability_time": "2023-06-20T00:00:00Z"},
            },
            "sources_used": ["src_10k_a_2022"],
            "missing_items": [],
        }
        res = score_case(case, pred)
        assert res["categories"]["source_references"]["passed"] is False
        assert any("superseded" in e for e in res["categories"]["source_references"]["errors"])

    def test_missing_expected_required_fact_detected(self, cases_by_id):
        case = cases_by_id["case_01_amended_filing"]
        # Missing FY2022_NetIncome
        pred = {
            "case_id": "case_01_amended_filing",
            "claims": [{"claim_id": "c1", "text": "Revenue is $460M", "source_id": "src_10k_a_2022", "status": "supported"}],
            "facts": {
                "FY2022_Revenue": {"value": 460.0, "source_id": "src_10k_a_2022", "availability_time": "2023-06-20T00:00:00Z"},
            },
            "sources_used": ["src_10k_a_2022"],
            "missing_items": [],
        }
        res = score_case(case, pred)
        assert res["categories"]["arithmetic"]["passed"] is False
        assert any("Missing expected required fact 'FY2022_NetIncome'" in e for e in res["categories"]["arithmetic"]["errors"])

    def test_reject_boolean_and_nonfinite_numerics(self, cases_by_id):
        case = cases_by_id["case_04_share_basis_mismatch"]
        # Boolean in calculation result and NaN in fact
        bad_pred_bool = {
            "case_id": "case_04_share_basis_mismatch",
            "claims": [{"claim_id": "c1", "text": "Diluted EPS is valid", "source_id": "src_10k_2023", "status": "supported"}],
            "facts": {
                "NetIncome": {"value": 200.0, "source_id": "src_10k_2023"},
                "BasicShares": {"value": 100.0, "source_id": "src_10k_2023"},
                "DilutedShares": {"value": 125.0, "source_id": "src_10k_2023"},
                "BasicEPS": {"value": True, "source_id": "src_10k_2023"},
                "DilutedEPS": {"value": float("nan"), "source_id": "src_10k_2023"},
            },
            "calculations": {
                "diluted_eps": {"result": True},
            },
            "sources_used": ["src_10k_2023"],
            "missing_items": [],
        }
        res = score_case(case, bad_pred_bool)
        assert res["categories"]["arithmetic"]["passed"] is False
        assert len(res["categories"]["arithmetic"]["errors"]) >= 2
        errs = " ".join(res["categories"]["arithmetic"]["errors"])
        assert "boolean" in errs.lower() or "non-numeric" in errs.lower() or "non-finite" in errs.lower()

    def test_malformed_shapes_fail_safely(self, cases_by_id):
        case = cases_by_id["case_01_amended_filing"]

        # Malformed claims (not a list, containing non-dicts)
        pred_bad_claims = {
            "case_id": "case_01_amended_filing",
            "claims": "not a list",
            "facts": None,
            "calculations": "bad",
        }
        res1 = score_case(case, pred_bad_claims)
        assert res1["passed"] is False
        assert isinstance(res1["categories"]["unsupported_claims"]["errors"], list)

        # Malformed items inside list
        pred_bad_claim_items = {
            "case_id": "case_01_amended_filing",
            "claims": [None, 123, "string claim"],
            "facts": {"FY2022_Revenue": "invalid_number"},
        }
        res2 = score_case(case, pred_bad_claim_items)
        assert res2["passed"] is False

        # None prediction
        res3 = score_case(case, None)  # type: ignore
        assert res3["passed"] is False


class TestDatasetScoringAndCLI:
    """Test full dataset evaluation, markdown formatting, and CLI execution."""

    def test_score_dataset_summary(self):
        cases = load_jsonl(CASES_FILE)
        preds = load_jsonl(FIXTURES_DIR / "sample_predictions_clean.jsonl")

        report = score_predictions(cases, preds)
        summary = report["summary"]

        assert summary["total_cases"] == len(cases)
        assert summary["passed_cases"] == len(cases)
        assert summary["failed_cases"] == 0
        assert summary["pass_rate"] == 1.0
        assert summary["missing_cases_count"] == 0
        assert summary["duplicate_cases_count"] == 0
        assert summary["unknown_cases_count"] == 0
        assert summary["categories_summary"]["arithmetic_pass_rate"] == 1.0
        assert summary["categories_summary"]["chronology_pass_rate"] == 1.0
        assert summary["categories_summary"]["injection_safe_rate"] == 1.0
        assert summary["cost_summary"]["total_tokens"] > 0
        assert summary["reviewer_effort_summary"]["total_open_items"] >= 0

        # Markdown report rendering
        md = format_markdown_report(report)
        assert "# Research Evaluation Report (Deterministic Scorer)" in md
        assert "Unsupported Claims" in md
        assert "Arithmetic / Formulas" in md
        assert "case_01_amended_filing" in md

    def test_missing_cases_in_predictions_fails_dataset(self):
        cases = load_jsonl(CASES_FILE)
        # Only provide 1 prediction out of 9 cases
        clean_preds = load_jsonl(FIXTURES_DIR / "sample_predictions_clean.jsonl")
        subset_preds = [clean_preds[0]]  # only case_01

        report = score_predictions(cases, subset_preds)
        summary = report["summary"]

        assert summary["total_cases"] == 9
        assert summary["passed_cases"] == 1
        assert summary["failed_cases"] == 8
        assert summary["pass_rate"] < 1.0
        assert summary["missing_cases_count"] == 8

    def test_duplicate_cases_in_predictions_fails_dataset(self):
        cases = load_jsonl(CASES_FILE)
        clean_preds = load_jsonl(FIXTURES_DIR / "sample_predictions_clean.jsonl")
        dup_preds = list(clean_preds) + [clean_preds[0]]  # duplicate case_01

        report = score_predictions(cases, dup_preds)
        summary = report["summary"]

        assert summary["duplicate_cases_count"] == 1
        assert "case_01_amended_filing" in summary["duplicate_cases"]
        # Case 01 duplicate should fail
        assert summary["passed_cases"] == 8
        assert summary["failed_cases"] >= 2  # duplicates failed

    def test_unknown_cases_in_predictions_fails_dataset(self):
        cases = load_jsonl(CASES_FILE)
        clean_preds = load_jsonl(FIXTURES_DIR / "sample_predictions_clean.jsonl")
        unknown_pred = {
            "case_id": "case_999_fake",
            "claims": [],
            "facts": {},
            "sources_used": [],
        }
        preds = list(clean_preds) + [unknown_pred]

        report = score_predictions(cases, preds)
        summary = report["summary"]

        assert summary["unknown_cases_count"] == 1
        assert "case_999_fake" in summary["unknown_cases"]
        assert summary["failed_cases"] >= 1

    def test_cli_execution_with_report_and_markdown(self, tmp_path):
        out_report = tmp_path / "eval_out.json"
        cmd = [
            sys.executable,
            "evals/score_research.py",
            str(CASES_FILE),
            str(FIXTURES_DIR / "sample_predictions_clean.jsonl"),
            "--report",
            str(out_report),
            "--markdown",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        assert "Research Evaluation Report (Deterministic Scorer)" in proc.stdout
        assert out_report.exists()

        saved_data = json.loads(out_report.read_text(encoding="utf-8"))
        assert "summary" in saved_data
        assert saved_data["summary"]["total_cases"] == 9

    def test_cli_fail_on_errors_flag_clean(self):
        cmd = [
            sys.executable,
            "evals/score_research.py",
            str(CASES_FILE),
            str(FIXTURES_DIR / "sample_predictions_clean.jsonl"),
            "--fail-on-errors",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        assert "Passed: 9 (100.0%)" in proc.stdout

    def test_cli_fail_on_errors_flag_errors(self):
        cmd = [
            sys.executable,
            "evals/score_research.py",
            str(CASES_FILE),
            str(FIXTURES_DIR / "sample_predictions_with_errors.jsonl"),
            "--fail-on-errors",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 1
        assert "Failed: 9" in proc.stdout
        assert "Missing Cases" in proc.stdout

    def test_cli_without_fail_on_errors_flag_exits_zero(self):
        cmd = [
            sys.executable,
            "evals/score_research.py",
            str(CASES_FILE),
            str(FIXTURES_DIR / "sample_predictions_with_errors.jsonl"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        assert "Failed: 9" in proc.stdout

    def test_cli_execution_from_outside_cwd(self, tmp_path):
        repo_root = Path(__file__).resolve().parent.parent
        script_path = repo_root / "evals" / "score_research.py"
        cmd = [
            sys.executable,
            str(script_path),
            str(CASES_FILE),
            str(FIXTURES_DIR / "sample_predictions_clean.jsonl"),
            "--fail-on-errors",
        ]
        # Run with cwd set to tmp_path outside repo
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 0
        assert "Passed: 9 (100.0%)" in proc.stdout


@pytest.mark.parametrize("available,ok", [
    ("2023-08-01", True),
    ("2023-08-01T23:59:59Z", True),
    ("2023-08-02", False),
])
def test_chronology_date_only_case_cutoff_is_end_of_day(available, ok):
    from evals.score_research import _evaluate_chronology

    case = {"cutoff": "2023-08-01", "source_pack": [{"source_id": "S1", "filing_date": available}]}
    prediction = {"sources_used": ["S1"], "facts": {"Revenue": {"availability_time": available}}}
    result = _evaluate_chronology(case, prediction)
    assert result["passed"] is ok
    if not ok:
        # The human-readable diagnostic keeps the original cutoff text.
        assert all("after case cutoff 2023-08-01." in v for v in result["violations"])
