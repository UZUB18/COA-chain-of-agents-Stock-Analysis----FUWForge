"""
Independent semantic and calculation audit (Task 4).

The existing integrity audit answers one question: *do the stored bytes match
their recorded hashes?* That is necessary and it is not sufficient. Every case
below is a run that passes hash verification and still must not be reported as
fully verified:

- duplicate role IDs, and missing roles in an otherwise "completed" run
- a parent hash swapped to another valid envelope
- a final report edited after sealing
- a source unit changed underneath a recorded hash
- a quantitative artifact that is perfectly well-shaped but arithmetically wrong

The audit therefore reports independent levels, and ``verify_run`` may not claim
success at the calculation level just because envelope hashes passed.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run_audit import (
    AUDIT_LEVELS,
    AuditReport,
    verify_run_semantic,
    semantic_audit,
)
from src.utils.beneish import beneish_score

ALL_AGENTS = ["A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"]


def _good_run(tmp: Path, ticker: str = "SEM") -> Path:
    """Produce a real mock run; every damaged variant copies it first."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Semantic Co",
        ticker=ticker,
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("semantic_run", params, output_root=tmp)
    return tmp


def _reseal(path: Path) -> None:
    """Recompute the content checksum after a deliberate edit.

    Used to model a *consistent* tamper: the attacker edits content and
    re-seals, so hash verification passes and only semantic checks can catch it.
    """
    from src.utils.checksum import envelope_content_checksum

    data = json.loads(path.read_text(encoding="utf-8"))
    data["content_checksum"] = envelope_content_checksum(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _envelope_path(run_dir: Path, agent: str) -> Path:
    return run_dir / f"{agent.lower()}_envelope.json"


# ------------------------------------------------------------------ good run


def test_good_run_is_clean_at_every_level():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_good_"))
    try:
        _good_run(tmp)
        report = semantic_audit(tmp)
        assert report.valid, report.errors
        assert set(report.levels) == set(AUDIT_LEVELS)
        # A mock run supplies no evidence pack, so source reconstruction is
        # honestly "not applicable" rather than a pass. Every level that has
        # something to check must actually pass.
        assert report.levels["source_reconstruction"] == "not_applicable"
        for level in (
            "envelope_integrity",
            "graph_and_contracts",
            "calculation_replay",
            "final_artifacts",
            "completeness",
        ):
            assert report.levels[level] == "passed", (level, report.levels)
        assert report.overall_state == "verified"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_completed_run_requires_the_exact_expected_role_set():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_roles_"))
    try:
        _good_run(tmp)
        complete = semantic_audit(tmp)
        assert complete.complete
        assert not complete.levels["graph_and_contracts"] != "passed"

        # Remove a role from an otherwise completed run.
        _envelope_path(tmp, "A5").unlink()
        report = semantic_audit(tmp)
        assert not report.valid
        assert any("A5" in err for err in report.errors)
        assert "graph_and_contracts" not in report.levels or report.levels[
            "graph_and_contracts"
        ] != "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- graph level


def test_duplicate_role_ids_are_rejected():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_dup_"))
    try:
        _good_run(tmp)
        # Write a second envelope claiming to be A3.
        original = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        (tmp / "a3_copy_envelope.json").write_text(
            json.dumps(original, indent=2), encoding="utf-8"
        )
        report = semantic_audit(tmp)
        assert not report.valid
        assert any("duplicate" in err.lower() and "A3" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_swapped_parent_hash_is_caught_by_graph_validation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_swap_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        # Point A3 at A2's real checksum: a plausible-looking, self-consistent
        # lie that hash verification alone cannot detect.
        a2 = json.loads(_envelope_path(tmp, "A2").read_text(encoding="utf-8"))
        a3["upstream"] = [
            {"agent": "A2", "content_checksum": a2["content_checksum"]}
        ]
        _envelope_path(tmp, "A3").write_text(json.dumps(a3, indent=2), encoding="utf-8")
        _reseal(_envelope_path(tmp, "A3"))

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("graph_and_contracts") != "passed"
        # A3 only ever consumed A1 in the parallel phase.
        assert any("A3" in err and "A2" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- final-artifact level


def test_changed_report_after_sealing_is_caught():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_report_"))
    try:
        _good_run(tmp)
        report_path = next(tmp.glob("*_report.md"))
        original = report_path.read_text(encoding="utf-8")
        report_path.write_text(original + "\n\n## Injected conclusion\n", encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("final_artifacts") != "passed"
        assert any("report" in err.lower() for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_changed_artifact_bundle_after_sealing_is_caught():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_bundle_"))
    try:
        _good_run(tmp)
        bundle_path = next(tmp.glob("*_artifacts.json"))
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        payload["A7"]["artifacts"]["comparison_fields"]["quality_score_10"] = 9.9
        bundle_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("final_artifacts") != "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------- calculation level


def test_well_shaped_but_arithmetically_wrong_beneish_is_rejected():
    """
    Schema-valid, in-range, correctly shaped - and still wrong.

    This is the case that a "finite number in a permissible range" check passes
    and a real recomputation does not.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_math_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        beneish = a3["artifacts"]["BeneishM"]
        # Perturb the score while keeping its shape, range and classification.
        # -2.0121 is the true value for these indices; -1.71 is not.
        beneish["raw_score"] = -1.71
        beneish["score"] = -1.71
        _envelope_path(tmp, "A3").write_text(json.dumps(a3, indent=2), encoding="utf-8")
        _reseal(_envelope_path(tmp, "A3"))

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("calculation_replay") != "passed"
        assert any("beneish" in err.lower() for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_beneish_witness_missing_is_reported_not_assumed():
    """A Beneish with a deterministic claim but no inputs cannot be replayed."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_wit_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        a3["artifacts"]["BeneishM"].pop("calculation_witness", None)
        _envelope_path(tmp, "A3").write_text(json.dumps(a3, indent=2), encoding="utf-8")
        _reseal(_envelope_path(tmp, "A3"))

        report = semantic_audit(tmp)
        assert report.levels.get("calculation_replay") != "passed"
        assert any(
            "witness" in err.lower() or "not replayable" in err.lower()
            for err in report.errors
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_correct_beneish_witness_replays_exactly():
    """The good fixture must actually be replayable, or the check is vacuous."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_replay_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        witness = a3["artifacts"]["BeneishM"]["calculation_witness"]
        expected = beneish_score(**witness["inputs"])
        assert a3["artifacts"]["BeneishM"]["raw_score"] == pytest.approx(expected)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------- source reconstruction


def test_missing_source_bytes_are_unavailable_not_success():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_src_"))
    try:
        _good_run(tmp)
        # A run with no evidence pack must not claim source verification.
        report = semantic_audit(tmp)
        assert report.levels["source_reconstruction"] in (
            "not_applicable",
            "source_bytes_unavailable",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ halted / levels


def test_halted_run_is_explicitly_incomplete():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_halt_"))
    try:
        from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
        params = OrchestratorParams(
            company="Halt Co",
            ticker="HALT",
            analysis_date="2026-02-01",
            depth="Standard",
            focus=[],
            weights="default",
        )
        params.__dict__["fail_gate"] = True
        with pytest.raises(RuntimeError):
            orchestrator.run("halted", params, output_root=tmp)

        report = semantic_audit(tmp)
        assert not report.complete, "a halted run must not be reported as complete"
        assert report.levels.get("completeness") == "incomplete"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_levels_are_reported_independently():
    """The whole point: one reassuring word is not an audit result."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_levels_"))
    try:
        _good_run(tmp)
        report = semantic_audit(tmp)
        assert isinstance(report, AuditReport)
        for level in AUDIT_LEVELS:
            assert level in report.levels
        assert report.overall_state in {
            "verified",
            "incomplete",
            "failed",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_run_semantic_is_a_thin_compatible_wrapper():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_compat_"))
    try:
        _good_run(tmp)
        result = verify_run_semantic(tmp, strict=False)
        assert result.valid, result.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)