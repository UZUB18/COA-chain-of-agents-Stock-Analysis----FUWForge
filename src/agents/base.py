# Base classes for agent implementations.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.envelope import Envelope


@dataclass
class AgentContext:
    run_id: str
    params: Dict[str, Any]
    depth_config: Dict[str, Any]
    focus_overrides: Dict[str, Any]
    frozen_checksum: str
    workdir: Path
    provenance_path: Path
    retries: int = 0
    envelopes: Dict[str, Envelope] = field(default_factory=dict)
    source_documents: list[Dict[str, Any]] = field(default_factory=list)
    source_text: str = ""
    evidence_checksum: str = ""
    challenge_initial: Optional[Dict[str, Any]] = None
    on_provider_return: Optional[Callable[[str], Any]] = None


@dataclass
class AgentOutput:
    envelope: Envelope
    raw_response: Dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    """
    Base abstraction for contract-bound agents.
    """

    id: str = "AX"
    name: str = "Abstract Agent"

    def __init__(self, prompt_path: Optional[Path] = None):
        self.prompt_path = prompt_path

    def run(self, context: AgentContext) -> AgentOutput:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError("Agent implementations must override `run`.")

    def load_prompt(self) -> str:
        if not self.prompt_path:
            raise FileNotFoundError("Prompt path not configured for agent.")
        return self.prompt_path.read_text(encoding="utf-8")

    def build_prompt(self, context: AgentContext) -> str:
        if hasattr(self, "_build_prompt") and callable(getattr(self, "_build_prompt")):
            return getattr(self, "_build_prompt")(context)
        ticker = context.params.get("ticker", "TICK")
        checksum = context.frozen_checksum or "unfrozen"
        return f"Agent {self.id} ({self.name}) - {ticker} - {checksum}"
