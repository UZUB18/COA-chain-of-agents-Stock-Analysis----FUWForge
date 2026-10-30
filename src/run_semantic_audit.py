"""
Independent semantic and calculation audit for a completed or halted run.

:func:`src.run_audit.verify_run` answers "do the stored bytes match their
recorded hashes?". That question is necessary and it is not sufficient. This
module adds the levels that hash verification cannot reach:

===================  ==========================================================
Level                Establishes                              Does not establish
===================  ==========================================================
envelope_integrity   Stored bytes match their recorded        Facts are true
                     content checksums
source_reconstruction Retrieved raw bytes can be restored    The model read them
                     and re-hash to the manifest
graph_and_contracts Required role set, no duplicates,       Arithmetic is correct
                     consumed parents, contracts              (see below)
calculation_replay   Reported numbers match pinned formulas Not a forward view
                     and recorded inputs
final_artifacts      The exact report and artifact bundle    The prose is sound
                     bytes match the manifest
===================  ==========================================================

Two rules the implementation holds to:

1. ``calculation_replay`` never reports success because a number was finite and
   in range. It recomputes from the recorded inputs, and reports
   ``not_replayable`` when the inputs were not recorded, because "cannot check"
   is not "checked".
2. A run that did not complete every role is reported as incomplete. Its
   completed subgraph is verified, but it is never presented as a finished
   research report.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from src.contracts import AGENT_IDS, _collect_embedded_witnesses, validate_contract
from src.envelope import Envelope
from src.fact_binding import binding_failures, binding_notes, verify_witness_bindings
from src.resources import get_config_dir, get_schema_path
from src.utils.checksum import checksum_file, envelope_content_checksum
from src.validators import EnvelopeValidator, ValidationResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The independent levels, in the order a reviewer should read them.
AUDIT_LEVELS = (
    "envelope_integrity",
    "source_reconstruction",
    "graph_and_contracts",
    "calculation_replay",
    "final_artifacts",
    "completeness",
)

#: The expected role set for a run that claims to be complete.
EXPECTED_ROLES = tuple(AGENT_IDS)


@dataclass
class AuditReport:
    """Per-level audit result. Levels are independent; none implies another."""

    levels: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: Warnings that remain informational under ``--strict``.
    strict_exempt_warnings: Set[str] = field(default_factory=set)
    #: What a level could NOT establish. These are not defects and are not
    #: escalated by ``--strict``: they state the boundary of what was verified,
    #: so "the arithmetic replayed" is never confused with "every input was
    #: source-backed".
    unavailable: List[str] = field(default_factory=list)
    complete: bool = False
    halted: bool = False
    witnesses_attempted: int = 0
    witnesses_replayed: int = 0
    inputs_verified: int = 0
    inputs_unverified: int = 0
    core_witnesses_replayed: int = 0
    core_inputs_verified: int = 0
    core_inputs_unverified: int = 0

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def overall_state(self) -> str:
        if self.errors:
            return "failed"
        if self.halted or not self.complete:
            return "incomplete"
        return "verified"

    def source_backed_acceptance(self) -> List[str]:
        """The unmet conditions of the opt-in source-backed gate.

        This is the single source of truth for both the gate and the assurance
        label, so the check that fails a run and the label a reader sees can
        never drift apart.
        """
        unmet: List[str] = []
        if self.levels.get("source_reconstruction") != "source_bytes_verified":
            unmet.append(
                "run requires verified source bytes "
                f"(source_reconstruction is '{self.levels.get('source_reconstruction')}')"
            )
        # A borrowed identity fact cannot substitute for core financial forensics.
        from src.utils.beneish import BENEISH_INPUT_KEYS

        if (
            self.core_witnesses_replayed != 1
            or self.core_inputs_verified != len(BENEISH_INPUT_KEYS)
            or self.core_inputs_unverified != 0
        ):
            unmet.append(
                "A3 BeneishM must replay with all eight source-verified index inputs "
                "and no unverified core inputs"
            )
        if self.witnesses_replayed < 1:
            unmet.append("no deterministic calculation witnesses replayed (expected >= 1)")
        if self.inputs_verified < 1:
            unmet.append("no verified financial inputs found (expected >= 1)")
        if self.unavailable:
            unmet.append(f"{len(self.unavailable)} calculation gap(s) disclosed")
        return unmet

    @property
    def assurance(self) -> Dict[str, Any]:
        """What this audit result actually establishes, stated as one label.

        ``overall_state`` answers "did the audit pass". It is not an assurance
        level: a run whose forensics rest on disclosed gaps passes the ordinary
        audit, and calling that "verified" invites the reader to hear "the
        financial analysis was validated". Those are different claims and the
        result must not blur them.
        """
        unmet = self.source_backed_acceptance()
        if self.errors:
            label = "failed"
            means = "The audit found defects; no assurance level applies until they are resolved."
        elif self.halted or not self.complete:
            label = "incomplete"
            means = "Only the completed subgraph was audited. This is not a complete report."
        elif not unmet:
            label = "source_backed"
            means = (
                "Sealed bytes match, the core forensic witness replayed, and all eight "
                "core inputs bound to typed facts with no disclosed calculation gap."
            )
        else:
            label = "internally_consistent_with_disclosed_gaps"
            means = (
                "Sealed bytes match and the structure is internally consistent, but the "
                "run is not source-backed end to end."
            )
        return {
            "label": label,
            "means": means,
            "does_not_mean": (
                "It is not a statement that the issuer's disclosures are accurate, that "
                "the economic inputs were the right ones, or that the investment "
                "conclusion follows from the evidence."
            ),
            "unmet_source_backed_conditions": unmet,
        }

    def failed_levels(self) -> List[str]:
        return [name for name, state in self.levels.items() if state == "failed"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_state": self.overall_state,
            "assurance": self.assurance,
            "complete": self.complete,
            "halted": self.halted,
            "levels": dict(self.levels),
            "failed_levels": self.failed_levels(),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "unavailable": list(self.unavailable),
            "witnesses_attempted": self.witnesses_attempted,
            "witnesses_replayed": self.witnesses_replayed,
            "inputs_verified": self.inputs_verified,
            "inputs_bound": self.inputs_verified,
            "inputs_unverified": self.inputs_unverified,
            "coverage": {
                "witnesses_attempted": self.witnesses_attempted,
                "witnesses_replayed": self.witnesses_replayed,
                "inputs_verified": self.inputs_verified,
                "inputs_bound": self.inputs_verified,
                "inputs_unverified": self.inputs_unverified,
                "core_witnesses_replayed": self.core_witnesses_replayed,
                "core_inputs_verified": self.core_inputs_verified,
                "core_inputs_unverified": self.core_inputs_unverified,
            },
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------------------- manifest


def _is_safe_path(base_dir: Path, target_path: Union[str, Path]) -> Tuple[bool, Optional[Path]]:
    """
    Ensure target_path does not escape base_dir via directory traversal or symlinks.
    Returns (is_safe, resolved_path).
    """
    try:
        base_resolved = base_dir.resolve()
        raw_target = Path(target_path)
        if raw_target.is_absolute():
            return False, None
        if ".." in raw_target.parts:
            return False, None
        full_path = base_dir / raw_target
        if full_path.is_symlink():
            link_target = full_path.resolve()
            try:
                link_target.relative_to(base_resolved)
            except ValueError:
                return False, None
        resolved = full_path.resolve()
        try:
            resolved.relative_to(base_resolved)
            return True, full_path
        except ValueError:
            return False, None
    except Exception:
        return False, None


def find_graph_cycles(graph: Dict[str, List[str]]) -> List[List[str]]:
    """Detect cycles in directed graph. Returns list of cycles as node chains."""
    visited: Dict[str, int] = {}  # 0=unvisited, 1=visiting, 2=visited
    cycles: List[List[str]] = []
    path_stack: List[str] = []

    def dfs(node: str) -> None:
        visited[node] = 1
        path_stack.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited or visited[neighbor] == 0:
                dfs(neighbor)
            elif visited[neighbor] == 1:
                cycle_start_idx = path_stack.index(neighbor)
                cycle = path_stack[cycle_start_idx:] + [neighbor]
                cycles.append(cycle)
        path_stack.pop()
        visited[node] = 2

    for node in sorted(graph.keys()):
        if visited.get(node, 0) == 0:
            dfs(node)
    return cycles


def _output_record(path: Path, run_dir: Path) -> Dict[str, Any]:
    return {
        "path": str(path.relative_to(run_dir)),
        "sha256": checksum_file(path),
        "bytes": path.stat().st_size,
    }


def build_run_manifest(
    run_dir: Path,
    envelopes: Dict[str, Envelope],
    *,
    run_id: str,
    ticker: str,
    analysis_date: str,
    status: str,
    halt_reason: Optional[str] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Bind one run's identity, graph, outputs and versions into a single record.

    The manifest is what the audit re-checks. It is written at run completion so
    the exact final bytes of the report and artifact bundle are pinned; editing
    either afterwards becomes a detectable ``final_artifacts`` failure.
    """
    summary = summary or {}
    roles: Dict[str, str] = {}
    graph: Dict[str, List[str]] = {}
    prompt_hashes: Dict[str, str] = {}
    execution_keys: Dict[str, str] = {}
    for agent_id, env in sorted(envelopes.items()):
        if env.content_checksum:
            roles[agent_id] = env.content_checksum
        graph[agent_id] = sorted(
            str(entry.get("agent")) for entry in (env.upstream or []) if isinstance(entry, dict) and entry.get("agent")
        )
        stored_prompt = (env.metrics or {}).get("prompt_sha256") or (
            (env.artifacts or {}).get("_execution_lineage", {}).get("prompt_sha256")
        )
        if stored_prompt:
            prompt_hashes[agent_id] = str(stored_prompt)
        stored_key = (env.metrics or {}).get("execution_key") or (
            (env.artifacts or {}).get("_execution_lineage", {}).get("execution_key")
        )
        if stored_key:
            execution_keys[agent_id] = str(stored_key)

    outputs: Dict[str, Any] = {}
    for pattern, name in (
        ("*_report.md", "report"),
        ("*_artifacts.json", "artifacts"),
        ("*_provenance.log", "provenance"),
        ("run_summary.json", "summary"),
    ):
        for path in sorted(run_dir.glob(pattern)):
            outputs[name] = _output_record(path, run_dir)
            break

    provenance_out: Dict[str, Any] = {}
    if "provenance" in outputs:
        prov_path = run_dir / outputs["provenance"]["path"]
        tail_checksum = None
        if prov_path.is_file():
            try:
                from src.provenance import ProvenanceLog

                plog = ProvenanceLog(prov_path)
                plog.load_existing()
                tail_checksum = plog.tail_checksum
            except Exception:
                tail_checksum = None
        provenance_out = {
            "path": outputs["provenance"]["path"],
            "tail": tail_checksum,
        }

    calculator_versions: Dict[str, str] = {}
    for agent_id, env in envelopes.items():
        for art_name, art_val in (env.artifacts or {}).items():
            if isinstance(art_val, dict):
                calc_id = art_val.get("calculator_id")
                calc_ver = art_val.get("calculator_version") or art_val.get("formula_version")
                if calc_id and calc_ver:
                    calculator_versions[str(calc_id)] = str(calc_ver)
                witness = art_val.get("calculation_witness")
                if isinstance(witness, dict):
                    w_calc_id = witness.get("calculator_id")
                    w_calc_ver = witness.get("formula_version") or witness.get("calculator_version")
                    if w_calc_id and w_calc_ver:
                        calculator_versions[str(w_calc_id)] = str(w_calc_ver)

    evidence_state = "source_bytes_unavailable"
    pack_checksum = summary.get("evidence_checksum")
    bundle_manifest_sha256 = None
    bundle_path = run_dir / "evidence" / "evidence_bundle_manifest.json"
    if bundle_path.is_file():
        try:
            bundle_manifest_sha256 = checksum_file(bundle_path)
        except Exception:
            bundle_manifest_sha256 = None

    if pack_checksum:
        evidence_state = "source_bytes_verified"
    else:
        ev_file = run_dir / "evidence" / "evidence.json"
        if ev_file.is_file():
            try:
                from src.evidence import EvidencePack

                first = next(iter(envelopes.values())) if envelopes else None
                if first:
                    pack = EvidencePack(
                        ev_file,
                        ticker=first.params.get("ticker", ticker),
                        analysis_date=first.params.get("analysis_date", analysis_date),
                    )
                    pack_checksum = pack.checksum
                    evidence_state = "source_bytes_verified"
            except Exception:
                evidence_state = "source_bytes_unavailable"

    config: Dict[str, Any] = {
        "provider": summary.get("provider")
        or (next(iter(envelopes.values())).metrics.get("provider") if envelopes else None),
        "model": summary.get("model")
        or (summary.get("params") or {}).get("model")
        or (next(iter(envelopes.values())).metrics.get("model") if envelopes else None),
        "depth": (summary.get("params") or {}).get("depth")
        or (next(iter(envelopes.values())).params.get("depth") if envelopes else None),
        "strict": bool(summary.get("strict")),
        "frozen_checksum": summary.get("checksum") or summary.get("frozen_checksum"),
    }
    if isinstance(summary.get("params"), dict):
        for k, v in summary["params"].items():
            if k not in config and k not in ("ticker", "company", "analysis_date"):
                config[k] = v

    return {
        "schema_version": "run_manifest_v1",
        "run_id": run_id,
        "status": status,
        "complete": status == "completed" and set(roles) == set(EXPECTED_ROLES),
        "ticker": ticker,
        "analysis_date": analysis_date,
        "halt_reason": halt_reason,
        "roles": roles,
        "graph": graph,
        "outputs": outputs,
        "evidence": {
            "pack_checksum": pack_checksum,
            "state": evidence_state,
            "bundle_manifest_sha256": bundle_manifest_sha256,
        },
        "calculator_versions": calculator_versions,
        "prompt_sha256": prompt_hashes,
        "execution_keys": execution_keys,
        "provenance": provenance_out,
        "configuration": config,
        "generated_at": _now_iso(),
    }


