"""Tests for portable evidence bundle verification, packaging, and restoration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from src.evidence import EvidencePack
from src.evidence_bundle import (
    DEFAULT_BUNDLE_MAX_TOTAL_BYTES,
    DEMO_BUNDLE_MAX_TOTAL_BYTES,
    DEFAULT_EXTRACTION_VERSION,
    build_bundle,
    check_extraction_version,
    restore_bundle,
    verify_bundle,
)
from src.utils.checksum import sha256_digest


def make_test_pack(
    tmp_path: Path,
    text: str = "Quarterly results: Revenue 500, OCF 120.",
    ticker: str = "TEST",
    include_attachment: bool = False,
) -> Path:
    """Helper to create a valid synthetic EvidencePack offline without network access."""
    pack_dir = tmp_path / ("source_pack_att" if include_attachment else "source_pack")
    pack_dir.mkdir(parents=True, exist_ok=True)

    payload = text.encode("utf-8")
    raw_path = pack_dir / "D001.bytes"
    text_path = pack_dir / "D001.txt"
    raw_path.write_bytes(payload)
    text_path.write_bytes(payload)

    document = {
        "id": "D001",
        "type": "Filing",
        "title": "Form 10-Q Results",
        "url": "https://example.com/sec/filing-10q",
        "doc_date": "2025-01-15",
        "date_basis": "supplied_manifest",
        "accessed": "2026-10-06T10:00:00Z",
        "content_type": "text/plain",
        "verification": "retrieved",
        "content_sha256": sha256_digest(payload),
        "text_sha256": sha256_digest(payload),
        "raw_file": "D001.bytes",
        "text_file": "D001.txt",
    }

    attachments = []
    if include_attachment:
        att_bytes = b'{"accepted": ["https://example.com"], "limitations": []}'
        att_file = pack_dir / "web_research_trace.json"
        att_file.write_bytes(att_bytes)
        attachments.append({
            "file": "web_research_trace.json",
            "sha256": sha256_digest(att_bytes),
            "kind": "gemini_web_research",
        })

    evidence_json = pack_dir / "evidence.json"
    evidence_json.write_text(
        json.dumps({
            "schema_version": "1",
            "ticker": ticker,
            "analysis_date": "2025-01-15",
            "documents": [document],
            "attachments": attachments,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence_json


def test_build_bundle_and_verify_valid(tmp_path: Path):
    """build_bundle creates a compliant bundle directory verified by verify_bundle."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle_out"

    build_res = build_bundle(ev_json, bundle_out, extraction_version="2.1")
    assert "manifest_sha256" in build_res
    assert build_res["verified_files"] == 3  # evidence.json, D001.bytes, D001.txt
    assert build_res["ticker"] == "TEST"
    assert build_res["extraction_version"] == "2.1"

    # Verify bundle against the trusted manifest SHA-256
    verify_res = verify_bundle(bundle_out, build_res["manifest_sha256"])
    assert verify_res["verified_files"] == 3
    assert verify_res["verified_bytes"] == build_res["total_bytes"]
    assert verify_res["manifest_sha256"] == build_res["manifest_sha256"]


def test_build_bundle_with_attachments(tmp_path: Path):
    """build_bundle includes attachments properly and verify_bundle succeeds."""
    ev_json = make_test_pack(tmp_path, include_attachment=True)
    bundle_out = tmp_path / "bundle_att"

    build_res = build_bundle(ev_json, bundle_out)
    assert build_res["verified_files"] == 4  # evidence.json, D001.bytes, D001.txt, web_research_trace.json

    verify_res = verify_bundle(bundle_out, build_res["manifest_sha256"])
    assert verify_res["verified_files"] == 4


