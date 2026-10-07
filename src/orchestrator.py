"""
Deterministic orchestrator for FUWForge (Fired Up Wealth) on COA v2.1.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.agents.base import AgentContext, AgentOutput
from src.agents.registry import AgentRegistry, default_registry
from src.config import load_depth_modes, load_focus_routing, load_orchestrator_config
from src.contracts import REQUIRED_ARTIFACTS, validate_contract
from src.data_gaps import missing_data_map
from src.envelope import Envelope
from src.provenance import ProvenanceLog
from src.runtime_metrics import RuntimeMetricsCollector
from src.utils.checksum import envelope_content_checksum
from src.validators import (
    EnvelopeValidator,
    ValidationResult,
    validate_checksum,
    validate_gate_passed,
    validate_required_artifacts,
)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _strict_mode() -> bool:
    return os.environ.get("FUWFORGE_STRICT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@dataclass
class OrchestratorParams:
    company: str
    ticker: str
    analysis_date: str
    depth: str
    focus: List[str]
    weights: Any


@dataclass
class AgentExecution:
    envelope: Envelope
    raw_data: Dict[str, Any]
    raw_response: Dict[str, Any]
    latency_ms: int
    attempts: int


class FUWForgeOrchestrator:
    """
    Coordinates the end-to-end flow across A1–A7.
    """

    def __init__(
        self,
        project_root: Path,
        registry: Optional[AgentRegistry] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        randomize: bool = False,
        strict: bool = False,
        evidence_pack: Optional[Path] = None,
    ) -> None:
        self.project_root = project_root
        self.evidence_pack_path = evidence_pack
        self.evidence_pack = None
        self.config_dir = project_root / "config"
        self.schema_path = project_root / "schemas" / "envelope_v2_1.json"
        self.depth_modes = load_depth_modes(self.config_dir)
        self.focus_routing = load_focus_routing(self.config_dir)
        self.flow_config = load_orchestrator_config(self.config_dir)
        run_settings = (
            self.flow_config.get("run_settings", {})
            if isinstance(self.flow_config, dict)
            else {}
        )
        self.max_retries = int(run_settings.get("max_retries", 2))
        self.retry_backoff_seconds = float(
            run_settings.get("retry_backoff_seconds", 0.0)
        )
        self.strict = bool(strict) or _strict_mode()
        self.validator = EnvelopeValidator(self.schema_path)

        if registry is not None:
            self.registry = registry
        else:
            self.registry = default_registry(
                provider=provider,
                model=model,
                randomize=randomize,
                max_retries=self.max_retries,
                retry_backoff_seconds=self.retry_backoff_seconds,
                strict=True if self.strict else None,
            )

    def run(
        self,
        run_id: str,
        params: OrchestratorParams,
        mode: str = "automated",
        output_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Execute the gated flow. Writes ``run_summary.json`` even when the run
        halts, then returns metadata about the run.
        """
        output_root = (output_root or (self.project_root / "runs" / run_id)).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        state: Dict[str, Any] = {
            "metrics": RuntimeMetricsCollector(),
            "started_at": _now_iso(),
        }
        try:
            from src.evidence import EvidencePack

            self.evidence_pack = None
            if self.evidence_pack_path:
                pack = EvidencePack(self.evidence_pack_path, ticker=params.ticker, analysis_date=params.analysis_date)
                self.evidence_pack = pack.snapshot(output_root / "evidence")
                state["evidence_checksum"] = pack.checksum
            elif mode == "automated" and self.strict and self.registry.provider not in ("mock", "stub"):
                raise ValueError("Strict model analysis requires --evidence-pack with retrieved source documents.")
            return self._run_pipeline(run_id, params, mode, output_root, state)
        except Exception as exc:
            payload = self._summary_payload(
                run_id, params, state, status="halted", halt_reason=str(exc), outputs={}
            )
            self._write_summary_file(output_root, payload)
            raise

    def _run_pipeline(
        self,
        run_id: str,
        params: OrchestratorParams,
        mode: str,
        output_root: Path,
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        depth_key = params.depth.lower()
        if depth_key not in self.depth_modes:
            raise KeyError(
                f"Unknown depth mode '{params.depth}'. Expected one of {list(self.depth_modes)}."
            )

        depth_config = self.depth_modes[depth_key].payload
        focus_overrides = self._resolve_focus(params.focus)

        provenance = ProvenanceLog(
            output_root / f"{params.ticker}_{params.analysis_date}_provenance.log"
        )
        provenance.load_existing()
        if self.evidence_pack:
            provenance.append("EVIDENCE", f"Retrieved-document snapshot {self.evidence_pack.checksum}; documents={len(self.evidence_pack.documents)}")
            provenance.flush()
        metrics: RuntimeMetricsCollector = state["metrics"]

        frozen_checksum: Optional[str] = None
        envelopes: Dict[str, Envelope] = {}

        # Phase 1 — A1 Gate
        metrics.start_agent("A1")
        execution = self._run_agent(
            "A1",
            params,
            depth_config,
            focus_overrides,
            None,
            run_id,
            output_root,
            provenance,
            mode,
            envelopes,
        )
        a1_env = self._accept_agent(
            "A1",
            execution,
            params,
            depth_config,
            None,
            mode,
            provenance,
            metrics,
            envelopes,
            output_root,
        )

        frozen_checksum = a1_env.metrics.get("data_checksum")
        if not frozen_checksum:  # pragma: no cover - guardrail
            raise RuntimeError("A1 did not emit metrics.data_checksum; cannot proceed.")
        state["checksum"] = frozen_checksum
        envelopes["A1"] = a1_env
        provenance.append(
            "ORCH", f"A1 checkpoint recorded (checksum={frozen_checksum})."
        )
        provenance.flush()

        # Phase 2 — Parallel Core Analysis (A2–A5)
        phase2_ids = ["A2", "A3", "A4", "A5"]
        for aid in phase2_ids:
            metrics.start_agent(aid)

        executions: Dict[str, AgentExecution] = {}
        if mode == "controller":
            for aid in phase2_ids:
                executions[aid] = self._run_agent(
                    aid,
                    params,
                    depth_config,
                    focus_overrides,
                    frozen_checksum,
                    run_id,
                    output_root,
                    provenance,
                    mode,
                    envelopes,
                )
        else:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(4, len(phase2_ids))
            ) as executor:
                futures = {
                    executor.submit(
                        self._run_agent,
                        aid,
                        params,
                        depth_config,
                        focus_overrides,
                        frozen_checksum,
                        run_id,
                        output_root,
                        provenance,
                        mode,
                        envelopes,
                    ): aid
                    for aid in phase2_ids
                }
                for fut in concurrent.futures.as_completed(futures):
                    executions[futures[fut]] = fut.result()

        # Deterministic validation and acceptance in canonical order A2 -> A5
        for agent_id in phase2_ids:
            envelopes[agent_id] = self._accept_agent(
                agent_id,
                executions[agent_id],
                params,
                depth_config,
                frozen_checksum,
                mode,
                provenance,
                metrics,
                envelopes,
                output_root,
            )

        # Phase 3 — Adversarial Loop
        for agent_id in ["A6B", "A6R", "A6"]:
            metrics.start_agent(agent_id)
            execution = self._run_agent(
                agent_id,
                params,
                depth_config,
                focus_overrides,
                frozen_checksum,
                run_id,
                output_root,
                provenance,
                mode,
                envelopes,
            )
            envelopes[agent_id] = self._accept_agent(
                agent_id,
                execution,
                params,
                depth_config,
                frozen_checksum,
                mode,
                provenance,
                metrics,
                envelopes,
                output_root,
            )

        # Phase 4 — Final Assembly (A7)
        metrics.start_agent("A7")
        execution = self._run_agent(
            "A7",
            params,
            depth_config,
            focus_overrides,
            frozen_checksum,
            run_id,
            output_root,
            provenance,
            mode,
            envelopes,
        )
        envelopes["A7"] = self._accept_agent(
            "A7",
            execution,
            params,
            depth_config,
            frozen_checksum,
            mode,
            provenance,
            metrics,
            envelopes,
            output_root,
        )

        outputs = self._write_outputs(
            run_id, params, output_root, envelopes, depth_config, frozen_checksum
        )
        summary = self._summary_payload(
            run_id, params, state, status="completed", halt_reason=None, outputs=outputs
        )
        self._write_summary_file(output_root, summary)
        return summary

    # ------------------------------------------------------------------ agents

    def _run_agent(
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
        envelopes: Optional[Dict[str, Envelope]] = None,
    ) -> AgentExecution:
        """
        Execute or ingest an agent envelope depending on mode, measuring wall-clock latency.
        """
        context = AgentContext(
            run_id=run_id,
            params=params.__dict__,
            depth_config=depth_config,
            focus_overrides=focus_overrides,
            frozen_checksum=frozen_checksum or "",
            workdir=output_root,
            provenance_path=provenance.path,
            envelopes=dict(envelopes or {}),
            source_documents=self.evidence_pack.sources() if self.evidence_pack else [],
            source_text=self.evidence_pack.prompt_text() if self.evidence_pack else "",
            evidence_checksum=self.evidence_pack.checksum if self.evidence_pack else "",
        )

        started = time.perf_counter()
        if mode == "controller":
            path = output_root / f"{agent_id.lower()}_envelope.json"
            if not path.exists():
                raise FileNotFoundError(f"Controller mode expects {path} to exist.")
            data = json.loads(path.read_text(encoding="utf-8"))
            output = AgentOutput(
                envelope=Envelope.from_dict(data),
                raw_response={"controller": True, "raw_data": data},
            )
        else:
            agent_obj = self.registry.resolve(agent_id)
            print(
                f"[{_now_iso()}] [ORCH] Starting agent {agent_id} ({agent_obj.name})...",
                flush=True,
            )
            output = agent_obj.run(context)
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))

        raw_response = output.raw_response or {}
        raw_data = raw_response.get("raw_data")
        if not isinstance(raw_data, dict):
            raw_data = output.envelope.to_dict()
        attempts = 1
        try:
            attempts = int(
                raw_response.get("attempts")
                or (int(raw_response.get("attempt", 0)) + 1)
            )
        except (TypeError, ValueError):
            attempts = 1

        return AgentExecution(
            envelope=output.envelope,
            raw_data=raw_data,
            raw_response=raw_response,
            latency_ms=latency_ms,
            attempts=max(1, attempts),
        )

    def _accept_agent(
        self,
        agent_id: str,
        execution: AgentExecution,
        params: OrchestratorParams,
        depth_config: Dict[str, Any],
        frozen_checksum: Optional[str],
        mode: str,
        provenance: ProvenanceLog,
        metrics: RuntimeMetricsCollector,
        envelopes: Dict[str, Envelope],
        output_root: Path,
    ) -> Envelope:
        """
        Validate an agent result against schema, required artifacts, hard gates,
        and the machine-checkable contract; then persist and record metrics.
        """
        env = execution.envelope

        if mode == "controller":
            if env.content_checksum:
                actual = envelope_content_checksum(execution.raw_data)
                if actual != env.content_checksum:
                    raise RuntimeError(
                        f"{agent_id} content checksum mismatch: envelope content does not match its recorded checksum."
                    )
            else:
                provenance.append(
                    "ORCH",
                    f"{agent_id} envelope carries no content_checksum (legacy or unsealed); run `controller seal` to add integrity.",
                )
                provenance.flush()

        try:
            self._require(
                self.validator.validate(env.to_dict()),
                f"{agent_id} schema validation failed",
                provenance=provenance,
                agent_id=agent_id,
            )
            if frozen_checksum:
                self._require(
                    validate_checksum(env, frozen_checksum),
                    f"{agent_id} checksum mismatch",
                    provenance=provenance,
                    agent_id=agent_id,
                )
            required = self._required_artifacts_for(agent_id)
            if required:
                self._require(
                    validate_required_artifacts(env, required),
                    f"{agent_id} missing required artifacts",
                    provenance=provenance,
                    agent_id=agent_id,
                )

            gate_result = validate_gate_passed(env)
            if not gate_result.valid:
                notes = env.gates.get("notes", [])
                notes_str = "; ".join(notes) if notes else "Gate condition not met."
                if agent_id == "A1":
                    provenance.append("ORCH", f"A1 GOD gate FAILED: {notes_str}")
                    provenance.flush()
                    if "MISSING_DATA_TABLE" in env.sections_md:
                        missing_file = (
                            output_root
                            / f"{params.ticker}_{params.analysis_date}_missing_data.md"
                        )
                        missing_file.write_text(
                            env.sections_md["MISSING_DATA_TABLE"], encoding="utf-8"
                        )
                    self._require(
                        gate_result,
                        "A1 gate failure halts the run",
                        provenance=provenance,
                        agent_id=agent_id,
                    )
                else:
                    self._require(
                        gate_result,
                        f"{agent_id} hard gate failure halts the run",
                        provenance=provenance,
                        agent_id=agent_id,
                    )

            contract = validate_contract(
                env,
                depth_config=depth_config,
                envelopes=envelopes,
                strict=self.strict,
            )
            for warning in contract.warnings:
                env.warnings.append(warning)
                provenance.append("CONTRACT", f"{agent_id} warning: {warning}")
            self._require(
                contract,
                f"{agent_id} contract validation failed",
                provenance=provenance,
                agent_id=agent_id,
            )
        except RuntimeError:
            if mode != "controller":
                self._write_agent_outputs(
                    output_root,
                    agent_id,
                    AgentOutput(envelope=env, raw_response=execution.raw_response),
                )
            raise

        if mode != "controller":
            env.upstream = [
                {
                    "agent": upstream_id,
                    "content_checksum": upstream_env.content_checksum,
                }
                for upstream_id, upstream_env in envelopes.items()
            ]
            execution.raw_data = env.to_dict()
            env.content_checksum = envelope_content_checksum(execution.raw_data)
            execution.raw_data = env.to_dict()
            self._write_agent_outputs(
                output_root,
                agent_id,
                AgentOutput(envelope=env, raw_response=execution.raw_response),
            )

        prompt_hash = execution.raw_response.get("prompt_sha256")
        detail = f"attempts={execution.attempts}, latency_ms={execution.latency_ms}"
        if prompt_hash:
            detail += f", prompt_sha256={prompt_hash}"
        provenance.append("ORCH", f"{agent_id} envelope accepted ({detail}).")
        provenance.flush()

        tokens = int(env.metrics.get("tokens") or 0)
        metrics.finalize_agent(
            agent_id,
            latency_ms=execution.latency_ms,
            tokens=tokens,
            retries=max(0, execution.attempts - 1),
            confidence=env.confidence,
            status="passed",
        )
        print(
            f"[{_now_iso()}] [ORCH] Agent {agent_id} accepted ({execution.latency_ms}ms, attempt {execution.attempts}).",
            flush=True,
        )
        return env

    def _required_artifacts_for(self, agent_id: str) -> Iterable[str]:
        return REQUIRED_ARTIFACTS.get(agent_id, [])

    def _resolve_focus(self, focus_tags: Iterable[str]) -> Dict[str, Any]:
        overrides: Dict[str, Any] = {}
        for tag in focus_tags:
            if tag not in self.focus_routing:
                continue
            overrides[tag] = self.focus_routing[tag]
        return overrides

    # ----------------------------------------------------------------- outputs

    def _write_agent_outputs(
        self, output_root: Path, agent_id: str, output: AgentOutput
    ) -> None:
        env_path = output_root / f"{agent_id.lower()}_envelope.json"
        env_path.write_text(
            json.dumps(output.envelope.to_dict(), indent=2), encoding="utf-8"
        )
        if output.raw_response:
            raw_path = output_root / f"{agent_id.lower()}_raw.json"
            raw_path.write_text(
                json.dumps(output.raw_response, indent=2), encoding="utf-8"
            )

    def _write_outputs(
        self,
        run_id: str,
        params: OrchestratorParams,
        output_root: Path,
        envelopes: Dict[str, Envelope],
        depth_config: Optional[Dict[str, Any]] = None,
        checksum: Optional[str] = None,
    ) -> Dict[str, str]:
        assembled_sections = self._assemble_sections(
            envelopes, depth_config=depth_config
        )
        front_matter = (
            "---\n"
            f"run_id: {run_id}\n"
            f"ticker: {params.ticker}\n"
            f"analysis_date: {params.analysis_date}\n"
            f"depth: {params.depth}\n"
            f"focus: {json.dumps(list(params.focus or []))}\n"
            f"weights: {params.weights if isinstance(params.weights, str) else json.dumps(params.weights)}\n"
            f"frozen_checksum: {checksum}\n"
            f"generated_at: {_now_iso()}\n"
            "---\n\n"
        )
        report_path = output_root / f"{params.ticker}_{params.analysis_date}_report.md"
        injected = [agent_id for agent_id, env in envelopes.items() if env.metrics.get("injected_defaults")]
        unverified = [agent_id for agent_id, env in envelopes.items() if env.metrics.get("source_grounding") == "unverified"]
        notice = ""
        if injected or unverified:
            notice = "> **NOT VERIFIED RESEARCH.** "
            if injected:
                notice += "Runner placeholders were injected for " + ", ".join(injected) + ". "
            if unverified:
                notice += "Retrieved-document grounding is missing for " + ", ".join(unverified) + ". "
            notice += "Do not treat defaults or unsupported claims as investment facts.\n\n"
        elif self.evidence_pack:
            notice = "> **Evidence supplied:** retrieved documents are cached and hash-checked. This does not independently verify every generated claim; review cited facts and scenario assumptions.\n\n"
        gaps = {agent_id: missing_data_map(env.metrics["missing_data"]) for agent_id, env in envelopes.items() if env.metrics.get("missing_data")}
        if gaps:
            notice += "> **Incomplete research coverage:** explicit unavailable inputs remain. Null means unknown, not zero or neutral; review these gaps before using ratings or scenarios.\n\n"
            rows = ["## Research data gaps", "| Agent | Field | Coverage / reason |", "| --- | --- | --- |"]
            for agent_id, missing in sorted(gaps.items()):
                for field, reason in sorted(missing.items()):
                    rows.append(f"| {agent_id} | {field} | {str(reason).replace(chr(124), chr(92) + chr(124)).replace(chr(10), chr(32))} |")
            notice += "\n".join(rows) + "\n\n"
        report_path.write_text(front_matter + notice + assembled_sections, encoding="utf-8")

        artifacts_path = (
            output_root / f"{params.ticker}_{params.analysis_date}_artifacts.json"
        )
        artifacts_payload = {
            agent_id: env.to_dict() for agent_id, env in envelopes.items()
        }
        artifacts_path.write_text(
            json.dumps(artifacts_payload, indent=2), encoding="utf-8"
        )

        provenance_path = (
            output_root / f"{params.ticker}_{params.analysis_date}_provenance.log"
        )

        return {
            "report": str(report_path),
            "artifacts": str(artifacts_path),
            "provenance": str(provenance_path),
            "summary": str(output_root / "run_summary.json"),
        }

    def _assemble_sections(
        self,
        envelopes: Dict[str, Envelope],
        depth_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        import re

        skip_sections = (
            set(depth_config.get("skip_sections", [])) if depth_config else set()
        )

        collected: Dict[str, str] = {}
        gap_sections: List[str] = []
        for env in envelopes.values():
            for key, md in env.sections_md.items():
                text = str(md or "")
                if key.upper() == "MISSING_DATA_TABLE":
                    if text.strip():
                        gap_sections.append(f"### {env.agent.id} unavailable inputs\n\n{text}")
                    continue
                if key in collected and collected[key] != text:
                    raise RuntimeError(
                        f"Conflicting content for section '{key}' across agent envelopes."
                    )
                collected[key] = text

        def section_sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
            key = item[0]
            match = re.match(r"^SECTION_(\d+)", key, re.IGNORECASE)
            if match:
                sec_num = int(match.group(1))
                return (sec_num, 0, key)
            if key.upper() == "AI_STRATEGY":
                return (3, 1, key)
            return (99, 0, key)

        ordered = sorted(collected.items(), key=section_sort_key)
        sections: List[str] = []
        for key, markdown in ordered:
            match = re.match(r"^SECTION_(\d+)", key, re.IGNORECASE)
            if match and int(match.group(1)) in skip_sections:
                continue
            sections.append(str(markdown or "").rstrip() + "\n\n")
        if gap_sections:
            sections.append("## Per-agent coverage diagnostics\n\n" + "\n\n".join(gap_sections) + "\n")
        return "".join(sections)

    # ----------------------------------------------------------------- summary

    def _summary_payload(
        self,
        run_id: str,
        params: OrchestratorParams,
        state: Dict[str, Any],
        status: str,
        halt_reason: Optional[str],
        outputs: Dict[str, str],
    ) -> Dict[str, Any]:
        metrics: RuntimeMetricsCollector = state["metrics"]
        return {
            "run_id": run_id,
            "status": status,
            "halt_reason": halt_reason,
            "provider": getattr(self.registry, "provider", "unknown"),
            "params": params.__dict__,
            "checksum": state.get("checksum"),
            "evidence_checksum": state.get("evidence_checksum"),
            "metrics": metrics.to_summary(),
            "outputs": outputs,
            "started_at": state.get("started_at"),
            "finished_at": _now_iso(),
        }

    def _write_summary_file(self, output_root: Path, payload: Dict[str, Any]) -> None:
        (output_root / "run_summary.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _require(
        self,
        result: ValidationResult,
        message: str,
        provenance: Optional[ProvenanceLog] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        if not result.valid:
            err_msg = f"{message}: {'; '.join(result.errors)}"
            if provenance:
                provenance.append(
                    "ORCH", f"Validation FAILED ({agent_id or 'UNKNOWN'}): {err_msg}"
                )
                provenance.flush()
            raise RuntimeError(err_msg)


# Backwards compatibility alias (legacy naming)
CodexOrchestrator = FUWForgeOrchestrator
