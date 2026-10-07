# Agent registry and providers for FUWForge multi-agent execution.

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from src.agents.base import AgentContext, AgentOutput, BaseAgent
from src.agents.mock import (
    A1Mock,
    A2Mock,
    A3Mock,
    A4Mock,
    A5Mock,
    A6BMock,
    A6RMock,
    A6Mock,
    A7Mock,
    MockAgent,
)

AGENT_SPECS: List[Tuple[str, str]] = [
    ("A1", "Harvester-&-Validator"),
    ("A2", "Business-Market-Peers"),
    ("A3", "Quant-Valuation-Technicals"),
    ("A4", "Leadership-Governance-ESG"),
    ("A5", "News-Sentiment-Flows"),
    ("A6B", "Bull-Case"),
    ("A6R", "Bear-Case"),
    ("A6", "CIO-Synthesis"),
    ("A7", "Final-Assembly"),
]


class StubAgent(BaseAgent):
    """
    Placeholder agent that raises until integrated with model execution.
    """

    def run(
        self, context: AgentContext
    ) -> AgentOutput:  # pragma: no cover - orchestrator handles stub
        raise NotImplementedError(
            f"Agent {self.id} is a stub. Plug in your execution logic before running automated mode."
        )


@dataclass
class RegistryEntry:
    agent_cls: Type[BaseAgent]
    prompt_path: str | None = None
    factory: Optional[Callable[[], BaseAgent]] = None


class AgentRegistry:
    def __init__(self, provider: str = "custom") -> None:
        self.provider = provider
        self._registry: Dict[str, RegistryEntry] = {}
        self._instances: Dict[str, BaseAgent] = {}

    def register(
        self, agent_cls: Type[BaseAgent], prompt_path: str | None = None
    ) -> None:
        self._registry[agent_cls.id] = RegistryEntry(
            agent_cls=agent_cls, prompt_path=prompt_path
        )

    def register_instance(self, agent: BaseAgent) -> None:
        self._instances[agent.id] = agent

    def register_factory(self, agent_id: str, factory: Callable[[], BaseAgent]) -> None:
        self._registry[agent_id] = RegistryEntry(agent_cls=BaseAgent, factory=factory)

    def resolve(self, agent_id: str) -> BaseAgent:
        if agent_id in self._instances:
            return self._instances[agent_id]
        if agent_id not in self._registry:
            raise KeyError(f"Agent {agent_id} is not registered.")
        entry = self._registry[agent_id]
        if entry.factory is not None:
            return entry.factory()
        instance = entry.agent_cls(
            prompt_path=None if entry.prompt_path is None else entry.prompt_path
        )
        return instance

    def known_agents(self) -> Dict[str, RegistryEntry]:
        return dict(self._registry)


def make_mock_agent_cls(
    agent_id: str, agent_name: str, randomize: bool = False
) -> Type[BaseAgent]:
    class ConfiguredMockAgent(MockAgent):
        def __init__(self, prompt_path: Optional[Path] = None):
            super().__init__(
                agent_id=agent_id,
                name=agent_name,
                prompt_path=prompt_path,
                randomize=randomize,
            )

    ConfiguredMockAgent.id = agent_id
    ConfiguredMockAgent.name = agent_name
    ConfiguredMockAgent.__name__ = f"{agent_id}ConfiguredMock"
    return ConfiguredMockAgent


def make_model_agent_cls(
    agent_id: str,
    agent_name: str,
    backend: Any,
    max_retries: int = 2,
    retry_backoff_seconds: float = 0.0,
    strict: Optional[bool] = None,
) -> Type[BaseAgent]:
    from src.agents.model import ModelAgent

    class ConfiguredModelAgent(ModelAgent):
        def __init__(self, prompt_path: Optional[Path] = None):
            super().__init__(
                agent_id=agent_id,
                name=agent_name,
                backend=backend,
                prompt_path=prompt_path,
                max_retries=max_retries,
                strict=strict,
                retry_backoff_seconds=retry_backoff_seconds,
            )

    ConfiguredModelAgent.id = agent_id
    ConfiguredModelAgent.name = agent_name
    ConfiguredModelAgent.__name__ = f"{agent_id}ConfiguredModel"
    return ConfiguredModelAgent


