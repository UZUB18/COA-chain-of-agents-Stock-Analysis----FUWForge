"""
Envelope models for agent outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, MutableMapping, Optional


class _Absent:
    def __repr__(self) -> str:
        return "<absent>"

    def __deepcopy__(self, memo: Any) -> "_Absent":
        return self

    def __copy__(self) -> "_Absent":
        return self


_ABSENT = _Absent()


@dataclass
class AgentInfo:
    id: str
    name: str


@dataclass
class Envelope:
    schema_version: str
    agent: AgentInfo
    timestamp: Optional[str]
    params: Dict[str, Any]
    gates: Dict[str, Any]
    sections_md: Dict[str, str]
    artifacts: Dict[str, Any]
    sources: List[Dict[str, Any]]
    warnings: List[str]
    metrics: Dict[str, Any]
    confidence: Optional[float] = None
    content_checksum: Optional[str] = None
    upstream: List[Dict[str, Any]] = field(default_factory=list)
    #: Declared checksum scheme. ``_ABSENT`` (legacy v1) is never emitted, so a
    #: legacy envelope round-trips byte-identically; an explicit value, even an
    #: invalid one, is preserved so verification can reject it.
    checksum_scheme: Any = field(default_factory=lambda: _ABSENT)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Envelope":
        agent_data = data.get("agent", {})
        agent = AgentInfo(
            id=agent_data.get("id", ""),
            name=agent_data.get("name", ""),
        )
        timestamp = data.get("timestamp")
        return cls(
            schema_version=data.get("schema_version", ""),
            agent=agent,
            timestamp=timestamp,
            params=dict(data.get("params", {})),
            gates=dict(data.get("gates", {})),
            sections_md=dict(data.get("sections_md", {})),
            artifacts=dict(data.get("artifacts", {})),
            sources=list(data.get("sources", [])),
            warnings=list(data.get("warnings", [])),
            metrics=dict(data.get("metrics", {})),
            confidence=data.get("confidence"),
            content_checksum=data.get("content_checksum"),
            upstream=list(data.get("upstream", [])),
            checksum_scheme=data["checksum_scheme"] if "checksum_scheme" in data else _ABSENT,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "agent": {"id": self.agent.id, "name": self.agent.name},
            "timestamp": self.timestamp,
            "params": self.params,
            "gates": self.gates,
            "sections_md": self.sections_md,
            "artifacts": self.artifacts,
            "sources": self.sources,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "confidence": self.confidence,
            "content_checksum": self.content_checksum,
            "upstream": self.upstream,
        }
        if self.checksum_scheme is not _ABSENT:
            data["checksum_scheme"] = self.checksum_scheme
        return data

    def set_timestamp_now(self) -> None:
        self.timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"


_OBJECT_FIELDS = ("params", "gates", "sections_md", "artifacts", "metrics")
_ARRAY_FIELDS = ("sources", "warnings", "upstream")


def envelope_shape_errors(data: Mapping[str, Any]) -> List[str]:
    """
    Describe why ``data`` cannot be held in an :class:`Envelope`.

    ``Envelope.from_dict`` coerces its containers with ``dict()`` and
    ``list()``. A wrong type either raises (``metrics: 42``) or is silently
    reshaped (a string becomes a list of characters), so a caller that loads
    an untrusted file must check the shape first and report the damage instead
    of building the envelope. An absent optional field is not reported here;
    the JSON schema owns missing fields.
    """
    errors: List[str] = []
    agent = data.get("agent")
    if not isinstance(agent, dict):
        errors.append("agent must be an object")
    else:
        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not agent_id:
            errors.append("agent.id must be a non-empty string")
    for field_name in _OBJECT_FIELDS:
        if field_name in data and not isinstance(data[field_name], dict):
            errors.append(f"{field_name} must be an object")
    for field_name in _ARRAY_FIELDS:
        if field_name in data and not isinstance(data[field_name], list):
            errors.append(f"{field_name} must be an array")
    return errors


def merge_sections(envelopes: List[Envelope]) -> Dict[str, str]:
    merged: MutableMapping[str, str] = {}
    for env in envelopes:
        for key, value in env.sections_md.items():
            merged[key] = value
    return dict(merged)
