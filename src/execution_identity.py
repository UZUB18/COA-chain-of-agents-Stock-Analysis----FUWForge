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

Release note (identity version 2):
Execution keys use ``schema_version: 2`` and include a package-relative digest
of all declared output-shaping modules (plus source-backed custom agent
classes). In-flight resumable runs created under ``schema_version: 1`` lose
receipt reuse once after upgrading; finish or abandon in-flight runs before
upgrading. Custom agents whose source cannot be hashed fail closed on resume.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.resources
import inspect
import json
from pathlib import Path, PurePosixPath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

EXECUTION_IDENTITY_VERSION = 2

OUTPUT_SHAPING_MODULES: tuple[str, ...] = (
    "agents/base.py",
    "agents/mock.py",
    "agents/model.py",
    "agents/output_format.py",
    "agents/output_schema.py",
    "agents/registry.py",
    "altman_inputs.py",
    "calculation_witness.py",
    "contracts.py",
    "data_gaps.py",
    "evidence.py",
    "fact_binding.py",
    "facts.py",
    "forensic_inputs.py",
    "sec_facts.py",
    "utils/beneish.py",
    "utils/finance.py",
)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
#: Envelope checksums in this project are stored as "sha256:<hex>"; accept both
#: the bare hex form used inside execution keys and the prefixed form used on
#: the envelope itself.
_CHECKSUM_RE = re.compile(r"^(?:sha256:)?[a-f0-9]{64}$")


def _read_package_module_bytes(rel_path: str) -> bytes:
    pkg = __package__ or "src"
    parts = PurePosixPath(rel_path).parts
    return importlib.resources.files(pkg).joinpath(*parts).read_bytes()


@functools.lru_cache(maxsize=1)
def framework_code_digest() -> str:
    """Return a process-cached SHA-256 digest of all declared output-shaping modules."""
    entries = [
        {
            "path": rel_path,
            "sha256": hashlib.sha256(_read_package_module_bytes(rel_path)).hexdigest(),
        }
        for rel_path in sorted(OUTPUT_SHAPING_MODULES)
    ]
    payload = json.dumps(
        {"version": EXECUTION_IDENTITY_VERSION, "modules": entries},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unhashable_agent_digest(fw_digest: str, agent: Any, *, is_factory: bool) -> str:
    cls = type(agent)
    payload = json.dumps(
        {
            "version": EXECUTION_IDENTITY_VERSION,
            "framework": fw_digest,
            "unhashable": f"{cls.__module__}.{cls.__qualname__}",
            "factory": bool(is_factory),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def agent_code_identity(agent: Any, *, is_factory: bool = False) -> tuple[str, bool]:
    """Return ``(code_sha256, is_hashable)`` for an agent instance.

    Built-in framework agents use :func:`framework_code_digest`. Ordinary
    source-backed custom classes combine the framework digest with the class and
    module bytes (never absolute filesystem paths). Dynamic, zip-imported, or
    factory-created agents return ``is_hashable=False`` so resume fails closed.
    """
    fw_digest = framework_code_digest()
    if is_factory:
        return _unhashable_agent_digest(fw_digest, agent, is_factory=True), False

    from src.agents.base import BaseAgent
    from src.agents.mock import (
        A1Mock,
        A2Mock,
        A3Mock,
        A4Mock,
        A5Mock,
        A6BMock,
        A6Mock,
        A6RMock,
        A7Mock,
        MockAgent,
    )
    from src.agents.model import ModelAgent

    builtin_classes = {
        object,
        BaseAgent,
        MockAgent,
        ModelAgent,
        A1Mock,
        A2Mock,
        A3Mock,
        A4Mock,
        A5Mock,
        A6BMock,
        A6RMock,
        A6Mock,
        A7Mock,
    }
    pkg = __package__ or "src"
    custom_classes = []
    for cls in type(agent).__mro__:
        if cls in builtin_classes:
            continue
        if (
            cls.__dict__.get("_fuwforge_builtin") is True
            and cls.__module__ == f"{pkg}.agents.registry"
        ):
            continue
        custom_classes.append(cls)

    if not custom_classes:
        return fw_digest, True

    custom_entries = []
    for cls in custom_classes:
        try:
            source_file = inspect.getsourcefile(cls)
            class_src = inspect.getsource(cls)
            if not source_file or not class_src or not class_src.strip():
                return _unhashable_agent_digest(fw_digest, agent, is_factory=False), False
            src_path = Path(source_file)
            if not src_path.is_file():
                return _unhashable_agent_digest(fw_digest, agent, is_factory=False), False
            mod_bytes = src_path.read_bytes()
            resolved_src = src_path.resolve()
            for attr_val in cls.__dict__.values():
                if inspect.isfunction(attr_val):
                    fn_path = Path(attr_val.__code__.co_filename)
                    if not fn_path.is_file() or fn_path.resolve() != resolved_src:
                        return _unhashable_agent_digest(fw_digest, agent, is_factory=False), False
                    fn_src = inspect.getsource(attr_val)
                    if not fn_src or not fn_src.strip():
                        return _unhashable_agent_digest(fw_digest, agent, is_factory=False), False
        except (OSError, TypeError, ValueError):
            return _unhashable_agent_digest(fw_digest, agent, is_factory=False), False

        custom_entries.append(
            {
                "qualname": cls.__qualname__,
                "class_sha256": hashlib.sha256(class_src.encode("utf-8")).hexdigest(),
                "module_sha256": hashlib.sha256(mod_bytes).hexdigest(),
            }
        )

    payload = json.dumps(
        {
            "version": EXECUTION_IDENTITY_VERSION,
            "framework": fw_digest,
            "custom": custom_entries,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), True


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
        "schema_version": EXECUTION_IDENTITY_VERSION,
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
    "EXECUTION_IDENTITY_VERSION",
    "OUTPUT_SHAPING_MODULES",
    "UpstreamRef",
    "agent_code_identity",
    "capture_upstream",
    "execution_key",
    "framework_code_digest",
    "graph_from_envelopes",
    "true_descendants",
    "upstream_to_envelope_refs",
    "_bare_hex",
    "_hash",
    "_SHA256_RE",
]