def mock_registry(randomize: bool = False) -> AgentRegistry:
    """
    Registry populated with mock agents capable of generating synthetic/random test data.
    """
    registry = AgentRegistry(provider="mock")
    for agent_id, name in AGENT_SPECS:
        cls = make_mock_agent_cls(agent_id, name, randomize=randomize)
        registry.register(cls)
    return registry


def model_registry(
    backend: Any,
    provider: str = "model",
    max_retries: int = 2,
    retry_backoff_seconds: float = 0.0,
    strict: Optional[bool] = None,
) -> AgentRegistry:
    """
    Registry populated with model agents powered by the given ModelBackend.
    """
    registry = AgentRegistry(provider=provider)
    for agent_id, name in AGENT_SPECS:
        cls = make_model_agent_cls(
            agent_id,
            name,
            backend=backend,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            strict=strict,
        )
        registry.register(cls)
    return registry


def stub_registry() -> AgentRegistry:
    """
    Pre-populate registry with stubbed agents A1–A7 plus A6B/A6R.
    """
    registry = AgentRegistry(provider="stub")
    for agent_id, name in AGENT_SPECS:
        stub_cls = type(f"{agent_id}Stub", (StubAgent,), {"id": agent_id, "name": name})
        registry.register(stub_cls)
    return registry


def default_registry(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    randomize: bool = False,
    max_retries: Optional[int] = None,
    retry_backoff_seconds: Optional[float] = None,
    strict: Optional[bool] = None,
    **kwargs: Any,
) -> AgentRegistry:
    """
    Resolve agent registry according to provider ('mock', 'agy', 'openrouter', 'openai', 'gemini', 'stub').
    Defaults to 'mock' for instant, offline verification.
    """
    selected_provider = (
        provider or os.environ.get("FUWFORGE_PROVIDER") or "mock"
    ).lower()
    selected_model = model or os.environ.get("FUWFORGE_MODEL")

    if selected_provider == "stub":
        return stub_registry()

    if selected_provider in ("mock", "synthetic"):
        return mock_registry(randomize=randomize)

    model_kwargs: Dict[str, Any] = {
        "provider": selected_provider,
        "max_retries": 2 if max_retries is None else max_retries,
        "retry_backoff_seconds": (
            0.0 if retry_backoff_seconds is None else retry_backoff_seconds
        ),
        "strict": strict,
    }

    if selected_provider in ("agy", "gemini_cli"):
        from src.agents.model import AgyCliBackend

        backend = AgyCliBackend(
            model=selected_model or "gemini-3.8-flash-low", **kwargs
        )
        return model_registry(backend, **model_kwargs)

    if selected_provider in ("openrouter",):
        from src.agents.model import OpenRouterBackend

        backend = OpenRouterBackend(
            model=selected_model or "google/gemini-2.0-flash-001", **kwargs
        )
        return model_registry(backend, **model_kwargs)

    if selected_provider in ("opencode", "opencode-go", "opencode_go"):
        from src.agents.model import OpenCodeGoBackend

        backend = OpenCodeGoBackend(
            model=selected_model or "deepseek-v4.1-flash", **kwargs
        )
        return model_registry(backend, **model_kwargs)

    if selected_provider in ("openai",):
        from src.agents.model import OpenAICompatibleBackend

        backend = OpenAICompatibleBackend(
            model=selected_model or "gpt-4o-mini", **kwargs
        )
        return model_registry(backend, **model_kwargs)

    if selected_provider == "gemini":
        from src.agents.model import GeminiApiBackend

        backend = GeminiApiBackend(model=selected_model, **kwargs)
        return model_registry(backend, **model_kwargs)

    raise ValueError(
        f"Unknown agent provider '{selected_provider}'. Supported: mock, agy, opencode, openrouter, openai, gemini, stub."
    )
