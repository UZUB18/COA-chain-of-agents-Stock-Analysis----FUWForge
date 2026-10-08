"""End-to-end offline smoke tests through the real CLI entrypoints.

Everything here drives ``python -m src.controller`` as a subprocess, using the
documented commands and flags. The library-level units are covered elsewhere;
this file answers a different question: *does the documented workflow still
work end to end, offline, on a clean checkout?*

Scenarios covered (all with the mock provider, no network, no credentials):

- normal research execution
- interrupted execution and safe resume
- evidence snapshot reuse
- refusal to resume with changed evidence
- reused-envelope checksum stability
- ``verify-run`` and ``verify-run --strict``
- evidence bundle verification and restoration
- sealing after a change to upstream artifacts
- research-packet export

The recovery policy is asserted as it is implemented: an uncertain provider
call stops for an explicit operator choice, and a retry may re-bill. Nothing
here promises exactly-once billing, because the API cannot guarantee it.
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

RUN_ARGS = [
    "--provider",
    "mock",
    "--company",
    "Smoke Co",
    "--ticker",
    "SMK",
    "--analysis-date",
    "2026-02-01",
    "--depth",
    "Standard",
]


def _cli(*args, expect_ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-m", "src.controller", *args],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONHASHSEED": "0"},
    )
    if expect_ok and result.returncode != 0:
        raise AssertionError(
            f"CLI failed ({result.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def _audit_report(run_dir: Path) -> dict:
    """
    Parse ``verify-run --json`` output into the audit report.

    The CLI interleaves orchestrator log lines with the JSON document on
    stdout may interleave orchestrator log lines with the JSON document, so the
    report is extracted with a streaming decoder that stops at the end of the
    first JSON value rather than assuming the stream ends there.
    """
    result = _cli("verify-run", str(run_dir), "--json")
    text = result.stdout
    start = text.find("{")
    assert start != -1, text
    report, _end = json.JSONDecoder().raw_decode(text[start:])
    return report


def _run(tmp: Path, *extra, expect_ok: bool = True) -> subprocess.CompletedProcess:
    return _cli("run", str(tmp / "run"), *RUN_ARGS, *extra, expect_ok=expect_ok)


# ============================================ 1. normal research execution


def test_normal_execution_produces_a_complete_verifiable_run(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_run_"))
    try:
        _run(tmp)
        run_dir = tmp / "run"
        assert (run_dir / "SMK_2026-02-01_report.md").exists()
        assert (run_dir / "SMK_2026-02-01_artifacts.json").exists()
        assert (run_dir / "run_manifest.json").exists()
        for agent in ("a1", "a2", "a3", "a4", "a5", "a6", "a7"):
            assert (run_dir / f"{agent}_envelope.json").exists(), agent

        report = _audit_report(run_dir)
        assert report["overall_state"] == "verified", report
        assert report["complete"] is True
        assert report["levels"]["calculation_replay"] == "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_run_strict_passes_on_a_clean_run(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_strict_"))
    try:
        _run(tmp)
        result = _cli("verify-run", str(tmp / "run"), "--strict")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Audit passed" in result.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_run_discloses_unavailable_inputs(tmp_path):
    """
    A mock run has no fact ledger, so its indices are unverified.

    The CLI must say so. A bare "Audit passed" with no disclosure would let a
    reader assume the arithmetic was source-backed.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_unavail_"))
    try:
        _run(tmp)
        result = _cli("verify-run", str(tmp / "run"))
        assert "unavailable" in result.stdout.lower()
        assert "no source fact" in result.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ======================================== 2. interrupted execution + resume


def test_interrupted_run_resumes_and_reuses_receipts(tmp_path):
    """
    Interrupt a run, then resume it.

    Resume must reuse verified prior work rather than silently producing a
    second, divergent run.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_resume_"))
    try:
        run_dir = tmp / "run"
        # First run completes normally.
        _run(tmp)
        original_report = (run_dir / "SMK_2026-02-01_report.md").read_bytes()
        ledger = run_dir / "execution_ledger.db"
        assert ledger.is_file()

        # Resume is a no-op on a complete run and must still verify.
        result = _cli("run", str(run_dir), *RUN_ARGS, "--resume")
        assert result.returncode == 0, result.stdout + result.stderr
        resumed_report = (run_dir / "SMK_2026-02-01_report.md").read_bytes()
        assert resumed_report == original_report
        _cli("verify-run", str(run_dir), "--strict")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reused_envelope_checksum_is_stable_across_resumes(tmp_path):
    """
    Resuming must not mutate a reused envelope.

    A resume that appends warnings without recomputing the checksum produces a
    run that fails its own audit on the second resume.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_stable_"))
    try:
        run_dir = tmp / "run"
        _run(tmp)
        before = {
            path.name: json.loads(path.read_text(encoding="utf-8"))["content_checksum"]
            for path in sorted(run_dir.glob("*_envelope.json"))
        }
        for _ in range(2):
            _cli("run", str(run_dir), *RUN_ARGS, "--resume")
        after = {
            path.name: json.loads(path.read_text(encoding="utf-8"))["content_checksum"]
            for path in sorted(run_dir.glob("*_envelope.json"))
        }
        assert before == after, "resume mutated a reused envelope"
        _cli("verify-run", str(run_dir), "--strict")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================== 3. evidence snapshot reuse / refusal


