"""Portable evidence bundle verification, packaging, and restoration.

Verifies a bounded local evidence directory against an independently trusted
manifest SHA-256 digest.

IMPORTANT BOUNDARIES AND SECURITY PROPERTIES:
1. Authenticity requires a trusted root OUTSIDE the bundle:
   A self-consistent rewritten bundle can carry its own rewritten hashes.
   Therefore, a self-hash or co-located hash alone is NOT proof of authorship or
   authenticity. Verification is only meaningful when compared against an
   independently trusted manifest SHA-256 digest (e.g., a previously recorded
   run record, verified release manifest, or out-of-band signed artifact).
2. Hash integrity is not factual truth:
   A valid SHA-256 digest proves only that the supplied bytes match the
   recorded digest. It does not verify the truth or reliability of the claims
   contained within those documents. Missing bytes cannot be recreated from
   hashes alone.
3. Not a secure archive extractor:
   This module operates on an existing quiescent local directory tree. It is not
   a tar/zip decompression safety filter or a signature verification engine.
4. Not protected against concurrent hostile mutation:
   Assumes the directory tree is not being concurrently modified by a hostile
   process. Defending against TOCTOU race conditions requires OS-level
   isolation (e.g., read-only mounts, sandboxes, or immutable containers).
5. Source extraction and semantic validation remain separate checks:
   Verifying bundle byte integrity confirms file presence and digest match.
   Re-deriving parsed text and evaluating research claims require subsequent
   processing by EvidencePack and semantic audit passes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

if TYPE_CHECKING:
    from src.evidence import EvidencePack

# Production total size budget: 512 MiB for complete supported pack
DEFAULT_BUNDLE_MAX_TOTAL_BYTES: int = 512 * 1024 * 1024

# Demo default budget matching reference verifier: 64 MiB
DEMO_BUNDLE_MAX_TOTAL_BYTES: int = 64 * 1024 * 1024

# Maximum number of files permitted in a bundle
MAX_FILES_IN_BUNDLE: int = 256

# Maximum manifest file size (1 MiB)
MAX_MANIFEST_BYTES: int = 1024 * 1024

# Default extraction version identity
DEFAULT_EXTRACTION_VERSION: str = "2.1"


def _validate_sha256(value: object) -> str:
    """Validate that a value is a lowercase 64-character SHA-256 hex string."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("expected a lowercase SHA-256 digest")
    return value


def verify_bundle(
    root: Union[Path, str],
    expected_manifest_sha256: str,
    *,
    max_total_bytes: int = DEMO_BUNDLE_MAX_TOTAL_BYTES,
) -> Dict[str, Any]:
    """Check listed files, sizes, hashes, containment and absence of extra files.

    Args:
        root: Path to the bundle directory.
        expected_manifest_sha256: 64-char hex SHA-256 digest from a trusted out-of-band source.
        max_total_bytes: Upper bound on the sum of all file sizes (defaults to 64 MiB demo limit).

    Returns:
        dict with verified_files count, verified_bytes total, and manifest_sha256.

    Raises:
        ValueError: On missing/invalid files, tampered hashes, unlisted files,
                    symlinks, directory traversal, or manifest hash mismatch.
    """
    _validate_sha256(expected_manifest_sha256)
    if isinstance(max_total_bytes, bool) or not isinstance(max_total_bytes, int) or max_total_bytes < 1:
        raise ValueError("max_total_bytes must be a positive integer")

    bundle_root = Path(root)
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise ValueError("bundle root must be a real directory")
    bundle_root = bundle_root.resolve()

    try:
        manifest = bundle_root / "manifest.json"
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("missing, linked or oversized manifest")

        raw = manifest.read_bytes()
        actual_manifest_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_manifest_sha256 != expected_manifest_sha256:
            raise ValueError("manifest does not match the independently trusted root")

        doc = json.loads(raw)
        if not isinstance(doc, dict) or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
            raise ValueError("unsupported bundle schema")

        records = doc.get("files")
        if not isinstance(records, list) or not (1 <= len(records) <= MAX_FILES_IN_BUNDLE):
            raise ValueError(f"bundle requires between 1 and {MAX_FILES_IN_BUNDLE} files")

        actual_files = set()
        for path in bundle_root.rglob("*"):
            if path.is_symlink():
                raise ValueError("symlinks are not allowed in an evidence bundle")
            if path.is_file():
                actual_files.add(path.relative_to(bundle_root).as_posix())
            elif not path.is_dir():
                raise ValueError("special filesystem entries are not allowed")

        listed_files = set()
        total_bytes = 0

        for record in records:
            if not isinstance(record, dict):
                raise ValueError("malformed file record")

            relative = record.get("path")
            if not isinstance(relative, str) or not relative or "\\" in relative:
                raise ValueError("file path must be a nonempty relative POSIX path")

            posix = PurePosixPath(relative)
            if (
                posix.is_absolute()
                or ".." in posix.parts
                or relative != posix.as_posix()
                or relative in (".", "manifest.json")
            ):
                raise ValueError("unsafe or noncanonical file path")

            if relative in listed_files:
                raise ValueError("duplicate file record")
            listed_files.add(relative)

            expected_size = record.get("size")
            if type(expected_size) is not int or expected_size < 0:
                raise ValueError("file size must be a nonnegative integer")

            expected_hash = _validate_sha256(record.get("sha256"))
            total_bytes += expected_size
            if total_bytes > max_total_bytes:
                raise ValueError("bundle exceeds the total byte budget")

            path = bundle_root.joinpath(*posix.parts)
            if not path.resolve().is_relative_to(bundle_root) or not path.is_file():
                raise ValueError(f"missing or escaping evidence file: {relative}")

            if path.stat().st_size != expected_size:
                raise ValueError(f"evidence size mismatch: {relative}")

            hasher = hashlib.sha256()
            streamed_size = 0
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    streamed_size += len(chunk)
                    if streamed_size > expected_size:
                        raise ValueError("file changed during verification")
                    hasher.update(chunk)

            if streamed_size != expected_size or hasher.hexdigest() != expected_hash:
                raise ValueError(f"evidence hash mismatch: {relative}")

        if actual_files != (listed_files | {"manifest.json"}):
            raise ValueError("bundle contains missing or unlisted files")

        return {
            "verified_files": len(listed_files),
            "verified_bytes": total_bytes,
            "manifest_sha256": expected_manifest_sha256,
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"bundle cannot be verified: {exc}") from exc


