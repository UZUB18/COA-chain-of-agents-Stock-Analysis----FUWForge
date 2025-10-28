# Base classes for agent implementations.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

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
