"""
Deterministic orchestrator for FUWForge (Fired Up Wealth) on COA v2.1.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import copy

from src.agents.base import AgentContext, AgentOutput
from src.agents.registry import AgentRegistry, default_registry
from src.config import load_depth_modes, load_focus_routing, load_orchestrator_config
from src.contracts import REQUIRED_ARTIFACTS, validate_contract
from src.data_gaps import missing_data_map
from src.envelope import Envelope
from src.execution_identity import (
    UpstreamRef,
    capture_upstream,
    execution_key,
    upstream_to_envelope_refs,
)
from src.execution_store import (
    ExecutionReceipt,
    ExecutionState,
    ExecutionStore,
    RecoveryChoice,
)
from src.forensic_inputs import forensic_inputs_for
from src.provenance import ProvenanceLog
from src.resources import (
    get_config_dir,
    get_default_runs_dir,
    get_schema_path,
)
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


#: Placeholder digest for an absent prompt/evidence hash. It is a real SHA-256
#: value so execution-identity computation stays total, but it must never be
#: confused with a recorded hash: an agent with no prompt hash or no evidence
#: pack still gets a distinct identity from one that has either.
_EMPTY_SHA256 = "0" * 64


@dataclass
class OrchestratorParams:
    company: str
    ticker: str
    analysis_date: str
    depth: str
    focus: List[str]
    weights: Any
    independent_bear: bool = False


@dataclass
class AgentExecution:
    envelope: Envelope
    raw_data: Dict[str, Any]
    raw_response: Dict[str, Any]
    latency_ms: int
    attempts: int
    #: Parents actually supplied at execution time, captured from a frozen
    #: snapshot. Never re-derived at acceptance from shared mutable state.
    consumed_upstream: Tuple[UpstreamRef, ...] = ()
    #: Identity of the exact rendered request, for receipts and resume.
    execution_key: str = ""
    input_snapshot: Dict[str, Envelope] = field(default_factory=dict)
    reused: bool = False


class FUWForgeOrchestrator:
    """
    Coordinates the end-to-end flow across A1–A7.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        registry: Optional[AgentRegistry] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        randomize: bool = False,
        strict: bool = False,
        evidence_pack: Optional[Path] = None,
        resume: bool = False,
        recovery_choice: Optional[Union[RecoveryChoice, str]] = None,
        independent_bear: bool = False,
        execution_store_path: Optional[Path] = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root is not None else None
        self.evidence_pack_path = evidence_pack
        self.evidence_pack = None
        self.resume = bool(resume)
        self.recovery_choice = recovery_choice
        self.independent_bear = bool(independent_bear)
        self.execution_store_path = execution_store_path
        self.config_dir = get_config_dir(self.project_root)
        self.schema_path = get_schema_path("envelope_v2_1.json", self.project_root)
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
        resume: Optional[bool] = None,
        recovery_choice: Optional[Union[RecoveryChoice, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the gated flow. Writes ``run_summary.json`` and ``run_manifest.json``
        even when the run halts, then returns metadata about the run.
        """
        is_resume = self.resume if resume is None else bool(resume)
        rec_choice = self.recovery_choice if recovery_choice is None else recovery_choice
        if output_root:
            target_root = Path(output_root).resolve()
        else:
            # ``run_id`` is an identifier, not a path. An absolute or parent-
            # relative value would silently place a run outside the runs tree
            # (or onto an unrelated directory), so refuse it and require the
            # positional run_dir for an explicit location.
            identifier = Path(str(run_id))
            if (
                not identifier.name
                or identifier.is_absolute()
                or any(part in ("..", "") for part in identifier.parts)
            ):
                raise ValueError(
                    f"run_id {run_id!r} is not a plain identifier; pass the target "
                    "directory as run_dir instead."
                )
            runs_dir = get_default_runs_dir(self.project_root)
            target_root = (runs_dir / run_id).resolve()

        # Preserve archived runs: default existing output must not silently overwrite; explicit resume allowed.
        if mode == "automated" and not is_resume and target_root.is_dir():
            has_summary = (target_root / "run_summary.json").is_file()
            has_envelopes = bool(list(target_root.glob("*_envelope.json")))
            if has_summary or has_envelopes:
                raise FileExistsError(
                    f"Run directory '{target_root}' already contains an existing run. "
                    "Default execution will not silently overwrite archived runs. Pass resume=True or use a fresh directory."
                )

        target_root.mkdir(parents=True, exist_ok=True)
        state: Dict[str, Any] = {
            "metrics": RuntimeMetricsCollector(),
            "started_at": _now_iso(),
            "envelopes": {},
            "flow_mode": mode,
        }
        store: Optional[ExecutionStore] = None
        try:
            from src.evidence import EvidencePack

            self.evidence_pack = None
            if self.evidence_pack_path:
                pack = EvidencePack(self.evidence_pack_path, ticker=params.ticker, analysis_date=params.analysis_date)
                evidence_dir = target_root / "evidence"
                if evidence_dir.is_dir():
                    # Resume: this run already snapshotted its evidence. Re-snapshotting
                    # would fail on a non-empty directory and, worse, could overwrite
                    # the bytes that earlier envelopes were actually generated from.
                    # Re-load the existing snapshot instead — constructing an
                    # EvidencePack re-verifies every document and attachment against
                    # its recorded digest — and only reuse it when it is the same
                    # evidence this resume was asked to run against.
                    existing = EvidencePack(
                        evidence_dir / "evidence.json",
                        ticker=params.ticker,
                        analysis_date=params.analysis_date,
                    )
                    if existing.checksum != pack.checksum:
                        raise ValueError(
                            f"Run directory '{target_root}' already holds a different "
                            "evidence snapshot than the supplied --evidence-pack. Resuming "
                            "against changed evidence would invalidate prior receipts; "
                            "use a fresh directory instead."
                        )
                    self.evidence_pack = existing
                    state["evidence_reused_snapshot"] = True
                else:
                    self.evidence_pack = pack.snapshot(evidence_dir)
                state["evidence_checksum"] = pack.checksum
            elif mode == "automated" and self.strict and self.registry.provider not in ("mock", "stub"):
                raise ValueError("Strict model analysis requires --evidence-pack with retrieved source documents.")

            if mode != "controller":
                db_path = self.execution_store_path or (target_root / "execution_ledger.db")
                store = ExecutionStore(db_path)
                if is_resume:
                    store.reconcile_unvalidated_on_resume()
                    unvalidated = [r for r in store.list_receipts() if r.requires_recovery()]
                    if unvalidated:
                        if rec_choice:
                            for r in unvalidated:
                                store.resolve_recovery(r.execution_key, rec_choice)
                        else:
                            needing = [r for r in unvalidated if r.requires_recovery()]
                            if needing:
                                raise RuntimeError(
                                    f"Uncertain completion(s) detected for agents {[r.agent_id for r in needing]} on resume. "
                                    "An explicit recovery choice is required: specify recovery_choice='retry' or 'abandon'."
                                )

            return self._run_pipeline(
                run_id=run_id,
                params=params,
                mode=mode,
                output_root=target_root,
                state=state,
                store=store,
                resume=is_resume,
            )
        except Exception as exc:
            payload = self._summary_payload(
                run_id, params, state, status="halted", halt_reason=str(exc), outputs={}
            )
            self._write_summary_file(target_root, payload)
            try:
                from src.run_semantic_audit import build_run_manifest

                halted_envelopes = dict(state.get("envelopes", {}))
                if not halted_envelopes:
                    for ef in sorted(target_root.glob("*_envelope.json")):
                        try:
                            d = json.loads(ef.read_text(encoding="utf-8"))
                            env_obj = Envelope.from_dict(d)
                            halted_envelopes[env_obj.agent.id] = env_obj
                        except Exception:
                            pass
                manifest = build_run_manifest(
                    target_root,
                    halted_envelopes,
                    run_id=run_id,
                    ticker=params.ticker,
                    analysis_date=params.analysis_date,
                    status="halted",
                    halt_reason=str(exc),
                    summary=payload,
                )
                (target_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            except Exception as m_exc:
                prov = state.get("provenance")
                if prov:
                    prov.append("ORCH", f"Halted manifest creation notice: {m_exc}")
                    prov.flush()
            raise
        finally:
            if store is not None:
                store.close()

    def _run_pipeline(
        self,
        run_id: str,
        params: OrchestratorParams,
        mode: str,
        output_root: Path,
        state: Dict[str, Any],
        store: Optional[ExecutionStore] = None,
        resume: bool = False,
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
        state["provenance"] = provenance

        frozen_checksum: Optional[str] = None
        envelopes: Dict[str, Envelope] = {}
        state["envelopes"] = envelopes

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
            store=store,
            resume=resume,
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
            store=store,
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
                    store=store,
                    resume=resume,
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
                        store,
                        resume,
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
                store=store,
            )

        # Phase 3 — Adversarial Loop
        independent_bear = getattr(params, "independent_bear", False) or self.independent_bear

        # A6B — Bull Case
        metrics.start_agent("A6B")
        execution_a6b = self._run_agent(
            "A6B",
            params,
            depth_config,
            focus_overrides,
            frozen_checksum,
            run_id,
            output_root,
            provenance,
            mode,
            envelopes,
            store=store,
            resume=resume,
        )
        envelopes["A6B"] = self._accept_agent(
            "A6B",
            execution_a6b,
            params,
            depth_config,
            frozen_checksum,
            mode,
            provenance,
            metrics,
            envelopes,
            output_root,
            store=store,
        )

        # A6R — Bear Case / Rebuttals
        metrics.start_agent("A6R")
        if independent_bear and mode != "controller":
            # Step 1: Run independent initial bear pass against A1-A5 only (without seeing A6B)
            bear_initial_inputs = {
                aid: envelopes[aid]
                for aid in ["A1", "A2", "A3", "A4", "A5"]
                if aid in envelopes
            }
            initial_bear_exec = self._run_agent(
                "A6R",
                params,
                depth_config,
                focus_overrides,
                frozen_checksum,
                run_id,
                output_root,
                provenance,
                mode,
                bear_initial_inputs,
                store=store,
                resume=resume,
                challenge_initial={"phase": "independent", "instructions": "Generate independent bearish hypotheses without the bull case. Use local thesis identifiers in bull_id; reconciliation will map these later."},
            )
            initial_root = output_root / "challenge_initial"
            initial_root.mkdir(exist_ok=True)
            initial_env = self._accept_agent(
                "A6R", initial_bear_exec, params, depth_config, frozen_checksum,
                mode, provenance, metrics, bear_initial_inputs, initial_root, store=store,
            )
            # Step 2: Run reconciled pass with full upstream (A1-A5 + A6B)
            reconciled_bear_exec = self._run_agent(
                "A6R",
                params,
                depth_config,
                focus_overrides,
                frozen_checksum,
                run_id,
                output_root,
                provenance,
                mode,
                envelopes,
                store=store,
                resume=resume,
                challenge_initial=initial_env.to_dict(),
            )
            # Preserve initial bear theses and reconciliation record inside A6R artifacts
            init_theses = (
                initial_bear_exec.envelope.artifacts.get("BearRebuttals")
                or initial_bear_exec.envelope.artifacts.get("Headwinds")
                or []
            )
            if not reconciled_bear_exec.reused:
                reconciled_bear_exec.envelope.artifacts["initial_bear_theses"] = init_theses
                reconciled_bear_exec.envelope.artifacts["_independent_bear_reconciliation"] = {
                    "initial_theses_count": len(init_theses),
                    "reconciled_theses_count": len(reconciled_bear_exec.envelope.artifacts.get("BearRebuttals") or []),
                    "initial_envelope": initial_env.to_dict(),
                    "initial_checksum": initial_env.content_checksum,
                    "extra_calls_max": 1,
                    "promotion_status": "experimental_pending_cost_and_review_evaluation",
                }
            envelopes["A6R"] = self._accept_agent(
                "A6R",
                reconciled_bear_exec,
                params,
                depth_config,
                frozen_checksum,
                mode,
                provenance,
                metrics,
                envelopes,
                output_root,
                store=store,
            )
        else:
            execution_a6r = self._run_agent(
                "A6R",
                params,
                depth_config,
                focus_overrides,
                frozen_checksum,
                run_id,
                output_root,
                provenance,
                mode,
                envelopes,
                store=store,
                resume=resume,
            )
            envelopes["A6R"] = self._accept_agent(
                "A6R",
                execution_a6r,
                params,
                depth_config,
                frozen_checksum,
                mode,
                provenance,
                metrics,
                envelopes,
                output_root,
                store=store,
            )

        # A6 — CIO Synthesis
        metrics.start_agent("A6")
        execution_a6 = self._run_agent(
            "A6",
            params,
            depth_config,
            focus_overrides,
            frozen_checksum,
            run_id,
            output_root,
            provenance,
            mode,
            envelopes,
            store=store,
            resume=resume,
        )
        envelopes["A6"] = self._accept_agent(
            "A6",
            execution_a6,
            params,
            depth_config,
            frozen_checksum,
            mode,
            provenance,
            metrics,
            envelopes,
            output_root,
            store=store,
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
            store=store,
            resume=resume,
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
            store=store,
        )

        outputs = self._write_outputs(
            run_id, params, output_root, envelopes, depth_config, frozen_checksum
        )
        summary = self._summary_payload(
            run_id, params, state, status="completed", halt_reason=None, outputs=outputs
        )
        self._write_summary_file(output_root, summary)

        # Bind the exact final bytes into a run manifest
        from src.run_semantic_audit import build_run_manifest
        manifest = build_run_manifest(
            output_root,
            envelopes,
            run_id=run_id,
            ticker=params.ticker,
            analysis_date=params.analysis_date,
            status="completed",
            summary=summary,
        )
        manifest_path = output_root / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        outputs["manifest"] = str(manifest_path)

        return summary

    # ------------------------------------------------------------------ agents

    def _run_agent(
        self, agent_id: str, params: OrchestratorParams,
        depth_config: Dict[str, Any], focus_overrides: Dict[str, Any],
        frozen_checksum: Optional[str], run_id: str, output_root: Path,
        provenance: ProvenanceLog, mode: str,
        envelopes: Optional[Dict[str, Envelope]] = None,
        store: Optional[ExecutionStore] = None, resume: bool = False,
        challenge_initial: Optional[Dict[str, Any]] = None,
    ) -> AgentExecution:
        """Freeze inputs and persist a request before execution; reuse validated bytes only."""
        import hashlib
        import inspect
        from src.utils.beneish import CALCULATOR_VERSION

        def digest(value: Any) -> str:
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                allow_nan=False).encode()).hexdigest()

        snapshot = copy.deepcopy(envelopes or {})
        refs = capture_upstream({key: env.to_dict() for key, env in snapshot.items()}) if mode != "controller" else ()
        ledger = copy.deepcopy({
            fact.id: fact for fact in self.evidence_pack.get_facts(as_of=params.analysis_date)
        }) if self.evidence_pack else {}
        context = AgentContext(
            run_id=run_id, params=copy.deepcopy(params.__dict__),
            depth_config=copy.deepcopy(depth_config), focus_overrides=copy.deepcopy(focus_overrides),
            frozen_checksum=frozen_checksum or "", workdir=output_root,
            provenance_path=provenance.path, envelopes=snapshot,
            source_documents=self.evidence_pack.sources() if self.evidence_pack else [],
            source_text=self.evidence_pack.prompt_text() if self.evidence_pack else "",
            evidence_checksum=self.evidence_pack.checksum if self.evidence_pack else "",
            fact_ledger=ledger,
            forensic_inputs=forensic_inputs_for(ledger, as_of=params.analysis_date),
            challenge_initial=copy.deepcopy(challenge_initial),
        )
        started = time.perf_counter()
        key = ""
        identity: Dict[str, Any] = {}
        if mode == "controller":
            path = output_root / f"{agent_id.lower()}_envelope.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            output = AgentOutput(Envelope.from_dict(data), {"controller": True, "raw_data": data})
        else:
            agent = self.registry.resolve(agent_id)
            backend = getattr(agent, "backend", None)
            provider = self.registry.provider
            model = str(getattr(backend, "model", None) or getattr(self.registry, "model", None) or type(agent).__qualname__)
            prompt = agent.build_prompt(context)
            # Include non-prompt output-affecting inputs and implementation/config versions.
            source_path = inspect.getsourcefile(type(agent))
            code_hash = hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else type(agent).__qualname__
            request_hash = digest({"agent": agent_id, "prompt": prompt, "params": context.params,
                "depth": depth_config, "focus": focus_overrides, "strict": self.strict,
                "flow": self.flow_config, "calculator": CALCULATOR_VERSION,
                "code": code_hash, "randomize": getattr(agent, "randomize", False),
                "challenge_initial": challenge_initial,
                "schema": hashlib.sha256(self.schema_path.read_bytes()).hexdigest()})
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
            evidence_hash = digest(context.evidence_checksum or None)
            key = execution_key(provider=provider, model=model, prompt_sha256=request_hash,
                evidence_sha256=evidence_hash, upstream=refs)
            identity = {"provider": provider, "model": model, "prompt_sha256": prompt_hash,
                "request_sha256": request_hash, "evidence_sha256": evidence_hash}
            if store and resume:
                receipt = store.get(key)
                if receipt and receipt.is_safely_reusable():
                    payload = receipt.accepted_envelope
                    if not isinstance(payload, dict) or envelope_content_checksum(payload) != receipt.accepted_checksum:
                        raise RuntimeError(f"{agent_id}: reusable receipt checksum mismatch")
                    env = Envelope.from_dict(payload)
                    return AgentExecution(env, env.to_dict(), {**identity, "reused": True, "usage": receipt.usage},
                        0, 1, refs, key, snapshot, True)
                if receipt and receipt.recovery_choice == RecoveryChoice.ABANDON.value:
                    raise RuntimeError(f"{agent_id}: execution explicitly abandoned")
                if receipt and receipt.raw_response is not None:
                    # The provider already answered and the bytes are on disk. A
                    # local normalization or validation failure is recoverable
                    # without paying for the same completion twice: re-run the
                    # local pipeline over the stored response and accept it if it
                    # now passes. This is recovery, not a retry — the provider is
                    # not called.
                    recovered = self._recover_saved_response(
                        agent_id, receipt, context, store, key, identity, refs, snapshot
                    )
                    if recovered is not None:
                        return recovered
                if receipt and receipt.state == ExecutionState.UNKNOWN_COMPLETION.value:
                    if receipt.recovery_choice == RecoveryChoice.RETRY.value:
                        # An explicit operator decision, recorded on the receipt.
                        # Dispatch proceeds below and store.begin() clears the
                        # stale attempt outcome.
                        receipt = None
                    else:
                        raise RuntimeError(
                            f"{agent_id}: uncertain completion requires --recovery-choice retry or abandon"
                        )
            if store:
                store.begin(agent_id=agent_id, provider=provider, model=model,
                    prompt_sha256=request_hash, evidence_sha256=evidence_hash, upstream=refs,
                    execution_key=key, metadata=identity)
                # ModelAgent invokes this immediately upon provider return, before parsing.
                context.on_provider_return = lambda raw: store.mark_returned(
                    key, {"raw": str(raw)}, usage=getattr(raw, "usage", None))
                context.on_repair_start = lambda error, prompt_hash: store.begin_repair(key, error, prompt_hash)
            print(f"[{_now_iso()}] [ORCH] Starting agent {agent_id} ({agent.name})...", flush=True)
            try:
                output = agent.run(context)
            except Exception:
                if store:
                    receipt = store.get(key)
                    if receipt and receipt.state == ExecutionState.RETURNED.value:
                        store.mark_failed(key, "provider returned but local normalization/validation failed")
                    else:
                        store.mark_unknown_completion(key, reason="execution interrupted or provider completion uncertain")
                raise
            if store:
                store.mark_returned(key, output.raw_response or {"envelope": output.envelope.to_dict()},
                    usage=output.raw_response.get("usage"),
                    latency_ms=max(1, int((time.perf_counter() - started) * 1000)))
        raw = {**(output.raw_response or {}), **identity}
        data = raw.get("raw_data")
        return AgentExecution(output.envelope, data if isinstance(data, dict) else output.envelope.to_dict(),
            raw, max(1, int((time.perf_counter() - started) * 1000)),
            max(1, int(raw.get("attempts", 1))), refs, key, snapshot)

    def _recover_saved_response(
        self,
        agent_id: str,
        receipt: ExecutionReceipt,
        context: AgentContext,
        store: ExecutionStore,
        key: str,
        identity: Dict[str, Any],
        refs: Tuple[UpstreamRef, ...],
        snapshot: Dict[str, Envelope],
    ) -> Optional[AgentExecution]:
        """Re-run the local pipeline over an already-received provider response.

        A run that received a completion and then failed *locally* — malformed
        JSON, a normalization error, a contract violation — has already paid for
        that completion. Resuming it used to leave two options: dispatch again
        (paying twice for one answer) or abandon the run. Neither is necessary
        when the bytes are on disk.

        What this does NOT do is relax any check. The stored text is parsed and
        normalized through exactly the same path as a fresh response, and the
        recovered envelope is accepted only if it validates. A response that was
        genuinely bad stays bad; the difference is that a response which failed
        because of a local defect is no longer indistinguishable from one the
        provider got wrong.

        Returns ``None`` when recovery is not possible, leaving the caller's
        existing retry/abandon handling untouched.
        """
        raw_response = receipt.raw_response
        if not isinstance(raw_response, dict):
            return None
        stored_text = raw_response.get("raw")
        if not isinstance(stored_text, str) or not stored_text.strip():
            # The receipt carries an envelope rather than provider text (for
            # example a controller-mode write). Nothing to re-normalize.
            return None

        from src.agents.model import extract_json_payload, summarize_provider_usage

        agent = self.registry.resolve(agent_id)
        try:
            parsed = extract_json_payload(stored_text)
            normalize = getattr(agent, "_normalize_envelope", None)
            data = normalize(parsed, context) if callable(normalize) else parsed
            recorded_usage = receipt.usage
            if not isinstance(recorded_usage, dict) or "attempts" not in recorded_usage:
                history = [r.usage for r in store.get_by_agent(agent_id)
                           if (r.metadata or {}).get("repair_of") == key]
                recorded_usage = summarize_provider_usage([*history, recorded_usage])
            data["metrics"]["tokens"] = recorded_usage.get("total_tokens")
            env = Envelope.from_dict(data)
            contract = validate_contract(
                env,
                depth_config=context.depth_config,
                envelopes=snapshot,
                strict=self.strict,
                fact_ledger=context.fact_ledger or None,
                expected_company=str(context.params.get("ticker") or "").upper() or None,
                cutoff=context.params.get("analysis_date"),
            )
            if not contract.valid:
                store.mark_failed(
                    key,
                    "saved provider response still fails local validation: "
                    + "; ".join(contract.errors)[:500],
                )
                return None
        except Exception as exc:
            store.mark_failed(
                key,
                f"saved provider response could not be recovered locally "
                f"({type(exc).__name__}: {exc})"[:500],
            )
            return None

        recovered_raw = {
            **raw_response,
            "agent": agent_id,
            "recovered_locally": True,
            "prior_state": receipt.state,
            "prior_error": receipt.error,
            "usage": recorded_usage,
            **identity,
        }
        store.mark_returned(key, recovered_raw, usage=recorded_usage)
        print(
            f"[{_now_iso()}] [ORCH] Recovered {agent_id} from the saved provider "
            "response; no provider call was made.",
            flush=True,
        )
        return AgentExecution(
            env,
            env.to_dict(),
            recovered_raw,
            0,
            1,
            refs,
            key,
            snapshot,
            False,
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
        store: Optional[ExecutionStore] = None,
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
                # Cross-agent checks (A6/A6R bull-bear linkage) must see the
                # same upstream this agent actually consumed, not whatever has
                # been accepted since its prompt was rendered.
                envelopes=execution.input_snapshot,
                strict=self.strict,
                fact_ledger=(
                    {fact.id: fact for fact in self.evidence_pack.get_facts(as_of=params.analysis_date)}
                    if self.evidence_pack else None
                ),
                expected_company=params.ticker.upper(),
                cutoff=params.analysis_date,
            )
            # A reused envelope carries the exact bytes that were sealed and
            # checksummed when it was first accepted. Re-appending contract
            # warnings here would mutate that sealed content while the reuse
            # branch below deliberately leaves content_checksum untouched,
            # leaving a stale checksum that makes the NEXT resume fail with
            # "reusable receipt checksum mismatch". Validation still runs, so a
            # reused envelope can never quietly bypass its contract.
            if execution.reused:
                for warning in contract.warnings:
                    provenance.append("CONTRACT", f"{agent_id} warning: {warning}")
            else:
                for warning in contract.warnings:
                    if warning not in env.warnings:
                        env.warnings.append(warning)
                    provenance.append("CONTRACT", f"{agent_id} warning: {warning}")
            self._require(
                contract,
                f"{agent_id} contract validation failed",
                provenance=provenance,
                agent_id=agent_id,
            )
        except RuntimeError:
            if store and execution.execution_key:
                store.mark_failed(execution.execution_key, "acceptance validation failed")
            if mode != "controller":
                self._write_agent_outputs(
                    output_root,
                    agent_id,
                    AgentOutput(envelope=env, raw_response=execution.raw_response),
                )
            raise

        if mode != "controller" and not execution.reused:
            # Seal the lineage captured at execution time. The parallel phase
            # collects all four results before accepting any, so reading
            # `envelopes` here would attribute earlier-accepted siblings as
            # inputs that were never rendered into this agent's prompt.
            env.upstream = upstream_to_envelope_refs(execution.consumed_upstream)
            # Record the actually-consumed parent set inside the sealed payload
            # so an audit can compare it against the claimed lineage. This lives
            # in metrics.missing_data-style semantic content, not the excluded
            # telemetry block: the content checksum covers it via
            # research_missing_data only, so store it as an explicit artifact
            # instead to guarantee it is hashed.
            env.artifacts["_execution_lineage"] = {
                "consumed_upstream": [ref.agent for ref in execution.consumed_upstream],
                "execution_key": execution.execution_key or None,
                "upstream": [{"agent": r.agent, "content_checksum": r.content_checksum} for r in execution.consumed_upstream],
                **{k: execution.raw_response[k] for k in ("prompt_sha256", "request_sha256", "evidence_sha256", "provider", "model") if k in execution.raw_response},
            }
            execution.raw_data = env.to_dict()
            env.content_checksum = envelope_content_checksum(execution.raw_data)
            execution.raw_data = env.to_dict()
            self._write_agent_outputs(
                output_root,
                agent_id,
                AgentOutput(envelope=env, raw_response=execution.raw_response),
            )

        if mode != "controller" and execution.reused:
            self._write_agent_outputs(output_root, agent_id, AgentOutput(env, execution.raw_response))
        if store and execution.execution_key:
            store.mark_validated(execution.execution_key, env.to_dict(), accepted_checksum=env.content_checksum)

        prompt_hash = execution.raw_response.get("prompt_sha256")
        detail = f"attempts={execution.attempts}, latency_ms={execution.latency_ms}"
        if prompt_hash:
            detail += f", prompt_sha256={prompt_hash}"
        if execution.execution_key:
            detail += f", execution_key={execution.execution_key[:16]}"
        consumed = [ref.agent for ref in execution.consumed_upstream]
        if consumed:
            detail += ", consumed_upstream=" + "+".join(consumed)
        provenance.append("ORCH", f"{agent_id} envelope accepted ({detail}).")
        provenance.flush()

        usage = execution.raw_response.get("usage")
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else env.metrics.get("tokens")
        tokens = int(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else None
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
        # The report is the artifact a reader forwards; it must name the backend
        # that produced it. Without this, a fixture-generated "Buy" rating is
        # byte-for-byte indistinguishable in its header from a live analysis.
        provider_name = getattr(self.registry, "provider", "unknown")
        model_name = getattr(self.registry, "model", None) or "not-applicable"
        front_matter = (
            "---\n"
            f"run_id: {run_id}\n"
            f"ticker: {params.ticker}\n"
            f"analysis_date: {params.analysis_date}\n"
            f"depth: {params.depth}\n"
            f"provider: {provider_name}\n"
            f"model: {model_name}\n"
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
        if str(provider_name).strip().lower() != "mock":
            notice += (
                "> **Review generated claims:** Beneish and Altman scores are screening signals; "
                "they do not verify financial integrity or prove the absence of manipulation.\n\n"
            )
        if str(provider_name).strip().lower() == "mock":
            notice += (
                "> **MOCK PROVIDER — SYNTHETIC OUTPUT.** Every figure and paragraph below "
                "comes from built-in fixtures, not from retrieved filings or a language "
                "model. This run exercises pipeline mechanics only: it is not research and "
                "must not be cited as analysis.\n\n"
            )
        if injected or unverified:
            notice += "> **NOT VERIFIED RESEARCH.** "
            if injected:
                notice += "Runner placeholders were injected for " + ", ".join(injected) + ". "
            if unverified:
                notice += "Retrieved-document grounding is missing for " + ", ".join(unverified) + ". "
            notice += "Do not treat defaults or unsupported claims as investment facts.\n\n"
        elif self.evidence_pack:
            notice += "> **Evidence supplied:** retrieved documents are cached and hash-checked. This does not independently verify every generated claim; review cited facts and scenario assumptions.\n\n"
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

        # Bind the exact final bytes into a run manifest so an independent audit
        # can detect a report or artifact bundle edited after sealing.
        try:
            from src.run_semantic_audit import build_run_manifest

            manifest = build_run_manifest(
                output_root,
                envelopes,
                run_id=run_id,
                ticker=params.ticker,
                analysis_date=params.analysis_date,
                status="completed",
                summary={
                    "provider": getattr(self.registry, "provider", "unknown"),
                    "checksum": checksum,
                    "evidence_checksum": (self.evidence_pack.checksum if self.evidence_pack else None),
                    "params": params.__dict__,
                },
            )
            manifest_path = output_root / "run_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - never fail a run over the manifest
            provenance.append("ORCH", f"Run manifest could not be written: {exc}")
            provenance.flush()

        return {
            "report": str(report_path),
            "artifacts": str(artifacts_path),
            "provenance": str(provenance_path),
            "summary": str(output_root / "run_summary.json"),
            "manifest": str(output_root / "run_manifest.json"),
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
        registry_model = getattr(self.registry, "model", None)
        return {
            "run_id": run_id,
            "status": status,
            "halt_reason": halt_reason,
            "provider": getattr(self.registry, "provider", "unknown"),
            # True backend identity when known; otherwise an explicit sentinel, never a guess.
            "model": registry_model if registry_model else "not-applicable",
            "strict": self.strict,
            # Real execution mode recorded from run(..., mode=...), not a params field.
            "flow_mode": state.get("flow_mode", "automated"),
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
