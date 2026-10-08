"""Verify a bounded local evidence directory against an independently trusted hash.

Not an archive extractor or a signature verifier. Assumes a quiescent local
filesystem; concurrent hostile filesystem mutation requires stronger OS-level
isolation. Source extraction and semantic validation remain separate checks.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def _hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('expected a lowercase SHA-256 digest')
    return value


def verify_bundle(root: Path, expected_manifest_sha256: str, *,
                  max_total_bytes: int = 64 * 1024 * 1024) -> dict:
    """Check listed files, sizes, hashes, containment and absence of extra files."""
    _hash(expected_manifest_sha256)
    if isinstance(max_total_bytes, bool) or not isinstance(max_total_bytes, int) or max_total_bytes < 1:
        raise ValueError('max_total_bytes must be a positive integer')
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('bundle root must be a real directory')
    root = root.resolve()
    try:
        manifest = root / 'manifest.json'
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
            raise ValueError('missing, linked or oversized manifest')
        raw = manifest.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
            raise ValueError('manifest does not match the independently trusted root')
        doc = json.loads(raw)
        if not isinstance(doc, dict) or type(doc.get('schema_version')) is not int or doc['schema_version'] != 1:
            raise ValueError('unsupported bundle schema')
        records = doc.get('files')
        if not isinstance(records, list) or not 1 <= len(records) <= 256:
            raise ValueError('bundle requires between 1 and 256 files')
        actual = set()
        for path in root.rglob('*'):
            if path.is_symlink():
                raise ValueError('symlinks are not allowed in an evidence bundle')
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
            elif not path.is_dir():
                raise ValueError('special filesystem entries are not allowed')
        listed = set()
        total = 0
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('malformed file record')
            relative = record.get('path')
            if not isinstance(relative, str) or not relative or '\\' in relative:
                raise ValueError('file path must be a nonempty relative POSIX path')
            posix = PurePosixPath(relative)
            if posix.is_absolute() or '..' in posix.parts or relative != posix.as_posix() or relative in ('.', 'manifest.json'):
                raise ValueError('unsafe or noncanonical file path')
            if relative in listed:
                raise ValueError('duplicate file record')
            listed.add(relative)
            expected_size = record.get('size')
            if type(expected_size) is not int or expected_size < 0:
                raise ValueError('file size must be a nonnegative integer')
            expected_hash = _hash(record.get('sha256'))
            total += expected_size
            if total > max_total_bytes:
                raise ValueError('bundle exceeds the total byte budget')
            path = root.joinpath(*posix.parts)
            if not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError(f'missing or escaping evidence file: {relative}')
            if path.stat().st_size != expected_size:
                raise ValueError(f'evidence size mismatch: {relative}')
            hasher = hashlib.sha256()
            size = 0
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError('file changed during verification')
                    hasher.update(chunk)
            if size != expected_size or hasher.hexdigest() != expected_hash:
                raise ValueError(f'evidence hash mismatch: {relative}')
        if actual != listed | {'manifest.json'}:
            raise ValueError('bundle contains missing or unlisted files')
        return {'verified_files': len(listed), 'verified_bytes': total,
                'manifest_sha256': expected_manifest_sha256}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'bundle cannot be verified: {exc}') from exc
