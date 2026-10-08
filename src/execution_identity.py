"""Immutable consumed-parent metadata and content-addressed execution identity.

The distinction this module exists to protect:

- **Consumed lineage** is what an agent actually read when its prompt was
  rendered. It must be captured from a frozen input snapshot at execution
  start, before any parallel sibling is accepted.
- **Acceptance order** is a presentation/storage choice used to keep output
  files and reports deterministic. It is not evidence of causality.

Building lineage from the shared accepted-results dictionary at acceptance time
records parents an agent never saw. That makes the stored graph differ from the
executed graph, which silently breaks incremental recomputation, audit
reconstruction and prompt debugging.

The caller must separately freeze the actual input payload and record the exact
rendered prompt; this module records the identity of what was consumed, not the
provider's determinism. A content-addressed key does not make live model
generation reproducible — identical keys mean "same request", not "same answer".
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
#: Envelope checksums in this project are stored as "sha256:<hex>"; accept both
#: the bare hex form used inside execution keys and the prefixed form used on
#: the envelope itself.
_CHECKSUM_RE = re.compile(r"^(?:sha256:)?[a-f0-9]{64}$")


def _hash(value: object, *, label: str = "checksum") -> str:
    if not isinstance(value, str) or not _CHECKSUM_RE.fullmatch(value):
        raise ValueError(f"expected a lowercase SHA-256 hex digest for {label}")
    return value


def _bare_hex(checksum: str) -> str:
    return checksum.split(":", 1)[1] if ":" in checksum else checksum


@dataclass(frozen=True, slots=True)
class UpstreamRef:
    """An immutable reference to one consumed parent envelope."""

    agent: str
    content_checksum: str


def capture_upstream(
    envelopes: Mapping[str, Mapping[str, Any]]
) -> tuple[UpstreamRef, ...]:
    """
    Capture the parents an agent is about to consume.

    Call this at execution start against the *frozen* input mapping, never when
    a parallel result is later accepted. The returned tuple is sorted and
    immutable, so subsequent mutation of ``envelopes`` cannot change it.

    Entries without a recorded content checksum are rejected: an unsealed parent
    cannot be referenced as evidence of what was read.
    """
    refs = []
    for agent, envelope in envelopes.items():
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent identifiers must be nonempty strings")
        if not isinstance(envelope, Mapping):
            raise ValueError("expected a serialized envelope mapping")
        refs.append(UpstreamRef(agent.strip(), _hash(envelope.get("content_checksum"))))
    return tuple(sorted(refs, key=lambda ref: ref.agent))


def execution_key(
    *,
    provider: str,
    model: str,
    prompt_sha256: str,
    evidence_sha256: str,
    upstream: tuple[UpstreamRef, ...],
) -> str:
    """
    A content-addressed key binding one execution request to its identity.

    Two executions with the same key requested the same thing from the same
    provider with the same consumed parents. It does **not** promise the same
    response: live model generation is not deterministic, so a cached receipt
    must still be validated before reuse.
    """
    for label, value in (("provider", provider), ("model", model)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be specified")
    refs = []
    seen = set()
    for ref in upstream:
        if not isinstance(ref, UpstreamRef) or ref.agent in seen:
            raise ValueError("upstream references must be unique UpstreamRef values")
        if not isinstance(ref.agent, str) or not ref.agent.strip():
            raise ValueError("agent identifiers must be nonempty strings")
        refs.append(
            {
                "agent": ref.agent,
                "content_checksum": _bare_hex(_hash(ref.content_checksum)),
            }
        )
        seen.add(ref.agent)
    body = {
        "schema_version": 1,
        "provider": provider,
        "model": model,
        "prompt_sha256": _bare_hex(_hash(prompt_sha256, label="prompt")),
        "evidence_sha256": _bare_hex(_hash(evidence_sha256, label="evidence")),
        "upstream": sorted(refs, key=lambda item: item["agent"]),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def upstream_to_envelope_refs(refs: tuple[UpstreamRef, ...]) -> list[dict[str, str]]:
    """Serialize captured refs into the envelope ``upstream`` shape."""
    return [
        {"agent": ref.agent, "content_checksum": ref.content_checksum}
        for ref in refs
    ]


def graph_from_envelopes(envelopes: Mapping[str, Any]) -> dict[str, list[str]]:
    """Build an agent -> consumed parents map from serialized envelopes."""
    return {
        agent: [entry.get("agent") for entry in (envelope.get("upstream") or [])]
        for agent, envelope in envelopes.items()
    }


def true_descendants(
    graph: Mapping[str, Any], changed_agents: Any
) -> set[str]:
    """
    Agents that depend on any changed agent, transitively.

    Used to scope invalidation on resume: a new fact or prompt version
    invalidates the affected node and its genuine descendants, not every role
    merely because acceptance order changed.
    """
    parents = graph_from_envelopes(graph)
    children: dict[str, set[str]] = {}
    for agent, upstream in parents.items():
        for parent in upstream:
            children.setdefault(parent, set()).add(agent)

    affected = {agent for agent in changed_agents}
    frontier = set(affected)
    while frontier:
        nxt: set[str] = set()
        for node in frontier:
            for child in children.get(node, set()):
                if child not in affected:
                    affected.add(child)
                    nxt.add(child)
        frontier = nxt
    return affected


__all__ = [
    "UpstreamRef",
    "capture_upstream",
    "execution_key",
    "graph_from_envelopes",
    "true_descendants",
    "upstream_to_envelope_refs",
    "_bare_hex",
    "_hash",
    "_SHA256_RE",
]