def test_verify_missing_body_file(tmp_path: Path):
    """Missing listed evidence file causes verification failure."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    # Delete one of the body files
    (bundle_out / "D001.bytes").unlink()

    with pytest.raises(ValueError, match=r"missing or escaping evidence file|contains missing or unlisted"):
        verify_bundle(bundle_out, res["manifest_sha256"])


def test_verify_modified_file_bytes(tmp_path: Path):
    """Tampering with file contents causes SHA-256 hash or size mismatch failure."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    # Overwrite file with different bytes of same length
    orig = (bundle_out / "D001.bytes").read_bytes()
    tampered = b"X" * len(orig)
    (bundle_out / "D001.bytes").write_bytes(tampered)

    with pytest.raises(ValueError, match="evidence hash mismatch"):
        verify_bundle(bundle_out, res["manifest_sha256"])


def test_verify_modified_extracted_text(tmp_path: Path):
    """Modifying extracted text file fails bundle hash verification."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    (bundle_out / "D001.txt").write_text("fabricated extraction", encoding="utf-8")

    with pytest.raises(ValueError, match="evidence (size|hash) mismatch"):
        verify_bundle(bundle_out, res["manifest_sha256"])


def test_authenticity_rewritten_bundle_fails_old_trusted_root(tmp_path: Path):
    """Crucial security test: A self-consistent rewritten bundle fails when checked against OLD trusted root."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)
    trusted_root = res["manifest_sha256"]

    # Tamper with content and re-calculate manifest hashes to make bundle internally consistent
    new_payload = b"Tampered financial claim"
    (bundle_out / "D001.bytes").write_bytes(new_payload)
    (bundle_out / "D001.txt").write_bytes(new_payload)

    manifest_file = bundle_out / "manifest.json"
    manifest_doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    for rec in manifest_doc["files"]:
        if rec["path"] in ("D001.bytes", "D001.txt"):
            rec["size"] = len(new_payload)
            rec["sha256"] = hashlib.sha256(new_payload).hexdigest()

    # Rewrite manifest with updated hashes
    new_manifest_bytes = json.dumps(manifest_doc, indent=2).encode("utf-8") + b"\n"
    manifest_file.write_bytes(new_manifest_bytes)

    # Verification against the attacker's rewritten manifest hash would pass internally,
    # but verification against the caller's independently trusted root MUST fail.
    with pytest.raises(ValueError, match="manifest does not match the independently trusted root"):
        verify_bundle(bundle_out, trusted_root)


def test_verify_duplicate_file_record(tmp_path: Path):
    """Duplicate file records in manifest.json are rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    build_bundle(ev_json, bundle_out)

    manifest_file = bundle_out / "manifest.json"
    manifest_doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_doc["files"].append(manifest_doc["files"][0])  # duplicate first file

    manifest_bytes = json.dumps(manifest_doc, indent=2).encode("utf-8") + b"\n"
    manifest_file.write_bytes(manifest_bytes)
    trusted_hash = hashlib.sha256(manifest_bytes).hexdigest()

    with pytest.raises(ValueError, match="duplicate file record"):
        verify_bundle(bundle_out, trusted_hash)


def test_verify_extra_unlisted_file(tmp_path: Path):
    """An extra unlisted file on disk is rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    (bundle_out / "unlisted.bin").write_bytes(b"payload")

    with pytest.raises(ValueError, match="contains missing or unlisted files"):
        verify_bundle(bundle_out, res["manifest_sha256"])


@pytest.mark.parametrize("bad_path,match_msg", [
    ("../escape.txt", "unsafe or noncanonical file path"),
    ("sub/../../escape.txt", "unsafe or noncanonical file path"),
    ("/abs/path.txt", "file path must be a nonempty relative POSIX path|unsafe or noncanonical"),
    ("sub\\backslashes.txt", "file path must be a nonempty relative POSIX path"),
    (".", "unsafe or noncanonical file path"),
    ("manifest.json", "unsafe or noncanonical file path"),
])
def test_verify_path_traversal_and_invalid_paths(tmp_path: Path, bad_path: str, match_msg: str):
    """Path traversal and malformed paths in manifest.json are rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    build_bundle(ev_json, bundle_out)

    manifest_file = bundle_out / "manifest.json"
    manifest_doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_doc["files"].append({
        "path": bad_path,
        "size": 10,
        "sha256": "0" * 64,
    })

    manifest_bytes = json.dumps(manifest_doc, indent=2).encode("utf-8") + b"\n"
    manifest_file.write_bytes(manifest_bytes)
    trusted_hash = hashlib.sha256(manifest_bytes).hexdigest()

    with pytest.raises(ValueError, match=match_msg):
        verify_bundle(bundle_out, trusted_hash)


def test_verify_symlinks_rejected(tmp_path: Path):
    """Symlinks anywhere in the bundle tree are rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    target = bundle_out / "D001.bytes"
    link = bundle_out / "symlink_entry.bytes"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlinks are not allowed in an evidence bundle"):
        verify_bundle(bundle_out, res["manifest_sha256"])


