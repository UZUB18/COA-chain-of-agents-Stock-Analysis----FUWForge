"""
Deterministic orchestrator for FUWForge (Fired Up Wealth) on COA v2.1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.agents.base import AgentContext, AgentOutput, BaseAgent
from src.agents.registry import AgentRegistry, default_registry
from src.config import load_depth_modes, load_focus_routing, load_orchestrator_config
from src.envelope import Envelope
from src.provenance import ProvenanceLog
from src.runtime_metrics import RuntimeMetricsCollector
from src.utils.checksum import checksum_json
from src.validators import EnvelopeValidator, ValidationResult, validate_checksum, validate_required_artifacts


@dataclass
class OrchestratorParams:
    company: str
    ticker: str
    analysis_date: str
    depth: str
    focus: List[str]
    weights: Any


class FUWForgeOrchestrator:
    """
    Coordinates the end-to-end flow across A1–A7.
    """

    def __init__(
        self,
        project_root: Path,
        registry: Optional[AgentRegistry] = None,
    ) -> None:
        self.project_root = project_root
        self.config_dir = project_root / "config"
        self.schema_path = project_root / "schemas" / "envelope_v2_1.json"
        self.depth_modes = load_depth_modes(self.config_dir)
        self.focus_routing = load_focus_routing(self.config_dir)
        self.flow_config = load_orchestrator_config(self.config_dir)
        self.registry = registry or default_registry()
        self.validator = EnvelopeValidator(self.schema_path)

    def run(
        self,
        run_id: str,
        params: OrchestratorParams,
        mode: str = "automated",
        output_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Execute the gated flow. Returns metadata about the run.
        """
        depth_key = params.depth.lower()
        if depth_key not in self.depth_modes:
            raise KeyError(f"Unknown depth mode '{params.depth}'. Expected one of {list(self.depth_modes)}.")

        depth_config = self.depth_modes[depth_key].payload
        focus_overrides = self._resolve_focus(params.focus)

        output_root = output_root or (self.project_root / "runs" / run_id)
        output_root.mkdir(parents=True, exist_ok=True)

        provenance = ProvenanceLog(output_root / f"{params.ticker}_{params.analysis_date}_provenance.log")
        provenance.load_existing()
        metrics = RuntimeMetricsCollector()

        frozen_checksum: Optional[str] = None
        envelopes: Dict[str, Envelope] = {}

        # Phase 1 — A1 Gate
        metrics.start_agent("A1")
        a1_env = self._execute_agent(
            agent_id="A1",
            params=params,
            depth_config=depth_config,
            focus_overrides=focus_overrides,
            frozen_checksum=None,
            run_id=run_id,
            output_root=output_root,
            provenance=provenance,
            mode=mode,
        )
        self._require(self.validator.validate(a1_env.to_dict()), "A1 schema validation failed")
        self._require(validate_required_artifacts(a1_env, ["price_timestamp_eet", "shares_basic", "shares_diluted"]))
        gate_result = a1_env.gates.get("passed")
        self._require(
            ValidationResult(valid=bool(gate_result), errors=["A1 gate not passed"]),
            "A1 gate failure halts the run",
        )
        frozen_checksum = a1_env.metrics.get("data_checksum")
        if not frozen_checksum:  # pragma: no cover - guardrail
            raise RuntimeError("A1 did not emit metrics.data_checksum; cannot proceed.")
        provenance.append("ORCH", f"A1 checkpoint recorded (checksum={frozen_checksum}).")
        provenance.flush()
        envelopes["A1"] = a1_env
        metrics.finalize_agent("A1", latency_ms=0, tokens=0, retries=0, confidence=a1_env.confidence, status="passed")

        # Phase 2 — Parallel Agents (sequential execution for scaffolding)
        for agent_id in ["A2", "A3", "A4", "A5"]:
            metrics.start_agent(agent_id)
            env = self._execute_agent(
                agent_id=agent_id,
                params=params,
                depth_config=depth_config,
                focus_overrides=focus_overrides,
                frozen_checksum=frozen_checksum,
                run_id=run_id,
                output_root=output_root,
                provenance=provenance,
                mode=mode,
            )
            self._require(self.validator.validate(env.to_dict()), f"{agent_id} schema validation failed")
            self._require(validate_checksum(env, frozen_checksum), f"{agent_id} checksum mismatch")
            required = self._required_artifacts_for(agent_id)
            if required:
                self._require(
                    validate_required_artifacts(env, required),
                    f"{agent_id} missing required artifacts",
                )
            provenance.append("ORCH", f"{agent_id} envelope accepted.")
            provenance.flush()
            envelopes[agent_id] = env
            metrics.finalize_agent(
                agent_id,
                latency_ms=0,
                tokens=0,
                retries=0,
                confidence=env.confidence,
                status="passed",
            )

        # Phase 3 — Adversarial Loop
        for agent_id in ["A6B", "A6R", "A6"]:
            metrics.start_agent(agent_id)
            env = self._execute_agent(
                agent_id=agent_id,
                params=params,
                depth_config=depth_config,
                focus_overrides=focus_overrides,
                frozen_checksum=frozen_checksum,
                run_id=run_id,
                output_root=output_root,
                provenance=provenance,
                mode=mode,
            )
            self._require(self.validator.validate(env.to_dict()), f"{agent_id} schema validation failed")
            self._require(validate_checksum(env, frozen_checksum), f"{agent_id} checksum mismatch")
            envelopes[agent_id] = env
            provenance.append("ORCH", f"{agent_id} envelope accepted.")
            provenance.flush()
            metrics.finalize_agent(
                agent_id,
                latency_ms=0,
                tokens=0,
                retries=0,
                confidence=env.confidence,
                status="passed",
            )

        # Phase 4 — Final Assembly (A7)
        metrics.start_agent("A7")
        a7_env = self._execute_agent(
            agent_id="A7",
            params=params,
            depth_config=depth_config,
            focus_overrides=focus_overrides,
            frozen_checksum=frozen_checksum,
            run_id=run_id,
            output_root=output_root,
            provenance=provenance,
            mode=mode,
        )
        self._require(self.validator.validate(a7_env.to_dict()), "A7 schema validation failed")
        self._require(validate_checksum(a7_env, frozen_checksum), "A7 checksum mismatch")
        envelopes["A7"] = a7_env
        provenance.append("ORCH", "A7 final assembly accepted.")
        provenance.flush()
        metrics.finalize_agent("A7", latency_ms=0, tokens=0, retries=0, confidence=a7_env.confidence, status="passed")

        run_summary = {
            "run_id": run_id,
            "params": params.__dict__,
            "checksum": frozen_checksum,
            "metrics": metrics.to_summary(),
            "outputs": self._write_outputs(params, output_root, envelopes),
        }
        return run_summary

    def _execute_agent(
        self,
        agent_id: str,
        params: OrchestratorParams,
        depth_config: Dict[str, Any],
        focus_overrides: Dict[str, Any],
        frozen_checksum: Optional[str],
        run_id: str,
        output_root: Path,
        provenance: ProvenanceLog,
        mode: str,
    ) -> Envelope:
        """
        Execute or ingest an agent envelope depending on mode.
        """
        context = AgentContext(
            run_id=run_id,
            params=params.__dict__,
            depth_config=depth_config,
            focus_overrides=focus_overrides,
            frozen_checksum=frozen_checksum or "",
            workdir=output_root,
            provenance_path=provenance.path,
        )

        if mode == "controller":
            path = output_root / f"{agent_id.lower()}_envelope.json"
            if not path.exists():
                raise FileNotFoundError(f"Controller mode expects {path} to exist.")
            data = json.loads(path.read_text(encoding="utf-8"))
            return Envelope.from_dict(data)

        agent = self.registry.resolve(agent_id)
        output = agent.run(context)
        envelope = output.envelope
        envelope.metrics.setdefault("data_checksum", frozen_checksum or checksum_json(context.params))
        envelope.params.setdefault("ticker", params.ticker)
        self._write_agent_outputs(output_root, agent_id, output)
        return envelope

    def _required_artifacts_for(self, agent_id: str) -> Iterable[str]:
        artifacts_map = {
            "A1": ["price_timestamp_eet", "shares_basic", "shares_diluted"],
            "A2": ["PEER_LIST", "MoatTrend", "TopCustomerPct", "TopSupplierPct"],
            "A3": ["AltmanZ", "BeneishM", "OCF_vs_NI_Flag", "PEG_TTM", "PEG_Fwd", "HypeFlag", "PeerTable"],
            "A4": ["CEO_Pay_Ratio", "Auditor", "AuditorTenure", "GlassdoorScore", "AttritionNote", "ESG_Momentum"],
            "A5": [
                "SentimentSplit",
                "SocialVsFundDivergence",
                "MajorLitigation",
                "AuditorChangeYN",
                "InsiderNet12m",
            ],
            "A6B": ["BullCase"],
            "A6R": ["BearRebuttals"],
            "A6": ["DangerZoneDashboard", "ScenarioTable", "Catalysts", "Headwinds"],
            "A7": ["Scorecard", "SWOT", "Glossary"],
        }
        return artifacts_map.get(agent_id, [])

    def _resolve_focus(self, focus_tags: Iterable[str]) -> Dict[str, Any]:
        overrides: Dict[str, Any] = {}
        for tag in focus_tags:
            if tag not in self.focus_routing:
                continue
            overrides[tag] = self.focus_routing[tag]
        return overrides

    def _write_agent_outputs(self, output_root: Path, agent_id: str, output: AgentOutput) -> None:
        env_path = output_root / f"{agent_id.lower()}_envelope.json"
        env_path.write_text(json.dumps(output.envelope.to_dict(), indent=2), encoding="utf-8")
        if output.raw_response:
            raw_path = output_root / f"{agent_id.lower()}_raw.json"
            raw_path.write_text(json.dumps(output.raw_response, indent=2), encoding="utf-8")

    def _write_outputs(
        self,
        params: OrchestratorParams,
        output_root: Path,
        envelopes: Dict[str, Envelope],
    ) -> Dict[str, str]:
        assembled_sections = self._assemble_sections(envelopes)
        report_path = output_root / f"{params.ticker}_{params.analysis_date}_report.md"
        report_path.write_text(assembled_sections, encoding="utf-8")

        artifacts_path = output_root / f"{params.ticker}_{params.analysis_date}_artifacts.json"
        artifacts_payload = {agent_id: env.to_dict() for agent_id, env in envelopes.items()}
        artifacts_path.write_text(json.dumps(artifacts_payload, indent=2), encoding="utf-8")

        provenance_path = output_root / f"{params.ticker}_{params.analysis_date}_provenance.log"

        return {
            "report": str(report_path),
            "artifacts": str(artifacts_path),
            "provenance": str(provenance_path),
        }

    def _assemble_sections(self, envelopes: Dict[str, Envelope]) -> str:
        ordered_ids = ["A7", "A6", "A6B", "A6R", "A5", "A4", "A3", "A2", "A1"]
        sections: List[str] = []
        for agent_id in ordered_ids:
            env = envelopes.get(agent_id)
            if not env:
                continue
            for _, markdown in env.sections_md.items():
                sections.append(markdown.rstrip() + "\n\n")
        return "".join(sections)

    def _require(self, result: ValidationResult, message: str) -> None:
        if not result.valid:
            raise RuntimeError(f"{message}: {'; '.join(result.errors)}")


# Backwards compatibility alias (legacy naming)
CodexOrchestrator = FUWForgeOrchestrator
