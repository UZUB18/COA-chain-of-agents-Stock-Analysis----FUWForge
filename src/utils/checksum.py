"""
Checksum helpers to enforce immutability guarantees.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional


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
            f"{k}:{_normalize_json(v)}"
            for k, v in sorted(data.items(), key=lambda kv: kv[0])
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


def envelope_content_checksum(
    envelope_dict: Any, exclude: Iterable[str] = ("content_checksum", "metrics")
) -> str:
    """
    Hash the semantic content of an envelope (sections, artifacts, sources, lineage).

    ``metrics`` and ``content_checksum`` are excluded: most metrics are runtime telemetry
    and the checksum field would be self-referential. Explicit missing-data reasons
    are semantic research content and are hashed when present. The result is stable across
    JSON round-trips because ``checksum_json`` canonicalizes key order and types.
    """
    excluded = set(exclude)
    payload = {
        key: value for key, value in envelope_dict.items() if key not in excluded
    }
    gaps = (envelope_dict.get("metrics") or {}).get("missing_data")
    if gaps:
        payload["research_missing_data"] = gaps
    return checksum_json(payload)


#: Checksum scheme declared by every new envelope. Absence means legacy v1.
CHECKSUM_SCHEME_V2 = "fuwforge-canonical-json-v2"
#: Flat volatile telemetry metrics excluded from v2 (plus the ``telemetry``
#: subtree, reserved for future volatile execution detail). Every other metric
#: is semantic and hashed, so new metrics are covered without an allowlist.
TELEMETRY_METRICS = ("latency_ms", "tokens", "retries", "provider", "model", "telemetry")
LEGACY_CHECKSUM_WARNING = "legacy_checksum_scheme (non-injective canonical form)"


class UnknownChecksumScheme(ValueError):
    """An envelope declares a checksum scheme this code does not implement."""


class NonCanonicalEnvelope(UnknownChecksumScheme):
    """A v2 envelope holds values (NaN/Infinity) with no canonical form.

    Subclasses :class:`UnknownChecksumScheme` so every verifier that already
    fails closed on an unverifiable checksum also fails closed here.
    """


def canonical_json(data: Any) -> str:
    """Injective canonical JSON: sorted keys, no whitespace, no NaN/Infinity."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def envelope_content_checksum_v2(envelope_dict: Any) -> str:
    """Hash every envelope field except ``content_checksum`` and volatile telemetry."""
    payload = copy.deepcopy(dict(envelope_dict))
    payload.pop("content_checksum", None)
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        for key in TELEMETRY_METRICS:
            metrics.pop(key, None)
    try:
        canonical = canonical_json(payload)
    except ValueError as exc:  # NaN/Infinity: no v2 canonical form exists
        raise NonCanonicalEnvelope(f"envelope is not canonical JSON ({exc})") from None
    return sha256_digest(canonical.encode("utf-8"))


def checksum_scheme_of(envelope_dict: Any) -> Optional[str]:
    """``None`` for a legacy envelope (no declaration); raise for an unknown one."""
    if "checksum_scheme" not in envelope_dict:
        return None
    scheme = envelope_dict.get("checksum_scheme")
    if scheme != CHECKSUM_SCHEME_V2:
        raise UnknownChecksumScheme(f"unknown checksum_scheme {scheme!r}")
    return scheme


def envelope_checksum(envelope_dict: Any) -> str:
    """Content checksum under the envelope's own declared scheme.

    Never inferred from the digest: absence is v1, the v2 marker is v2, and
    anything else raises :class:`UnknownChecksumScheme`.
    """
    if checksum_scheme_of(envelope_dict) is None:
        return envelope_content_checksum(envelope_dict)
    return envelope_content_checksum_v2(envelope_dict)


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
