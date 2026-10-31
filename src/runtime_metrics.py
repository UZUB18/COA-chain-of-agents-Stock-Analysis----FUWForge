"""
Runtime metrics capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AgentMetric:
    latency_ms: int = 0
    tokens: int | None = None
    retries: int = 0
    confidence: float | None = None
    status: str = "pending"
    reused: bool = False


@dataclass
class RuntimeMetricsCollector:
    records: Dict[str, AgentMetric] = field(default_factory=dict)

    def start_agent(self, agent_id: str) -> None:
        self.records.setdefault(agent_id, AgentMetric(status="in_progress"))

    def finalize_agent(
        self,
        agent_id: str,
        latency_ms: int,
        tokens: int | None,
        retries: int,
        confidence: float | None,
        status: str,
        reused: bool = False,
    ) -> None:
        previous = self.records.get(agent_id)
        if previous and previous.status == "passed":
            latency_ms += previous.latency_ms
            retries += previous.retries
            tokens = previous.tokens + tokens if previous.tokens is not None and tokens is not None else None
            reused = previous.reused and reused
        self.records[agent_id] = AgentMetric(
            latency_ms=latency_ms,
            tokens=tokens,
            retries=retries,
            confidence=confidence,
            status=status,
            reused=reused,
        )

    def to_summary(self) -> Dict[str, Dict[str, int | float | str | None]]:
        return {agent_id: vars(metric) for agent_id, metric in self.records.items()}