def build_bundle(
    evidence_json_path: Union[Path, str],
    output_dir: Union[Path, str],
    *,
    ticker: Optional[str] = None,
    analysis_date: Optional[str] = None,
    extraction_version: str = DEFAULT_EXTRACTION_VERSION,
) -> Dict[str, Any]:
    """Package an existing EvidencePack into a self-contained portable bundle directory.

    Copies raw byte files, extracted text files, attachments, and the evidence manifest.
    Computes exact byte sizes and SHA-256 digests for all files and generates
    manifest.json with top-level metadata.

    Args:
        evidence_json_path: Path to the evidence.json file of an existing EvidencePack.
        output_dir: Path to the destination bundle directory (must not exist beforehand).
        ticker: Optional ticker override; if None, inferred from evidence.json.
        analysis_date: Optional analysis date override; if None, inferred from documents.
        extraction_version: Extraction pipeline version identifier.

    Returns:
        dict containing manifest_sha256, verified_files, total_bytes, ticker, and metadata.
    """
    ev_path = Path(evidence_json_path).resolve()
    if not ev_path.is_file():
        raise ValueError(f"evidence.json not found: {evidence_json_path}")

    raw_json = ev_path.read_text(encoding="utf-8")
    ev_data = json.loads(raw_json)

    if ticker is None:
        ticker = str(ev_data.get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError("ticker not specified and cannot be inferred from evidence.json")

    docs = ev_data.get("documents", [])
    if analysis_date is None:
        if not docs:
            raise ValueError("analysis_date not specified and cannot be inferred from empty documents")
        analysis_date = max(str(d["doc_date"]) for d in docs)

    # Lazy import to avoid circular dependencies
    from src.evidence import EvidencePack

    pack = EvidencePack(ev_path, ticker=ticker, analysis_date=analysis_date)

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)

    files_records: list[dict[str, Any]] = []

    # 1. Copy evidence.json
    ev_target = out_dir / "evidence.json"
    shutil.copyfile(ev_path, ev_target)
    ev_bytes = ev_target.read_bytes()
    files_records.append({
        "path": "evidence.json",
        "size": len(ev_bytes),
        "sha256": hashlib.sha256(ev_bytes).hexdigest(),
    })

    # 2. Copy document raw & text files
    for doc in pack.documents:
        for key in ("raw_file", "text_file"):
            rel_name = doc[key]
            src_file = pack._file(rel_name)
            dst_file = out_dir / rel_name
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_file, dst_file)
            content = dst_file.read_bytes()
            files_records.append({
                "path": PurePosixPath(rel_name).as_posix(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })

    # 3. Copy attachments
    for attachment in pack.attachments:
        rel_name = attachment["file"]
        src_file = pack._file(rel_name)
        dst_file = out_dir / rel_name
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_file, dst_file)
        content = dst_file.read_bytes()
        files_records.append({
            "path": PurePosixPath(rel_name).as_posix(),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })

    # Deduplicate files_records by path if any overlap exists, and sort deterministically
    seen_paths = set()
    unique_records = []
    for rec in files_records:
        if rec["path"] not in seen_paths:
            seen_paths.add(rec["path"])
            unique_records.append(rec)
    unique_records.sort(key=lambda r: r["path"])

    total_bytes = sum(r["size"] for r in unique_records)

    manifest_doc = {
        "schema_version": 1,
        "ticker": ticker.upper(),
        "pack_checksum": pack.checksum,
        "extraction_version": str(extraction_version),
        "analysis_date": str(analysis_date),
        "documents": pack.documents,
        "attachments": pack.attachments,
        "files": unique_records,
    }

    manifest_bytes = json.dumps(manifest_doc, indent=2).encode("utf-8") + b"\n"
    manifest_file = out_dir / "manifest.json"
    manifest_file.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    return {
        "manifest_sha256": manifest_sha256,
        "verified_files": len(unique_records),
        "files_count": len(unique_records),
        "total_bytes": total_bytes,
        "extraction_version": str(extraction_version),
        "ticker": ticker.upper(),
        "pack_checksum": pack.checksum,
        "bundle_dir": str(out_dir),
    }


