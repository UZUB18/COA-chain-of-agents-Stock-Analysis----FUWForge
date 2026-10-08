"""Immutable consumed-parent metadata and content-addressed execution identity.

The caller must separately freeze the actual input payload and record the exact
rendered prompt. A hash key does not make live model generation deterministic.
"""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, asdict
import hashlib
import json
import re


def _hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('expected a lowercase SHA-256 hex digest')
    return value


@dataclass(frozen=True, slots=True)
class UpstreamRef:
    agent: str
    content_checksum: str


def capture_upstream(envelopes: Mapping[str, Mapping[str, object]]) -> tuple[UpstreamRef, ...]:
    """Call at execution start, not when a parallel result is later accepted."""
    refs = []
    for agent, envelope in envelopes.items():
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError('agent identifiers must be nonempty strings')
        if not isinstance(envelope, Mapping):
            raise ValueError('expected a serialized envelope mapping')
        refs.append(UpstreamRef(agent, _hash(envelope.get('content_checksum'))))
    return tuple(sorted(refs, key=lambda ref: ref.agent))


def execution_key(*, provider: str, model: str, prompt_sha256: str,
                  evidence_sha256: str, upstream: tuple[UpstreamRef, ...]) -> str:
    for label, value in (('provider', provider), ('model', model)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{label} must be specified')
    refs = []
    seen = set()
    for ref in upstream:
        if not isinstance(ref, UpstreamRef) or ref.agent in seen:
            raise ValueError('upstream references must be unique UpstreamRef values')
        if not isinstance(ref.agent, str) or not ref.agent.strip():
            raise ValueError('agent identifiers must be nonempty strings')
        _hash(ref.content_checksum)
        refs.append(asdict(ref))
        seen.add(ref.agent)
    body = {
        'schema_version': 1, 'provider': provider, 'model': model,
        'prompt_sha256': _hash(prompt_sha256),
        'evidence_sha256': _hash(evidence_sha256),
        'upstream': sorted(refs, key=lambda item: item['agent']),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()
