"""
Runtime metrics capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AgentMetric:
    latency_ms: int = 0
    tokens: int = 0
    retries: int = 0
    confidence: float | None = None
    status: str = "pending"


@dataclass
class RuntimeMetricsCollector:
    records: Dict[str, AgentMetric] = field(default_factory=dict)

    def start_agent(self, agent_id: str) -> None:
        self.records.setdefault(agent_id, AgentMetric(status="in_progress"))

    def finalize_agent(
        self,
        agent_id: str,
        latency_ms: int,
        tokens: int,
        retries: int,
        confidence: float | None,
        status: str,
    ) -> None:
        self.records[agent_id] = AgentMetric(
            latency_ms=latency_ms,
            tokens=tokens,
            retries=retries,
            confidence=confidence,
            status=status,
        )

    def to_summary(self) -> Dict[str, Dict[str, int | float | str | None]]:
        return {agent_id: vars(metric) for agent_id, metric in self.records.items()}
