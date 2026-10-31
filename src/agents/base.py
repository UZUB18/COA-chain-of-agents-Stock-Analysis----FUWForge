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
    fact_ledger: Dict[str, Any] = field(default_factory=dict)
    #: Runner-computed forensic index inputs, when the ledger supports them.
    #:
    #: These are arithmetic the runner can do exactly and bind to source facts.
    #: A model may still report its own indices, but it must not be the only
    #: source of a number the run presents as computed.
    forensic_inputs: Dict[str, Any] = field(default_factory=dict)
    #: Runner-recorded evidence intake gaps (``EvidencePack.coverage_summary``).
    source_coverage: list[Dict[str, str]] = field(default_factory=list)
    #: Frozen v2 market history (``EvidencePack.market_snapshot``); ``None``
    #: for legacy packs, which keep the prior A1 price contract.
    market_snapshot: Optional[Dict[str, Any]] = None
    #: Runner-computed Altman Z inputs (``altman_inputs.altman_inputs_for``),
    #: feasible or an explicit unavailable result. Empty means "not supplied".
    altman_inputs: Dict[str, Any] = field(default_factory=dict)
    challenge_initial: Optional[Dict[str, Any]] = None
    on_provider_return: Optional[Callable[[str], Any]] = None
    on_repair_start: Optional[Callable[[str, str], Any]] = None
    web_search: bool = True
    on_search_start: Optional[Callable[[], Any]] = None
    gemini_backend: Optional[Any] = None


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