def test_verify_symlinked_manifest_rejected(tmp_path: Path):
    """A symlinked manifest.json is rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    manifest = bundle_out / "manifest.json"
    outside = tmp_path / "outside_manifest.json"
    outside.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(outside)

    with pytest.raises(ValueError, match="missing, linked or oversized manifest"):
        verify_bundle(bundle_out, res["manifest_sha256"])


def test_verify_symlinked_root_rejected(tmp_path: Path):
    """A bundle root that is a symlink is rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    symlink_root = tmp_path / "symlink_bundle"
    symlink_root.symlink_to(bundle_out)

    with pytest.raises(ValueError, match="bundle root must be a real directory"):
        verify_bundle(symlink_root, res["manifest_sha256"])


def test_verify_manifest_validation_failures(tmp_path: Path):
    """Invalid manifest structures, missing manifest, or bad expected hash strings are rejected."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out)

    # 1. Invalid trusted hash format
    with pytest.raises(ValueError, match="expected a lowercase SHA-256 digest"):
        verify_bundle(bundle_out, "INVALID_HASH")
    with pytest.raises(ValueError, match="expected a lowercase SHA-256 digest"):
        verify_bundle(bundle_out, "a" * 63)

    # 2. Missing manifest
    manifest = bundle_out / "manifest.json"
    manifest_content = manifest.read_bytes()
    manifest.unlink()
    with pytest.raises(ValueError, match="missing, linked or oversized manifest"):
        verify_bundle(bundle_out, res["manifest_sha256"])
    manifest.write_bytes(manifest_content)

    # 3. Oversized manifest (> 1 MiB)
    oversized = manifest_content + b" " * (1024 * 1024 + 10)
    manifest.write_bytes(oversized)
    oversized_hash = hashlib.sha256(oversized).hexdigest()
    with pytest.raises(ValueError, match="missing, linked or oversized manifest"):
        verify_bundle(bundle_out, oversized_hash)
    manifest.write_bytes(manifest_content)

    # 4. Exceeds total byte budget
    with pytest.raises(ValueError, match="bundle exceeds the total byte budget"):
        verify_bundle(bundle_out, res["manifest_sha256"], max_total_bytes=10)

    # 5. Invalid schema version
    manifest_doc = json.loads(manifest_content.decode("utf-8"))
    manifest_doc["schema_version"] = 2
    raw = json.dumps(manifest_doc).encode("utf-8")
    manifest.write_bytes(raw)
    with pytest.raises(ValueError, match="unsupported bundle schema"):
        verify_bundle(bundle_out, hashlib.sha256(raw).hexdigest())
    manifest.write_bytes(manifest_content)

    # 6. Empty files list
    manifest_doc = json.loads(manifest_content.decode("utf-8"))
    manifest_doc["files"] = []
    raw = json.dumps(manifest_doc).encode("utf-8")
    manifest.write_bytes(raw)
    with pytest.raises(ValueError, match=r"bundle requires between 1 and 256 files"):
        verify_bundle(bundle_out, hashlib.sha256(raw).hexdigest())
    manifest.write_bytes(manifest_content)


def test_extraction_version_helper(tmp_path: Path):
    """check_extraction_version accurately reports matches and extraction_mismatch."""
    ev_json = make_test_pack(tmp_path)
    bundle_out = tmp_path / "bundle"
    build_bundle(ev_json, bundle_out, extraction_version="2.1")

    # Matching version
    matched, status = check_extraction_version(bundle_out, "2.1")
    assert matched is True
    assert status == "source_bytes_verified"

    # Mismatched version
    matched, status = check_extraction_version(bundle_out, "1.0")
    assert matched is False
    assert status == "extraction_mismatch"

    # Missing manifest returns source_bytes_unavailable
    matched, status = check_extraction_version(tmp_path / "nonexistent", "2.1")
    assert matched is False
    assert status == "source_bytes_unavailable"


def test_restore_bundle_into_fresh_directory(tmp_path: Path):
    """restore_bundle verifies bundle and returns a functional EvidencePack."""
    ev_json = make_test_pack(tmp_path, text="Restored evidence content.", ticker="RESTORE")
    bundle_out = tmp_path / "bundle"
    res = build_bundle(ev_json, bundle_out, extraction_version="2.1")

    restore_target = tmp_path / "restored_pack"
    restored_pack = restore_bundle(
        bundle_out,
        restore_target,
        res["manifest_sha256"],
        expected_extraction_version="2.1",
    )

    assert isinstance(restored_pack, EvidencePack)
    assert restored_pack.data["ticker"] == "RESTORE"
    assert len(restored_pack.documents) == 1
    assert restored_pack.sources()[0]["id"] == "D001"

    # Check extraction version mismatch on restore
    restore_target_2 = tmp_path / "restored_pack_2"
    with pytest.raises(ValueError, match="extraction version mismatch: extraction_mismatch"):
        restore_bundle(
            bundle_out,
            restore_target_2,
            res["manifest_sha256"],
            expected_extraction_version="999.0",
        )


def test_build_bundle_refuses_to_guess_analysis_date_when_missing(tmp_path: Path):
    """build_bundle refuses to infer cutoff from newest document if missing."""
    ev_json = make_test_pack(tmp_path, text="No cutoff", ticker="NOCUT")
    # Strip analysis_date from evidence.json
    data = json.loads(ev_json.read_text(encoding="utf-8"))
    data.pop("analysis_date", None)
    ev_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    bundle_out = tmp_path / "bundle_fail"
    with pytest.raises(ValueError, match="refusing to guess analysis cutoff"):
        build_bundle(ev_json, bundle_out)

    # But succeeds if explicitly passed
    bundle_ok = tmp_path / "bundle_ok"
    res = build_bundle(ev_json, bundle_ok, analysis_date="2025-01-15")
    assert res["manifest_sha256"]


def test_restore_bundle_fails_without_analysis_date_and_succeeds_with_flag(tmp_path: Path):
    """restore_bundle requires analysis_date in manifest or explicit override."""
    ev_json = make_test_pack(tmp_path, ticker="NOAN")
    bundle_out = tmp_path / "bundle_legacy"
    res = build_bundle(ev_json, bundle_out)

    # Tamper with bundle manifest to remove analysis_date (simulating legacy bundle)
    manifest_path = bundle_out / "manifest.json"
    m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    m_data.pop("analysis_date", None)
    m_bytes = json.dumps(m_data, indent=2).encode("utf-8") + b"\n"
    manifest_path.write_bytes(m_bytes)
    m_sha = hashlib.sha256(m_bytes).hexdigest()

    target = tmp_path / "restore_fail"
    with pytest.raises(ValueError, match="manifest is missing required 'analysis_date'"):
        restore_bundle(bundle_out, target, m_sha)

    # Supplying analysis_date explicitly succeeds
    target_ok = tmp_path / "restore_ok"
    pack = restore_bundle(bundle_out, target_ok, m_sha, analysis_date="2025-01-15")
    assert pack.analysis_date == "2025-01-15"


def test_verify_and_restore_evidence_share_default_512mib_budget(tmp_path: Path):
    """verify_bundle and restore_bundle default to DEFAULT_BUNDLE_MAX_TOTAL_BYTES (512 MiB)."""
    assert verify_bundle.__kwdefaults__["max_total_bytes"] == DEFAULT_BUNDLE_MAX_TOTAL_BYTES
    assert restore_bundle.__kwdefaults__["max_total_bytes"] == DEFAULT_BUNDLE_MAX_TOTAL_BYTES