def validate_run_manifest(manifest: Dict[str, Any]) -> List[str]:
    """Validate a manifest against ``schemas/run_manifest_v1.json``."""
    schema_path = get_schema_path("run_manifest_v1.json")
    if not schema_path.is_file():  # pragma: no cover - packaging guard
        return ["run_manifest_v1.json schema is missing"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a hard dependency
        return []
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"run manifest: {'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in validator.iter_errors(manifest)
    ]


# --------------------------------------------------------------- calculation


def _load_fact_ledger(
    run_dir: Path,
    envelopes: Dict[str, Envelope],
    *,
    problems: Optional[List[str]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    ticker: Optional[str] = None,
    analysis_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Load the fact ledger from verified evidence snapshot, run files, and envelopes.

    Reconstructs facts from verified evidence snapshots (under run/evidence)
    with full checksum validation, rejects corrupt, malformed, or conflicting
    same-ID fact records, and prevents model-generated envelope facts from
    replacing or contradicting real source facts.
    """
    if problems is None:
        problems = []
    facts_map: Dict[str, Any] = {}
    try:
        from src.facts import Fact, load_fact_ledger, validate_fact
    except Exception as exc:  # pragma: no cover - import of core module
        problems.append(f"fact ledger support unavailable: {exc}")
        return facts_map

    run_dir = Path(run_dir)
    if manifest is None:
        manifest = _load_manifest(run_dir)

    if ticker is None or analysis_date is None:
        if manifest:
            ticker = ticker or manifest.get("ticker")
            analysis_date = analysis_date or manifest.get("analysis_date")
        if "A1" in envelopes and envelopes["A1"].params:
            ticker = ticker or envelopes["A1"].params.get("ticker")
            analysis_date = analysis_date or envelopes["A1"].params.get("analysis_date")
        elif envelopes:
            for env in envelopes.values():
                if env.params:
                    ticker = ticker or env.params.get("ticker")
                    analysis_date = analysis_date or env.params.get("analysis_date")
                    if ticker and analysis_date:
                        break
        if ticker is None or analysis_date is None:
            for p in sorted(run_dir.glob("run_summary.json")):
                try:
                    summ = json.loads(p.read_text(encoding="utf-8"))
                    p_params = summ.get("params") or {}
                    ticker = ticker or p_params.get("ticker")
                    analysis_date = analysis_date or p_params.get("analysis_date")
                except Exception:
                    pass
                break

    evidence_dir = run_dir / "evidence"
    ev_file = evidence_dir / "evidence.json"
    evidence_snapshot_loaded = False

    manifest_evidence = (
        manifest.get("evidence")
        if manifest and isinstance(manifest.get("evidence"), dict)
        else {}
    )
    manifest_claims_evidence = bool(
        manifest_evidence.get("pack_checksum")
        or manifest_evidence.get("state") == "source_bytes_verified"
    )

    # 1. Verified Evidence Snapshot under run/evidence
    if evidence_dir.is_dir() or ev_file.is_file():
        if not ev_file.is_file():
            problems.append(
                f"evidence directory exists at {evidence_dir.name} but evidence.json is missing"
            )
        else:
            try:
                from src.evidence import EvidencePack

                pack = EvidencePack(
                    ev_file,
                    ticker=str(ticker) if ticker else "UNKNOWN",
                    analysis_date=str(analysis_date) if analysis_date else "UNKNOWN",
                )
                manifest_cksum = manifest_evidence.get("pack_checksum")
                if manifest_cksum and pack.checksum != manifest_cksum:
                    problems.append(
                        f"evidence snapshot checksum mismatch: pack checksum '{pack.checksum}' "
                        f"does not match manifest recorded checksum '{manifest_cksum}'"
                    )

                bundle_manifest_file = evidence_dir / "evidence_bundle_manifest.json"
                if bundle_manifest_file.is_file() and manifest_evidence.get("bundle_manifest_sha256"):
                    actual_bm_sha = checksum_file(bundle_manifest_file)
                    if actual_bm_sha != manifest_evidence["bundle_manifest_sha256"]:
                        problems.append(
                            f"evidence bundle manifest checksum mismatch: actual '{actual_bm_sha}' "
                            f"does not match manifest recorded checksum '{manifest_evidence['bundle_manifest_sha256']}'"
                        )

                for f in getattr(pack, "facts", []):
                    val_res = validate_fact(f)
                    if val_res.errors:
                        problems.append(
                            f"fact {getattr(f, 'id', '<unknown>')!r} in evidence snapshot is malformed: "
                            + "; ".join(val_res.errors)
                        )
                        continue
                    if f.id in facts_map:
                        if facts_map[f.id] != f:
                            problems.append(
                                f"conflicting fact identity for {f.id!r}: evidence snapshot contains "
                                f"conflicting definitions for the same fact ID"
                            )
                    else:
                        facts_map[f.id] = f
                evidence_snapshot_loaded = True
            except Exception as exc:
                problems.append(
                    f"evidence snapshot at {evidence_dir.name} could not be loaded: {exc}. "
                    "An unreadable or corrupt evidence pack must not be treated as an absent one."
                )
    elif manifest_claims_evidence:
        problems.append(
            "manifest records verified evidence pack, but evidence directory is missing from run directory"
        )

    # 2. Top-level fact files
    for candidate in (
        run_dir / "facts",
        run_dir / "fact_ledger.json",
        run_dir / "facts.json",
    ):
        if not candidate.exists():
            continue
        try:
            loaded = load_fact_ledger(candidate)
        except Exception as exc:
            problems.append(
                f"fact ledger at {candidate.name} could not be loaded: {exc}. "
                "An unreadable ledger must not be treated as an absent one."
            )
            continue
        for f in loaded:
            val_res = validate_fact(f)
            if val_res.errors:
                problems.append(
                    f"fact {getattr(f, 'id', '<unknown>')!r} at {candidate.name} is malformed: "
                    + "; ".join(val_res.errors)
                )
                continue
            if f.id in facts_map:
                if facts_map[f.id] != f:
                    problems.append(
                        f"conflicting fact identity for {f.id!r}: {candidate.name} conflicts with "
                        f"previously loaded fact"
                    )
            else:
                facts_map[f.id] = f

    # 3. Envelope artifacts (model facts)
    for agent_id, env in sorted(envelopes.items()):
        art_facts = (env.artifacts or {}).get("facts") or (
            env.artifacts or {}
        ).get("fact_ledger")
        if isinstance(art_facts, list):
            items = art_facts
        elif isinstance(art_facts, dict):
            items = list(art_facts.values())
        else:
            continue
        for item in items:
            if not isinstance(item, dict) or "id" not in item:
                problems.append(
                    f"fact embedded in {agent_id} envelope is malformed (expected dict with 'id')"
                )
                continue
            try:
                fact = item if isinstance(item, Fact) else Fact.from_dict(item)
            except Exception as exc:
                problems.append(
                    f"fact {item.get('id')!r} embedded in {agent_id} envelope could not be "
                    f"parsed as a typed fact: {exc}"
                )
                continue
            val_res = validate_fact(fact)
            if val_res.errors:
                problems.append(
                    f"fact {fact.id!r} embedded in {agent_id} envelope is invalid: "
                    + "; ".join(val_res.errors)
                )
                continue

            if fact.id in facts_map:
                if facts_map[fact.id] != fact:
                    problems.append(
                        f"fact {fact.id!r} embedded in {agent_id} envelope conflicts with "
                        f"verified source fact; model cannot replace source facts"
                    )
            elif evidence_snapshot_loaded:
                problems.append(
                    f"fact {fact.id!r} embedded in {agent_id} envelope was not found in the "
                    f"verified evidence snapshot; facts must be bound to real snapshot facts"
                )
            else:
                facts_map[fact.id] = fact
    return facts_map


def replay_beneish(
    envelope: Envelope,
    fact_ledger: Optional[Mapping[str, Any]] = None,
    analysis_date: Optional[str] = None,
    expected_company: Optional[str] = None,
    unavailable: Optional[List[str]] = None,
    counters: Optional[Dict[str, int]] = None,
) -> List[str]:
    """
    Independently recompute A3's Beneish score from its recorded witness.

    Returns a list of problems. An empty list means the score was recomputed and
    matched. This never accepts "the number was finite and in range".

    Two distinct properties are checked, and passing one does not imply the
    other:

    * **Arithmetic consistency** — the recorded result is recomputed from the
      recorded inputs.
    * **Source binding** — each input that cites a fact is verified to equal
      that fact's actual value, with compatible unit, period, accounting basis,
      filing identity and point-in-time availability (see
      :mod:`src.fact_binding`).

    A witness whose inputs are internally consistent but cite a real fact ID
    with a fabricated number is rejected by the second check.
    """
    beneish = (envelope.artifacts or {}).get("BeneishM")
    if not isinstance(beneish, dict):
        return ["A3 BeneishM artifact is missing"]

    if counters is not None:
        counters["witnesses_attempted"] += 1

    score = beneish.get("score")
    feasible = beneish.get("feasible")
    if score is None or feasible is False:
        reason = beneish.get("unavailable_reason")
        if not isinstance(reason, str) or not reason.strip():
            return ["A3 BeneishM is unavailable without a documented reason"]
        witness = beneish.get("calculation_witness")
        if isinstance(witness, dict) and witness.get("provenance") == "deterministic":
            return [
                "A3 BeneishM is marked unavailable but calculation witness claims deterministic result"
            ]
        if unavailable is not None:
            unavailable.append(f"A3 BeneishM is unavailable: {reason}")
        return []

    from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score
    from src.calculation_witness import _finite

    provenance = beneish.get("provenance")
    witness = beneish.get("calculation_witness")

    # If calculation_witness is missing, check for legacy direct indices
    if not isinstance(witness, dict):
        legacy_indices = beneish.get("indices")
        if isinstance(legacy_indices, dict) and all(k in legacy_indices for k in BENEISH_INPUT_KEYS):
            witness = {
                "formula_id": "beneish_m_score",
                "formula_version": beneish.get("calculator_version") or "1.0.0",
                "calculator_id": beneish.get("calculator_id") or "beneish_calculator",
                "provenance": provenance or "deterministic",
                "inputs": legacy_indices,
                "result": beneish.get("raw_score") if beneish.get("raw_score") is not None else score,
            }
        elif provenance == "model_reported_unverified":
            if unavailable is not None:
                unavailable.append(
                    "A3 BeneishM is model-reported with no index ratios; calculation replay is not possible"
                )
            return []
        else:
            return [
                "A3 BeneishM claims a computed score but carries no calculation "
                "witness; calculation replay is not possible"
            ]

    w_prov = witness.get("provenance")
    inputs = witness.get("inputs")

    # Handle model-reported provenance
    if provenance == "model_reported_unverified" or w_prov == "model_reported_unverified":
        if not isinstance(inputs, dict) or not all(k in inputs for k in BENEISH_INPUT_KEYS):
            if unavailable is not None:
                unavailable.append(
                    "A3 BeneishM is model-reported with no index ratios; calculation replay is not possible"
                )
            return []

    if w_prov == "unavailable":
        return [
            "A3 BeneishM claims a computed score but its calculation witness is marked unavailable"
        ]
    if w_prov not in ("deterministic", "model_reported_unverified"):
        return [
            f"A3 BeneishM calculation witness has invalid provenance '{w_prov}', expected 'deterministic'"
        ]

    if witness.get("formula_id") != "beneish_m_score":
        return ["A3 BeneishM calculation witness must name the beneish_m_score formula"]
    if not isinstance(inputs, dict):
        return ["A3 BeneishM calculation witness carries no inputs"]

    missing = [key for key in BENEISH_INPUT_KEYS if key not in inputs]
    if missing:
        return [
            "A3 BeneishM calculation witness is missing index inputs: "
            + ", ".join(missing)
        ]

    unexpected = [key for key in inputs if key not in BENEISH_INPUT_KEYS]
    if unexpected:
        return [
            "A3 BeneishM calculation witness carries unexpected input labels: "
            + ", ".join(unexpected)
        ]

    for key in BENEISH_INPUT_KEYS:
        val = inputs[key]
        if _finite(val) is None:
            return [
                f"A3 BeneishM calculation witness input '{key}' is not a finite number: {val!r}"
            ]

    from src.forensic_inputs import validate_forensic_witness
    semantic_errors = validate_forensic_witness(witness, fact_ledger or {})
    if semantic_errors:
        return [f"A3 BeneishM {problem}" for problem in semantic_errors]

    # Source binding: every input that cites a fact must match that fact's
    # recorded value, unit, period, basis, filing identity and availability.
    #
    # This replaces the previous check, which verified only that a cited fact ID
    # *exists* and passed a chronology test. A witness could therefore cite a
    # real fact while using a fabricated number and replay "successfully".
    bindings = verify_witness_bindings(
        witness,
        fact_ledger=fact_ledger or {},
        expected_company=expected_company,
        cutoff=analysis_date,
        expected_periods=(
            witness.get("input_periods") if isinstance(witness.get("input_periods"), dict) else None
        ),
        expected_bases=(
            witness.get("input_bases") if isinstance(witness.get("input_bases"), dict) else None
        ),
    )
    binding_problems = [
        f"A3 BeneishM {problem}" for problem in binding_failures(bindings)
    ]
    if binding_problems:
        return binding_problems
    if unavailable is not None:
        for note in binding_notes(bindings):
            unavailable.append(f"A3 BeneishM {note}")

    if counters is not None:
        for b in bindings:
            if b.bound:
                counters["inputs_verified"] += 1
            elif b.status == "unavailable":
                counters["inputs_unverified"] += 1

    try:
        recomputed = beneish_score(
            **{key: float(inputs[key]) for key in BENEISH_INPUT_KEYS}
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return [f"A3 BeneishM calculation witness inputs are not usable: {exc}"]
    if _finite(recomputed) is None or _finite(score) is None:
        return ["A3 BeneishM computed and displayed scores must be finite numeric values"]
    witness_result = _finite(witness.get("result"))
    if witness_result is None or abs(witness_result - recomputed) > 1e-9:
        return ["A3 BeneishM witness result is nonfinite or does not match recomputed arithmetic"]

    recorded_raw = beneish.get("raw_score")
    if recorded_raw is None:
        recorded_raw = witness.get("result")
    if _finite(recorded_raw) is None:
        return ["A3 BeneishM has no finite recorded raw score to compare against"]

    if abs(float(recorded_raw) - recomputed) > 1e-9:
        return [
            f"A3 BeneishM arithmetic mismatch: recorded raw score {float(recorded_raw)!r} "
            f"does not equal the score recomputed from its own recorded inputs "
            f"({recomputed!r})"
        ]
    if abs(float(score) - round(recomputed, 2)) > 1e-9:
        return [
            f"A3 BeneishM display score {float(score)!r} does not match the "
            f"recomputed value rounded for display ({round(recomputed, 2)!r})"
        ]

    if counters is not None:
        counters["witnesses_replayed"] += 1

    return []


# ------------------------------------------------------------------- audit


def _is_rejected_halt_diagnostic(
    manifest: Optional[Mapping[str, Any]],
    agent_id: str,
    env: Envelope,
) -> bool:
    """Return true only for an explicitly rejected, unaccepted halt diagnostic."""
    if not isinstance(manifest, dict) or manifest.get("status") != "halted":
        return False
    roles = manifest.get("roles")
    return (
        isinstance(roles, dict)
        and agent_id not in roles
        and env.gates.get("passed") is False
    )


def semantic_audit(
    run_dir: Path,
    require_source_backed: bool = False,
    strict: bool = False,
) -> AuditReport:
    """
    Audit a run directory across independent levels.

    Never raises for a damaged run: each problem is reported at the level that
    can detect it, so one broken layer does not mask another.
    """
    from src.run_audit import verify_run

    run_dir = Path(run_dir)
    report = AuditReport()
    if not run_dir.is_dir():
        report.errors.append(f"not a directory: {run_dir}")
        for level in AUDIT_LEVELS:
            report.levels[level] = "failed"
        return report

    manifest = _load_manifest(run_dir)

    validator = EnvelopeValidator(get_schema_path("envelope_v2_1.json"))
    envelopes: Dict[str, Envelope] = {}
    by_file: Dict[str, Dict[str, Any]] = {}
    integrity_errors: List[str] = []
    #: Graph-level defects detected while loading (e.g. duplicate roles), so
    #: they are attributed to ``graph_and_contracts`` rather than to an
    #: unattributed top-level error.
    duplicate_role_errors: List[str] = []

    envelope_files = sorted(run_dir.glob("*_envelope.json"))
    empty_halted_run = (
        isinstance(manifest, dict)
        and manifest.get("status") == "halted"
        and manifest.get("roles") == {}
        and not envelope_files
    )
    for path in envelope_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            integrity_errors.append(f"{path.name}: invalid JSON ({exc})")
            continue
        except Exception as exc:
            integrity_errors.append(f"{path.name}: error reading envelope ({exc})")
            continue

        if not isinstance(data, dict):
            integrity_errors.append(f"{path.name}: envelope root is not a dictionary")
            continue

        agent_id = str((data.get("agent") or {}).get("id") or "")
        if not agent_id:
            agent_id = path.stem.replace("_envelope", "").upper()

        if agent_id in envelopes:
            # A duplicate role makes the run's role set ambiguous. It is a
            # graph defect, so it is attributed to ``graph_and_contracts``
            # rather than to an unattributed top-level error.
            duplicate_role_errors.append(
                f"duplicate role ID '{agent_id}' appears in more than one envelope "
                f"({path.name}); the run's role set is ambiguous"
            )
            continue

        integrity_errors.extend(
            f"{path.name}: {err}" for err in validator.validate(data).errors
        )
        env = Envelope.from_dict(data)
        envelopes[agent_id] = env
        by_file[agent_id] = data
        if env.content_checksum:
            actual = envelope_content_checksum(data)
            if actual != env.content_checksum:
                integrity_errors.append(
                    f"{path.name}: content checksum mismatch (file modified after sealing)"
                )
        else:
            warning = (
                f"{path.name}: no content_checksum "
                "(legacy or unsealed; run `controller seal`)"
            )
            report.warnings.append(warning)
            if _is_rejected_halt_diagnostic(manifest, agent_id, env):
                report.strict_exempt_warnings.add(warning)

    if not envelope_files and not empty_halted_run:
        integrity_errors.append("no *_envelope.json files found")

    # Level 1 — envelope integrity (plus the existing provenance-chain checks).
    chain_result = verify_run(run_dir)
    envelope_checksum_errors = set(chain_result.envelope_checksum_errors)
    integrity_errors.extend(
        err
        for err in chain_result.errors
        if err not in envelope_checksum_errors
        and not (
            empty_halted_run and err == "no *_envelope.json files found"
        )
    )
    report.warnings.extend(chain_result.warnings)
    report.levels["envelope_integrity"] = (
        "failed"
        if integrity_errors
        else "not_applicable"
        if empty_halted_run
        else "passed"
    )
    report.errors.extend(integrity_errors)

    # Resolve ticker and analysis_date (cutoff) from manifest, envelopes, or summary
    ticker = None
    analysis_date = None
    if manifest:
        ticker = manifest.get("ticker")
        analysis_date = manifest.get("analysis_date")
    if not ticker or not analysis_date:
        if "A1" in envelopes and envelopes["A1"].params:
            ticker = ticker or envelopes["A1"].params.get("ticker")
            analysis_date = analysis_date or envelopes["A1"].params.get("analysis_date")
        elif envelopes:
            for env in envelopes.values():
                if env.params:
                    ticker = ticker or env.params.get("ticker")
                    analysis_date = analysis_date or env.params.get("analysis_date")
                    if ticker and analysis_date:
                        break
    if not ticker or not analysis_date:
        for p in sorted(run_dir.glob("run_summary.json")):
            try:
                summ = json.loads(p.read_text(encoding="utf-8"))
                p_params = summ.get("params") or {}
                ticker = ticker or p_params.get("ticker")
                analysis_date = analysis_date or p_params.get("analysis_date")
            except Exception:
                pass
            break

    # Level 2 — source reconstruction.
    retrieved = [
        source
        for env in envelopes.values()
        for source in env.sources
        if isinstance(source, dict) and source.get("verification") == "retrieved"
    ]
    evidence_dir = run_dir / "evidence"
    manifest_evidence = (
        manifest.get("evidence")
        if manifest and isinstance(manifest.get("evidence"), dict)
        else {}
    )
    manifest_claims_evidence = bool(
        manifest_evidence.get("pack_checksum")
        or manifest_evidence.get("state") == "source_bytes_verified"
    )

    if not retrieved and not evidence_dir.exists():
        if manifest_claims_evidence:
            report.levels["source_reconstruction"] = "source_bytes_unavailable"
            report.errors.append(
                "source reconstruction unavailable: manifest records verified evidence pack, "
                "but evidence directory is missing from run directory"
            )
        else:
            report.levels["source_reconstruction"] = "not_applicable"
    else:
        try:
            if not (evidence_dir / "evidence.json").is_file():
                raise ValueError("evidence directory exists but evidence.json is missing")
            from src.evidence import EvidencePack

            pack = EvidencePack(
                evidence_dir / "evidence.json",
                ticker=str(ticker) if ticker else "UNKNOWN",
                analysis_date=str(analysis_date) if analysis_date else "UNKNOWN",
            )
            # Check retrieved sources match pack
            canonical = {source["id"]: source for source in pack.sources()}
            for source in retrieved:
                src_id = source.get("id")
                if src_id not in canonical:
                    raise ValueError(f"retrieved source '{src_id}' missing from evidence pack")
                if source != canonical[src_id]:
                    raise ValueError(f"retrieved source '{src_id}' metadata mismatch")

            manifest_cksum = manifest_evidence.get("pack_checksum")
            if manifest_cksum and pack.checksum != manifest_cksum:
                raise ValueError(
                    f"evidence pack checksum '{pack.checksum}' does not match "
                    f"manifest recorded checksum '{manifest_cksum}'"
                )

            report.levels["source_reconstruction"] = "source_bytes_verified"
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.levels["source_reconstruction"] = "source_bytes_unavailable"
            report.errors.append(
                f"source reconstruction unavailable: {exc} (a run whose source "
                "bytes cannot be restored is not fully auditable)"
            )

    # Load fact ledger early so graph contract validation and calculation replay
    # have access to the verified ledger.
    ledger_problems: List[str] = []
    fact_ledger = _load_fact_ledger(
        run_dir,
        envelopes,
        problems=ledger_problems,
        manifest=manifest,
        ticker=ticker,
        analysis_date=analysis_date,
    )
    # SEC CompanyFacts records the ticker as the fact's company identity, so
    # that is the value a cited fact must carry.
    expected_company = str(ticker).strip().upper() if ticker else None

    # Level 3 — graph and contracts.
    graph_errors = _audit_graph(
        envelopes, run_dir, report, fact_ledger=fact_ledger,
        expected_company=expected_company, cutoff=analysis_date, strict=strict,
    )
    graph_errors = list(duplicate_role_errors) + graph_errors
    report.errors.extend(graph_errors)
    report.levels["graph_and_contracts"] = "failed" if graph_errors else "passed"

    # Level 4 — calculation replay.
    # A ledger that could not be loaded is itself a calculation-replay defect:
    # it is indistinguishable, to a reader, from arithmetic with no sources.
    calc_errors: List[str] = list(ledger_problems)
    counters = {
        "witnesses_attempted": 0,
        "witnesses_replayed": 0,
        "inputs_verified": 0,
        "inputs_unverified": 0,
    }

    core_counters = dict(counters)
    if "A3" in envelopes:
        calc_errors.extend(
            replay_beneish(
                envelopes["A3"],
                fact_ledger=fact_ledger,
                analysis_date=analysis_date,
                expected_company=expected_company,
                unavailable=report.unavailable,
                counters=core_counters,
            )
        )
    counters.update(core_counters)
    report.core_witnesses_replayed = core_counters["witnesses_replayed"]
    report.core_inputs_verified = core_counters["inputs_verified"]
    report.core_inputs_unverified = core_counters["inputs_unverified"]

    from src.calculation_witness import CalculationWitness, validate_witness

    for agent_id, env in sorted(envelopes.items()):
        depth_errors: List[str] = []
        for art_name, witness in _collect_embedded_witnesses(
            env.artifacts or {}, errors=depth_errors
        ):
            if agent_id == "A3" and art_name == "BeneishM.calculation_witness":
                # Only the primary Beneish witness was already replayed above.
                continue
            if isinstance(witness, (dict, CalculationWitness)):
                witness = witness.to_dict() if isinstance(witness, CalculationWitness) else witness
                if isinstance(witness, dict):
                    counters["witnesses_attempted"] += 1
                    w_prov = witness.get("provenance")
                    if w_prov == "unavailable":
                        w_errors = validate_witness(
                            witness,
                            fact_ledger=fact_ledger,
                            cutoff=analysis_date,
                            expected_company=expected_company,
                        )
                        calc_errors.extend(
                            f"{agent_id}.{art_name} witness: {e}" for e in w_errors
                        )
                        report.unavailable.append(
                            f"{agent_id}.{art_name} witness is unavailable: {witness.get('notes')}"
                        )
                        continue
                    if w_prov == "model_reported_unverified":
                        report.unavailable.append(
                            f"{agent_id}.{art_name} witness: model-reported with no recomputable inputs"
                        )
                        continue
                    if w_prov != "deterministic":
                        calc_errors.append(
                            f"{agent_id}.{art_name} witness: invalid provenance '{w_prov}', expected 'deterministic'"
                        )
                        continue

                    w_errors = validate_witness(
                        witness,
                        fact_ledger=fact_ledger,
                        cutoff=analysis_date,
                        expected_company=expected_company,
                    )
                    calc_errors.extend(
                        f"{agent_id}.{art_name} witness: {e}" for e in w_errors
                    )
                    w_inputs = witness.get("inputs")
                    if not isinstance(w_inputs, dict) or not w_inputs:
                        # Empty or non-mapping inputs cannot establish source coverage
                        continue

                    w_bindings = verify_witness_bindings(
                        witness,
                        fact_ledger=fact_ledger or {},
                        expected_company=expected_company,
                        cutoff=analysis_date,
                        expected_periods=witness.get("input_periods"),
                        expected_bases=witness.get("input_bases"),
                    )
                    for note in binding_notes(w_bindings):
                        report.unavailable.append(f"{agent_id}.{art_name} witness: {note}")

                    formula_id = witness.get("formula_id")
                    formula_replayed = False
                    if not w_errors:
                        if formula_id == "beneish_m_score":
                            formula_replayed = True
                        elif formula_id in ("test_identity", "identity") and len(w_inputs) == 1:
                            in_val = next(iter(w_inputs.values()))
                            res_val = witness.get("result")
                            if (
                                isinstance(in_val, (int, float))
                                and isinstance(res_val, (int, float))
                                and not isinstance(in_val, bool)
                                and not isinstance(res_val, bool)
                                and math.isclose(in_val, res_val, rel_tol=1e-9)
                            ):
                                formula_replayed = True
                        else:
                            # A derived *input* does not replay an unknown output formula.
                            report.unavailable.append(
                                f"{agent_id}.{art_name} witness: unsupported formula '{formula_id}'; "
                                "output arithmetic was not replayed"
                            )
                        if formula_id in ("test_identity", "identity") and not formula_replayed:
                            calc_errors.append(
                                f"{agent_id}.{art_name} witness: identity arithmetic mismatch "
                                "or invalid input count (expected one input equal to result)"
                            )

                    if formula_replayed:
                        counters["witnesses_replayed"] += 1
                        for b in w_bindings:
                            if b.bound:
                                counters["inputs_verified"] += 1
                            elif b.status == "unavailable":
                                counters["inputs_unverified"] += 1
                    else:
                        for b in w_bindings:
                            if b.status == "unavailable":
                                counters["inputs_unverified"] += 1

        for err in depth_errors:
            calc_errors.append(f"{agent_id}.{err}")

    report.witnesses_attempted = counters["witnesses_attempted"]
    report.witnesses_replayed = counters["witnesses_replayed"]
    report.inputs_verified = counters["inputs_verified"]
    report.inputs_unverified = counters["inputs_unverified"]

    if require_source_backed:
        unmet = report.source_backed_acceptance()
        if unmet:
            calc_errors.append(
                "require-source-backed: run is not source-backed end to end. Unmet: "
                + "; ".join(unmet)
            )
            if report.unavailable:
                for item in report.unavailable:
                    calc_errors.append(
                        f"require-source-backed: calculation input is not source-backed: {item}"
                    )

    report.errors.extend(calc_errors)
    if calc_errors:
        report.levels["calculation_replay"] = "failed"
    elif counters["witnesses_replayed"] > 0:
        report.levels["calculation_replay"] = "passed"
    elif counters["witnesses_attempted"] > 0:
        report.levels["calculation_replay"] = "not_replayable"
    else:
        report.levels["calculation_replay"] = "not_applicable"

    # Level 5 — final artifacts bound to the manifest.
    artifact_errors = _audit_final_artifacts(run_dir, envelopes, report)
    report.errors.extend(artifact_errors)
    report.levels["final_artifacts"] = "failed" if artifact_errors else "passed"

    # Level 6 — completeness.
    manifest = manifest or _load_manifest(run_dir)
    expected = set(EXPECTED_ROLES)
    report.halted = bool(manifest and manifest.get("status") == "halted")
    manifest_roles = (
        manifest.get("roles")
        if report.halted and isinstance(manifest.get("roles"), dict)
        else {}
    )
    accepted_present = (
        set(envelopes).intersection(manifest_roles)
        if report.halted
        else set(envelopes)
    )
    missing = sorted(expected - accepted_present)
    unexpected = sorted(set(envelopes) - expected)
    report.complete = not report.halted and not missing and not unexpected
    if unexpected:
        report.levels["completeness"] = "failed"
        report.errors.append(
            "run contains unexpected role(s): " + ", ".join(unexpected)
        )
    if missing:
        if report.halted:
            if "completeness" not in report.levels:
                report.levels["completeness"] = "incomplete"
        else:
            report.levels["completeness"] = "failed"
            suffix = (
                f"contradicts a {manifest.get('status')} manifest"
                if manifest
                else "manifest is absent"
            )
            report.errors.append(
                "run is incomplete: missing required role(s): "
                f"{', '.join(missing)}; {suffix}"
            )
    elif not unexpected and report.halted:
        report.levels["completeness"] = "incomplete"
    elif not unexpected:
        report.levels["completeness"] = "passed"

    if report.halted:
        expected_absence_warnings = {
            "no assembled report found",
            "no artifacts bundle found",
        }
        if empty_halted_run:
            expected_absence_warnings.add("no provenance log found")
        report.strict_exempt_warnings.update(
            warning
            for warning in report.warnings
            if warning in expected_absence_warnings
        )

    if manifest:
        report.errors.extend(validate_run_manifest(manifest))

    return report


def _audit_graph(
    envelopes: Dict[str, Envelope],
    run_dir: Path,
    report: AuditReport,
    fact_ledger: Optional[Mapping[str, Any]] = None,
    expected_company: Optional[str] = None,
    cutoff: Optional[str] = None,
    strict: bool = False,
) -> List[str]:
    """Validate consumed-parent lineage and machine-checkable contracts."""
    errors: List[str] = []
    manifest = _load_manifest(run_dir)
    depth_config = None
    for path in sorted(run_dir.glob("run_summary.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            break
        depth = (summary.get("params") or {}).get("depth")
        if depth:
            try:
                from src.config import load_depth_modes

                modes = load_depth_modes(get_config_dir())
                depth_config = modes.get(str(depth).lower(), {}).payload
            except Exception:  # noqa: BLE001 - config is optional context
                depth_config = None
        break

    # Build adjacency for cycle detection
    graph_adj: Dict[str, List[str]] = {}
    for agent_id, env in envelopes.items():
        if agent_id not in EXPECTED_ROLES:
            errors.append(
                f"unexpected role ID '{agent_id}' found in run envelopes; expected only standard roles ({', '.join(EXPECTED_ROLES)})"
            )
        parents: List[str] = []
        for entry in env.upstream or []:
            if isinstance(entry, dict) and entry.get("agent"):
                parents.append(str(entry["agent"]))
        graph_adj[agent_id] = parents

    # Cycle detection
    cycles = find_graph_cycles(graph_adj)
    for cycle in cycles:
        errors.append(f"cycle detected in upstream graph: {' -> '.join(cycle)}")

    # Lineage must be internally consistent and reference only present roles.
    for agent_id, env in sorted(envelopes.items()):
        seen = set()
        for entry in env.upstream or []:
            if not isinstance(entry, dict):
                errors.append(f"{agent_id}: upstream entry is not a dictionary")
                continue
            parent = entry.get("agent")
            checksum = entry.get("content_checksum")
            if not parent:
                errors.append(f"{agent_id}: upstream entry without an agent id")
                continue
            if parent == agent_id:
                errors.append(
                    f"{agent_id}: self-referential upstream cycle ({agent_id} -> {agent_id})"
                )
                continue
            if parent in seen:
                errors.append(f"{agent_id}: duplicate upstream entry for {parent}")
            seen.add(parent)
            if parent not in envelopes:
                errors.append(
                    f"{agent_id}: upstream {parent} referenced but its envelope is "
                    "missing from the run"
                )
            elif envelopes[parent].content_checksum and checksum != envelopes[parent].content_checksum:
                errors.append(
                    f"{agent_id}: upstream {parent} checksum does not match the "
                    "referenced envelope"
                )

    # A self-consistent lie is still detectable: an agent that claims a parent
    # which never fed it is caught by comparing the sealed lineage against the
    # parents the runner recorded as actually supplied at execution.
    for agent_id, env in sorted(envelopes.items()):
        lineage = (env.artifacts or {}).get("_execution_lineage")
        if not isinstance(lineage, dict):
            continue
        recorded = sorted(str(entry) for entry in (lineage.get("consumed_upstream") or []))
        claimed = sorted(str(entry.get("agent")) for entry in (env.upstream or []) if isinstance(entry, dict) and entry.get("agent"))
        if claimed and claimed != recorded:
            # A non-empty sealed upstream that contradicts the execution record
            # is a genuine graph inconsistency (the parallel-phase false-parent
            # bug, or an edited envelope).
            errors.append(
                f"{agent_id}: sealed upstream {claimed} does not match the "
                f"parents actually supplied at execution {recorded}"
            )
        elif not claimed and recorded:
            # Lineage was stripped (legacy/unsealed): the executed parents are
            # known but the envelope no longer records them. Flag as a warning,
            # not a hard failure — this is the legacy-import case.
            report.warnings.append(
                f"{agent_id}: sealed upstream is empty but execution recorded "
                f"parents {recorded}; recorded lineage appears to have been "
                "stripped (legacy or re-sealed envelope)"
            )

    # Contracts are re-run here in dependency order, independently of acceptance.
    import inspect
    sig = inspect.signature(validate_contract)

    for agent_id in ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
        env = envelopes.get(agent_id)
        if env is None:
            continue
        if _is_rejected_halt_diagnostic(manifest, agent_id, env):
            continue
        upstream_envelopes: Dict[str, Envelope] = {}
        for entry in env.upstream or []:
            if isinstance(entry, dict):
                parent = entry.get("agent")
                if parent in envelopes:
                    upstream_envelopes[parent] = envelopes[parent]

        kwargs: Dict[str, Any] = {
            "depth_config": depth_config,
            "envelopes": upstream_envelopes,
            "strict": strict,
        }
        if "fact_ledger" in sig.parameters:
            kwargs["fact_ledger"] = fact_ledger
        elif "ledger" in sig.parameters:
            kwargs["ledger"] = fact_ledger
        if "expected_company" in sig.parameters:
            kwargs["expected_company"] = expected_company
        if "cutoff" in sig.parameters:
            kwargs["cutoff"] = cutoff

        result = validate_contract(env, **kwargs)
        errors.extend(f"{agent_id} contract: {err}" for err in result.errors)
        for warning in result.warnings:
            message = f"{agent_id} contract: {warning}"
            report.warnings.append(message)
            if strict:
                if warning in result.strict_exempt_warnings:
                    report.strict_exempt_warnings.add(message)
                else:
                    errors.append(message)

    return errors


def _audit_final_artifacts(
    run_dir: Path, envelopes: Dict[str, Envelope], report: AuditReport
) -> List[str]:
    """Bind the exact final report and artifact bundle bytes to the manifest."""
    errors: List[str] = []
    manifest = _load_manifest(run_dir)
    if not manifest:
        errors.append(
            "run_manifest.json is missing; the final report and artifact bundle "
            "are not bound to a trusted run root"
        )
        return errors

    if not isinstance(manifest, dict):
        return ["run_manifest.json is malformed (expected object)"]

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        errors.append("run_manifest.json 'outputs' field must be an object")
        return errors

    for name, record in outputs.items():
        if not isinstance(record, dict):
            errors.append(f"{name}: recorded output is not an object")
            continue
        rel_path = record.get("path")
        if not rel_path or not isinstance(rel_path, str):
            errors.append(f"{name}: recorded output has missing or invalid path")
            continue

        is_safe, path = _is_safe_path(run_dir, rel_path)
        if not is_safe or path is None:
            errors.append(
                f"{name}: output path '{rel_path}' is unsafe (escapes run directory or uses escaping symlink)"
            )
            continue

        if not path.is_file():
            errors.append(f"{name}: recorded output is missing from the run ({path.name})")
            continue
        actual = checksum_file(path)
        if actual != record.get("sha256"):
            errors.append(
                f"{name}: final {name} bytes do not match the run manifest "
                f"(changed after sealing)"
            )

    a3 = envelopes.get("A3")
    if a3 and a3.metrics.get("runner_beneish_block"):
        from src.utils.beneish import render_beneish_block

        artifact = a3.artifacts.get("BeneishM")
        section = a3.sections_md.get("SECTION_4_FINANCIAL_FORENSICS", "")
        block = render_beneish_block(artifact) if isinstance(artifact, dict) else ""
        record = outputs.get("report")
        safe, path = _is_safe_path(run_dir, record.get("path")) if isinstance(record, dict) and isinstance(record.get("path"), str) else (False, None)
        try:
            report_text = path.read_text(encoding="utf-8") if safe and path and path.is_file() else ""
        except (OSError, UnicodeError):
            report_text = ""
        if (not block or section.count(block) != 1 or report_text.count(block) != 1
                or section.count("<!-- runner-beneish:start -->") != 1
                or report_text.count("<!-- runner-beneish:start -->") != 1):
            errors.append("report Beneish block does not match the A3 artifact")

    # Check manifest roles against envelopes
    manifest_roles = manifest.get("roles")
    if isinstance(manifest_roles, dict):
        for role_id, role_cksum in manifest_roles.items():
            if role_id not in envelopes:
                errors.append(
                    f"manifest specifies role '{role_id}' which is missing from envelopes"
                )
            elif (
                envelopes[role_id].content_checksum
                and envelopes[role_id].content_checksum != role_cksum
            ):
                errors.append(
                    f"manifest checksum for role '{role_id}' ({role_cksum}) does not match envelope ({envelopes[role_id].content_checksum})"
                )
        for env_id, env in envelopes.items():
            if env.content_checksum and env_id not in manifest_roles:
                errors.append(
                    f"envelope '{env_id}' is present but not recorded in manifest roles"
                )
    elif manifest_roles is not None:
        errors.append("run_manifest.json 'roles' field must be an object")

    # Check manifest graph against envelopes
    manifest_graph = manifest.get("graph")
    if isinstance(manifest_graph, dict):
        for agent_id, env in envelopes.items():
            env_parents = sorted(
                str(entry.get("agent"))
                for entry in (env.upstream or [])
                if isinstance(entry, dict) and entry.get("agent")
            )
            if agent_id in manifest_graph:
                m_parents = sorted(str(p) for p in (manifest_graph[agent_id] or []))
                if env_parents == m_parents:
                    continue
                if not env_parents and m_parents:
                    # The envelope records no lineage at all while the manifest
                    # records parents. We cannot tell stripped/legacy lineage
                    # from a genuine contradiction, so report it the same way
                    # _audit_graph does: unavailable, not failed. A manifest
                    # that claims parents an envelope never recorded is not
                    # evidence of a fabricated dependency edge.
                    report.warnings.append(
                        f"manifest graph for '{agent_id}' records parents "
                        f"{m_parents} but the envelope records no upstream; "
                        "graph lineage is unavailable until the run is sealed"
                    )
                    continue
                errors.append(
                    f"manifest graph for '{agent_id}' {m_parents} does not match envelope upstream {env_parents}"
                )
    elif manifest_graph is not None:
        errors.append("run_manifest.json 'graph' field must be an object")

    return errors



def _load_manifest(run_dir: Path) -> Optional[Dict[str, Any]]:
    path = run_dir / "run_manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def verify_run_semantic(
    run_dir: Path,
    strict: bool = False,
    require_source_backed: bool = False,
) -> ValidationResult:
    """ValidationResult-compatible view of :func:`semantic_audit`."""
    report = semantic_audit(
        run_dir,
        require_source_backed=require_source_backed,
        strict=strict,
    )
    errors = list(report.errors)
    warnings = list(report.warnings)
    if strict:
        errors.extend(
            warning
            for warning in warnings
            if warning not in report.strict_exempt_warnings and warning not in errors
        )
        if not report.complete:
            errors.append("strict: run is incomplete")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