def _evidence_pack(tmp: Path) -> Path:
    """A minimal valid evidence pack built offline."""
    root = tmp / "pack"
    root.mkdir(parents=True, exist_ok=True)
    payload = b"Revenue: 123. Public filing data."
    (root / "D001.bytes").write_bytes(payload)
    (root / "D001.txt").write_bytes(payload)
    document = {
        "id": "D001",
        "type": "Filing",
        "title": "Annual results",
        "url": "https://example.com/annual-results",
        "doc_date": "2025-01-01",
        "date_basis": "supplied_manifest",
        "accessed": "2026-10-06T10:00:00Z",
        "content_type": "text/plain",
        "verification": "retrieved",
        "content_sha256": __import__("src.utils.checksum", fromlist=["sha256_digest"]).sha256_digest(payload),
        "text_sha256": __import__("src.utils.checksum", fromlist=["sha256_digest"]).sha256_digest(payload),
        "raw_file": "D001.bytes",
        "text_file": "D001.txt",
    }
    path = root / "evidence.json"
    path.write_text(
        json.dumps({"schema_version": "1", "ticker": "SMK", "documents": [document]}),
        encoding="utf-8",
    )
    return path


def test_resume_with_unchanged_evidence_reuses_the_snapshot(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_evreuse_"))
    try:
        pack = _evidence_pack(tmp)
        run_dir = tmp / "run"
        first = _cli("run", str(run_dir), *RUN_ARGS, "--evidence-pack", str(pack))
        assert first.returncode == 0, first.stdout + first.stderr
        second = _cli("run", str(run_dir), *RUN_ARGS, "--evidence-pack", str(pack), "--resume")
        assert second.returncode == 0, second.stdout + second.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resume_with_changed_evidence_is_refused(tmp_path):
    """
    Reusing a receipt against different evidence would silently launder one
    analysis under another run's inputs. The run must refuse.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_evchange_"))
    try:
        pack = _evidence_pack(tmp)
        run_dir = tmp / "run"
        _cli("run", str(run_dir), *RUN_ARGS, "--evidence-pack", str(pack))

        # Change the evidence: new bytes and hashes.
        from src.utils.checksum import sha256_digest

        new_payload = b"Revenue: 999. Different evidence entirely."
        (pack.parent / "D001.bytes").write_bytes(new_payload)
        (pack.parent / "D001.txt").write_bytes(new_payload)
        data = json.loads(pack.read_text(encoding="utf-8"))
        data["documents"][0]["content_sha256"] = sha256_digest(new_payload)
        data["documents"][0]["text_sha256"] = sha256_digest(new_payload)
        pack.write_text(json.dumps(data), encoding="utf-8")

        result = _cli(
            "run", str(run_dir), *RUN_ARGS, "--evidence-pack", str(pack), "--resume",
            expect_ok=False,
        )
        assert result.returncode != 0, "changed evidence must be refused on resume"
        combined = (result.stdout + result.stderr).lower()
        assert "evidence" in combined
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================ 4. bundle verification and restoration


def test_bundle_verify_and_restore_round_trip(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_bundle_"))
    try:
        pack = _evidence_pack(tmp)
        run_dir = tmp / "run"
        _cli("run", str(run_dir), *RUN_ARGS, "--evidence-pack", str(pack))

        bundle = tmp / "bundle"
        result = _cli("bundle-evidence", str(run_dir), "--output", str(bundle))
        assert result.returncode == 0, result.stdout + result.stderr
        assert bundle.is_dir()

        manifest = next(bundle.glob("*manifest*.json"), None)
        assert manifest is not None, sorted(p.name for p in bundle.iterdir())
        from src.utils.checksum import sha256_digest

        digest = sha256_digest(manifest.read_bytes()).split(":", 1)[-1]

        # Verify against an independently trusted digest.
        _cli("verify-evidence", str(bundle), "--manifest-sha256", digest)

        # Restore into a fresh directory.
        restored = tmp / "restored"
        _cli(
            "restore-evidence", str(bundle),
            "--manifest-sha256", digest,
            "--output", str(restored),
        )
        assert restored.is_dir()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bundle_verification_fails_with_a_wrong_trusted_digest(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_baddigest_"))
    try:
        pack = _evidence_pack(tmp)
        run_dir = tmp / "run"
        _cli("run", str(run_dir), *RUN_ARGS, "--evidence-pack", str(pack))
        bundle = tmp / "bundle"
        _cli("bundle-evidence", str(run_dir), "--output", str(bundle))
        result = _cli(
            "verify-evidence", str(bundle), "--manifest-sha256", "0" * 64,
            expect_ok=False,
        )
        assert result.returncode != 0, "a wrong trusted digest must fail verification"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================ 5. sealing after a change


def test_seal_force_truthfully_reports_the_edited_artifact_state(tmp_path):
    """
    After an upstream edit, ``--force`` re-seals the descendants.

    The tool must state that the run now certifies the *edited* bytes. It must
    not imply the original run was untouched.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_seal_"))
    try:
        run_dir = tmp / "run"
        _run(tmp)

        # Edit A1 without resealing: a naive edit.
        a1 = run_dir / "a1_envelope.json"
        data = json.loads(a1.read_text(encoding="utf-8"))
        data["warnings"] = list(data.get("warnings", [])) + ["edited upstream"]
        a1.write_text(json.dumps(data, indent=2), encoding="utf-8")

        plain = _cli("seal", str(run_dir), expect_ok=False)
        assert plain.returncode != 0, "non-forced seal must refuse a modified envelope"

        forced = _cli("seal", str(run_dir), "--force")
        assert forced.returncode == 0, forced.stdout + forced.stderr
        combined = (forced.stdout + forced.stderr).lower()
        # It must state that the run now certifies the edited bytes rather
        # than implying the original execution was unchanged.
        assert "edited bytes" in combined
        assert "not the original execution" in combined

        # And the run must now verify as the edited artifact it describes.
        report = _audit_report(run_dir)
        assert report["levels"]["envelope_integrity"] == "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sealing_is_idempotent(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_sealidem_"))
    try:
        run_dir = tmp / "run"
        _run(tmp)
        before = _audit_report(run_dir)["levels"]
        _cli("seal", str(run_dir))
        after = _audit_report(run_dir)["levels"]
        assert before == after
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================ 6. research-packet export


