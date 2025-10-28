# Agent registry and stubs.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Type

from src.agents.base import AgentContext, AgentOutput, BaseAgent


class StubAgent(BaseAgent):
    """
    Placeholder agent that raises until integrated with model execution.
    """

    def run(self, context: AgentContext) -> AgentOutput:  # pragma: no cover - orchestrator handles stub
        raise NotImplementedError(
            f"Agent {self.id} is a stub. Plug in your execution logic before running automated mode."
        )


@dataclass
class RegistryEntry:
    agent_cls: Type[BaseAgent]
    prompt_path: str | None = None


class AgentRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, RegistryEntry] = {}

    def register(self, agent_cls: Type[BaseAgent], prompt_path: str | None = None) -> None:
        self._registry[agent_cls.id] = RegistryEntry(agent_cls=agent_cls, prompt_path=prompt_path)

    def resolve(self, agent_id: str) -> BaseAgent:
        if agent_id not in self._registry:
            raise KeyError(f"Agent {agent_id} is not registered.")
        entry = self._registry[agent_id]
        instance = entry.agent_cls(prompt_path=None if entry.prompt_path is None else entry.prompt_path)
        return instance

    def known_agents(self) -> Dict[str, RegistryEntry]:
        return dict(self._registry)


def default_registry() -> AgentRegistry:
    """
    Pre-populate registry with stubbed agents A1–A7 plus A6B/A6R.
    """
    registry = AgentRegistry()

    class A1Stub(StubAgent):
        id = "A1"
        name = "Harvester-&-Validator"

    class A2Stub(StubAgent):
        id = "A2"
        name = "Business-Market-Peers"

    class A3Stub(StubAgent):
        id = "A3"
        name = "Quant-Valuation-Technicals"

    class A4Stub(StubAgent):
        id = "A4"
        name = "Leadership-Governance-ESG"

    class A5Stub(StubAgent):
        id = "A5"
        name = "News-Sentiment-Flows"

    class A6BStub(StubAgent):
        id = "A6B"
        name = "Bull-Case"

    class A6RStub(StubAgent):
        id = "A6R"
        name = "Bear-Case"

    class A6Stub(StubAgent):
        id = "A6"
        name = "CIO-Synthesis"

    class A7Stub(StubAgent):
        id = "A7"
        name = "Final-Assembly"

    for cls in [A1Stub, A2Stub, A3Stub, A4Stub, A5Stub, A6BStub, A6RStub, A6Stub, A7Stub]:
        registry.register(cls)

    return registry
