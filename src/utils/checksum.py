"""
Checksum helpers to enforce immutability guarantees.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable


def sha256_digest(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    return f"sha256:{digest}"


def checksum_string(value: str) -> str:
    return sha256_digest(value.encode("utf-8"))


def checksum_json(data: Any) -> str:
    """
    Deterministically hash JSON-like data structures.
    """
    normalized = _normalize_json(data)
    return sha256_digest(normalized.encode("utf-8"))


def _normalize_json(data: Any) -> str:
    """
    Flatten JSON data with stable key ordering.
    """
    if isinstance(data, dict):
        items = ",".join(
            f"{k}:{_normalize_json(v)}" for k, v in sorted(data.items(), key=lambda kv: kv[0])
        )
        return "{" + items + "}"
    if isinstance(data, (list, tuple, set)):
        entries = ",".join(_normalize_json(item) for item in data)
        return "[" + entries + "]"
    if isinstance(data, bool):
        return "true" if data else "false"
    if data is None:
        return "null"
    return str(data)


def checksum_file(path: Path, chunk_size: int = 65536) -> str:
    """
    Hash a file without loading it fully into memory.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def checksum_chain(entries: Iterable[str]) -> str:
    """
    Build a hash chain from sequential entries.
    """
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"
