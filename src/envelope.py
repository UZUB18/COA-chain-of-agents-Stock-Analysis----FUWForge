"""
Envelope models for agent outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, MutableMapping, Optional


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
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
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
        }

    def set_timestamp_now(self) -> None:
        self.timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"


def merge_sections(envelopes: List[Envelope]) -> Dict[str, str]:
    merged: MutableMapping[str, str] = {}
    for env in envelopes:
        for key, value in env.sections_md.items():
            merged[key] = value
    return dict(merged)