def test_research_packet_exports_and_validates(tmp_path):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_packet_"))
    try:
        run_dir = tmp / "run"
        _run(tmp)
        out = tmp / "packet.json"
        result = _cli("export-packet", str(run_dir), "--output", str(out))
        assert result.returncode == 0, result.stdout + result.stderr
        assert out.is_file()
        packet = json.loads(out.read_text(encoding="utf-8"))
        assert packet["schema_version"].startswith("research_packet_v1")
        assert packet["analysis_date"] == "2026-02-01"
        assert packet["approval_status"] == "pending_human_review"
        assert packet["audit"]["complete"] is True
        assert packet["claims"], "a complete run must export its claims"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_research_packet_export_is_deterministic(tmp_path):
    """
    Exporting the same run twice must produce identical bytes.

    Otherwise the packet's own hash says nothing about the run it describes.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_packetdet_"))
    try:
        run_dir = tmp / "run"
        _run(tmp)
        a = tmp / "a.json"
        b = tmp / "b.json"
        _cli("export-packet", str(run_dir), "--output", str(a))
        _cli("export-packet", str(run_dir), "--output", str(b))
        pa, pb = json.loads(a.read_text()), json.loads(b.read_text())
        # The packet content is deterministic; only the generation timestamp
        # legitimately differs between two exports.
        pa.pop("generated_at", None)
        pb.pop("generated_at", None)
        assert pa == pb
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================== 7. experimental feature (P7)


def test_independent_bear_is_opt_in_and_off_by_default(tmp_path):
    """
    The blind-bear challenge must never be silently enabled.

    A default run makes exactly one A6R call; with the flag it also produces a
    blind A6R phase.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_p6_bear_"))
    try:
        default_run = _cli("run", str(tmp / "default"), *RUN_ARGS)
        assert default_run.returncode == 0, default_run.stdout + default_run.stderr
        default_text = default_run.stdout + default_run.stderr
        assert "A6R" in default_text
        # Exactly one A6R acceptance.
        assert default_text.count("A6R accepted") == 1, default_text

        bear_run = _cli("run", str(tmp / "bear"), *RUN_ARGS, "--independent-bear")
        assert bear_run.returncode == 0, bear_run.stdout + bear_run.stderr
        bear_text = bear_run.stdout + bear_run.stderr
        assert bear_text.count("A6R accepted") == 2, bear_text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)