def check_extraction_version(
    manifest_or_bundle: Union[Dict[str, Any], Path, str],
    expected_version: str = DEFAULT_EXTRACTION_VERSION,
) -> Tuple[bool, str]:
    """Compare a manifest or bundle directory's extraction_version against an expected version.

    Args:
        manifest_or_bundle: Parsed manifest dict, Path to manifest.json, or Path to bundle root.
        expected_version: Expected extraction pipeline version string.

    Returns:
        (True, "source_bytes_verified") if matching.
        (False, "extraction_mismatch") if different or missing.
    """
    if isinstance(manifest_or_bundle, dict):
        manifest_data = manifest_or_bundle
    else:
        path = Path(manifest_or_bundle)
        if path.is_dir():
            manifest_file = path / "manifest.json"
        else:
            manifest_file = path
        if not manifest_file.is_file():
            return False, "source_bytes_unavailable"
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            return False, "semantic_failure"

    version = manifest_data.get("extraction_version")
    if version is not None and str(version) == str(expected_version):
        return True, "source_bytes_verified"
    return False, "extraction_mismatch"


def restore_bundle(
    bundle_dir: Union[Path, str],
    target_dir: Union[Path, str],
    expected_manifest_sha256: str,
    *,
    expected_extraction_version: Optional[str] = None,
    max_total_bytes: int = DEFAULT_BUNDLE_MAX_TOTAL_BYTES,
) -> EvidencePack:
    """Verify bundle authenticity, check extraction version, and restore into EvidencePack.

    1. Validates bundle against expected_manifest_sha256.
    2. Checks extraction_version if expected_extraction_version is supplied.
    3. Copies verified files into target_dir.
    4. Instantiates EvidencePack to run full text re-derivation and checksum checks.

    Args:
        bundle_dir: Source bundle directory.
        target_dir: Clean destination directory.
        expected_manifest_sha256: Independently trusted manifest SHA-256.
        expected_extraction_version: Optional version to check.
        max_total_bytes: Total byte limit for verification.

    Returns:
        Instantiated and verified EvidencePack instance.
    """
    bundle_path = Path(bundle_dir).resolve()
    target_path = Path(target_dir).resolve()

    # Step 1: Verify bundle integrity against trusted root
    verify_bundle(bundle_path, expected_manifest_sha256, max_total_bytes=max_total_bytes)

    # Step 2: Read verified manifest and check extraction version if requested
    manifest_data = json.loads((bundle_path / "manifest.json").read_text(encoding="utf-8"))
    if expected_extraction_version is not None:
        matched, status = check_extraction_version(manifest_data, expected_extraction_version)
        if not matched:
            raise ValueError(f"extraction version mismatch: {status}")

    # Step 3: Copy verified files to clean target directory
    target_path.mkdir(parents=True, exist_ok=False)
    for record in manifest_data["files"]:
        rel = PurePosixPath(record["path"])
        src_file = bundle_path.joinpath(*rel.parts)
        dst_file = target_path.joinpath(*rel.parts)
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_file, dst_file)

    # Step 4: Instantiate EvidencePack for re-extraction verification
    from src.evidence import EvidencePack

    evidence_json = target_path / "evidence.json"
    if not evidence_json.is_file():
        raise ValueError("restored bundle is missing evidence.json")

    ticker = manifest_data.get("ticker") or ""
    analysis_date = manifest_data.get("analysis_date")
    if not analysis_date:
        docs = manifest_data.get("documents", [])
        analysis_date = max(d["doc_date"] for d in docs) if docs else "2099-12-31"

    return EvidencePack(evidence_json, ticker=ticker, analysis_date=analysis_date)
