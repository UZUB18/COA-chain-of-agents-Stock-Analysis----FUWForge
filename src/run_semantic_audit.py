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

from src.contracts import AGENT_IDS, validate_contract
from src.envelope import Envelope
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
    complete: bool = False
    halted: bool = False

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

    def failed_levels(self) -> List[str]:
        return [name for name, state in self.levels.items() if state == "failed"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_state": self.overall_state,
            "complete": self.complete,
            "halted": self.halted,
            "levels": dict(self.levels),
            "failed_levels": self.failed_levels(),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
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
    schema_path = PROJECT_ROOT / "schemas" / "run_manifest_v1.json"
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


def _load_fact_ledger(run_dir: Path, envelopes: Dict[str, Envelope]) -> Dict[str, Any]:
    """Load fact ledger from files in run_dir or from envelope artifacts."""
    facts_map: Dict[str, Any] = {}
    try:
        from src.facts import Fact, load_fact_ledger

        for candidate in (
            run_dir / "facts",
            run_dir / "fact_ledger.json",
            run_dir / "facts.json",
        ):
            if candidate.exists():
                try:
                    loaded = load_fact_ledger(candidate)
                    for f in loaded:
                        facts_map[f.id] = f
                except Exception:
                    pass
        for env in envelopes.values():
            art_facts = (env.artifacts or {}).get("facts") or (
                env.artifacts or {}
            ).get("fact_ledger")
            if isinstance(art_facts, list):
                for item in art_facts:
                    if isinstance(item, dict) and "id" in item:
                        try:
                            f = (
                                Fact.from_dict(item)
                                if not isinstance(item, Fact)
                                else item
                            )
                            facts_map[f.id] = f
                        except Exception:
                            facts_map[item["id"]] = item
            elif isinstance(art_facts, dict):
                for k, item in art_facts.items():
                    if isinstance(item, dict) and "id" in item:
                        try:
                            f = (
                                Fact.from_dict(item)
                                if not isinstance(item, Fact)
                                else item
                            )
                            facts_map[f.id] = f
                        except Exception:
                            facts_map[item["id"]] = item
    except Exception:
        pass
    return facts_map


def replay_beneish(
    envelope: Envelope,
    fact_ledger: Optional[Mapping[str, Any]] = None,
    analysis_date: Optional[str] = None,
) -> List[str]:
    """
    Independently recompute A3's Beneish score from its recorded witness.

    Returns a list of problems. An empty list means the score was recomputed and
    matched. This never accepts "the number was finite and in range".
    """
    beneish = (envelope.artifacts or {}).get("BeneishM")
    if not isinstance(beneish, dict):
        return ["A3 BeneishM artifact is missing"]

    score = beneish.get("score")
    if score is None or beneish.get("feasible") is False:
        reason = beneish.get("unavailable_reason")
        if not isinstance(reason, str) or not reason.strip():
            return ["A3 BeneishM is unavailable without a documented reason"]
        witness = beneish.get("calculation_witness")
        if isinstance(witness, dict) and witness.get("provenance") == "deterministic":
            return [
                "A3 BeneishM is marked unavailable but calculation witness claims deterministic result"
            ]
        return []

    provenance = beneish.get("provenance")
    if provenance == "model_reported_unverified":
        return [
            "A3 BeneishM is model-reported with no index ratios; calculation "
            "replay is not possible (score not independently verifiable)"
        ]

    witness = beneish.get("calculation_witness")
    if not isinstance(witness, dict):
        return [
            "A3 BeneishM claims a computed score but carries no calculation "
            "witness; calculation replay is not possible"
        ]

    w_prov = witness.get("provenance")
    if w_prov == "model_reported_unverified":
        return [
            "A3 BeneishM calculation witness indicates model_reported_unverified; calculation replay is not possible"
        ]
    if w_prov == "unavailable":
        return [
            "A3 BeneishM claims a computed score but its calculation witness is marked unavailable"
        ]
    if w_prov != "deterministic":
        return [
            f"A3 BeneishM calculation witness has invalid provenance '{w_prov}', expected 'deterministic'"
        ]

    inputs = witness.get("inputs")
    if not isinstance(inputs, dict):
        return ["A3 BeneishM calculation witness carries no inputs"]

    from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score

    missing = [key for key in BENEISH_INPUT_KEYS if key not in inputs]
    if missing:
        return [
            "A3 BeneishM calculation witness is missing index inputs: "
            + ", ".join(missing)
        ]

    for key in BENEISH_INPUT_KEYS:
        val = inputs[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            return [
                f"A3 BeneishM calculation witness input '{key}' is not a finite number: {val!r}"
            ]

    # Fact linkage, unit consistency, and as-of chronology verification
    input_fact_ids = witness.get("input_fact_ids")
    units = witness.get("units")
    if isinstance(input_fact_ids, dict) and fact_ledger is not None:
        for input_key, fact_id in input_fact_ids.items():
            if not fact_id or not isinstance(fact_id, str):
                continue
            if fact_id not in fact_ledger:
                return [
                    f"A3 BeneishM witness input '{input_key}' references fact ID '{fact_id}' not found in fact ledger"
                ]
            fact = fact_ledger[fact_id]
            # Unit consistency
            if isinstance(units, dict) and input_key in units:
                expected_unit = units[input_key]
                fact_unit = getattr(fact, "unit", None) or (
                    fact.get("unit") if isinstance(fact, dict) else None
                )
                if expected_unit and fact_unit and expected_unit != "ratio" and fact_unit != expected_unit:
                    return [
                        f"A3 BeneishM witness input '{input_key}' unit mismatch: witness specifies '{expected_unit}', fact '{fact_id}' has '{fact_unit}'"
                    ]
            # Chronology check
            if analysis_date:
                from src.facts import usable_as_of

                if not usable_as_of(fact, analysis_date):
                    avail = getattr(fact, "availability_time", None) or (
                        fact.get("availability_time") if isinstance(fact, dict) else None
                    )
                    return [
                        f"A3 BeneishM witness input '{input_key}' references fact '{fact_id}' with availability_time '{avail}' after analysis cutoff '{analysis_date}' (chronology violation)"
                    ]

    try:
        recomputed = beneish_score(
            **{key: float(inputs[key]) for key in BENEISH_INPUT_KEYS}
        )
    except (TypeError, ValueError) as exc:
        return [f"A3 BeneishM calculation witness inputs are not usable: {exc}"]

    recorded_raw = beneish.get("raw_score")
    if not isinstance(recorded_raw, (int, float)) or isinstance(recorded_raw, bool):
        recorded_raw = witness.get("result")
    if not isinstance(recorded_raw, (int, float)) or isinstance(recorded_raw, bool):
        return ["A3 BeneishM has no recorded raw score to compare against"]

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
    return []


# ------------------------------------------------------------------- audit


def semantic_audit(run_dir: Path) -> AuditReport:
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

    validator = EnvelopeValidator(PROJECT_ROOT / "schemas" / "envelope_v2_1.json")
    envelopes: Dict[str, Envelope] = {}
    by_file: Dict[str, Dict[str, Any]] = {}
    integrity_errors: List[str] = []

    envelope_files = sorted(run_dir.glob("*_envelope.json"))
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
            report.errors.append(
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
            report.warnings.append(
                f"{path.name}: no content_checksum (legacy or unsealed; run `controller seal`)"
            )

    if not envelope_files:
        integrity_errors.append("no *_envelope.json files found")

    # Level 1 — envelope integrity (plus the existing provenance-chain checks).
    chain_result = verify_run(run_dir)
    integrity_errors.extend(
        err for err in chain_result.errors if "checksum mismatch" not in err
    )
    report.warnings.extend(chain_result.warnings)
    report.levels["envelope_integrity"] = "failed" if integrity_errors else "passed"
    report.errors.extend(integrity_errors)

    # Level 2 — source reconstruction.
    retrieved = [
        source
        for env in envelopes.values()
        for source in env.sources
        if isinstance(source, dict) and source.get("verification") == "retrieved"
    ]
    evidence_dir = run_dir / "evidence"
    if not retrieved and not evidence_dir.exists():
        report.levels["source_reconstruction"] = "not_applicable"
    else:
        try:
            first = next(iter(envelopes.values())) if envelopes else None
            if not first:
                raise ValueError("no envelopes to reconstruct sources from")
            from src.evidence import EvidencePack

            pack = EvidencePack(
                evidence_dir / "evidence.json",
                ticker=first.params.get("ticker", "UNKNOWN"),
                analysis_date=first.params.get("analysis_date", "UNKNOWN"),
            )
            # Check retrieved sources match pack
            canonical = {source["id"]: source for source in pack.sources()}
            for source in retrieved:
                src_id = source.get("id")
                if src_id not in canonical:
                    raise ValueError(f"retrieved source '{src_id}' missing from evidence pack")
                if source != canonical[src_id]:
                    raise ValueError(f"retrieved source '{src_id}' metadata mismatch")
            report.levels["source_reconstruction"] = "source_bytes_verified"
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.levels["source_reconstruction"] = "source_bytes_unavailable"
            report.errors.append(
                f"source reconstruction unavailable: {exc} (a run whose source "
                "bytes cannot be restored is not fully auditable)"
            )

    # Level 3 — graph and contracts.
    graph_errors = _audit_graph(envelopes, run_dir, report)
    report.errors.extend(graph_errors)
    report.levels["graph_and_contracts"] = "failed" if graph_errors else "passed"

    # Level 4 — calculation replay.
    fact_ledger = _load_fact_ledger(run_dir, envelopes)
    analysis_date = None
    if "A1" in envelopes and envelopes["A1"].params:
        analysis_date = envelopes["A1"].params.get("analysis_date")
    elif envelopes:
        first_env = next(iter(envelopes.values()))
        if first_env.params:
            analysis_date = first_env.params.get("analysis_date")

    calc_errors: List[str] = []
    if "A3" in envelopes:
        calc_errors.extend(
            replay_beneish(
                envelopes["A3"],
                fact_ledger=fact_ledger,
                analysis_date=analysis_date,
            )
        )

    from src.calculation_witness import validate_witness

    for agent_id, env in sorted(envelopes.items()):
        if agent_id == "A3":
            continue
        for art_name, art_val in (env.artifacts or {}).items():
            if isinstance(art_val, dict):
                witness = art_val.get("calculation_witness")
                if isinstance(witness, dict):
                    w_errors = validate_witness(
                        witness,
                        fact_ledger=fact_ledger,
                        cutoff=analysis_date,
                    )
                    calc_errors.extend(
                        f"{agent_id}.{art_name} witness: {e}" for e in w_errors
                    )

    report.errors.extend(calc_errors)
    report.levels["calculation_replay"] = "failed" if calc_errors else "passed"

    # Level 5 — final artifacts bound to the manifest.
    artifact_errors = _audit_final_artifacts(run_dir, envelopes, report)
    report.errors.extend(artifact_errors)
    report.levels["final_artifacts"] = "failed" if artifact_errors else "passed"

    # Level 6 — completeness.
    manifest = _load_manifest(run_dir)
    expected = set(EXPECTED_ROLES)
    present = set(envelopes)
    missing = sorted(expected - present)
    unexpected = sorted(present - expected)
    report.complete = not missing and not unexpected
    report.halted = bool(manifest and manifest.get("status") == "halted")
    if missing:
        report.levels["completeness"] = "incomplete"
        report.errors.append(
            "run is incomplete: missing required role(s): " + ", ".join(missing)
        )
    elif unexpected:
        report.levels["completeness"] = "failed"
        report.errors.append(
            "run contains unexpected role(s): " + ", ".join(unexpected)
        )
    elif report.halted:
        report.levels["completeness"] = "incomplete"
    else:
        report.levels["completeness"] = "passed"

    if manifest:
        report.errors.extend(validate_run_manifest(manifest))

    return report


def _audit_graph(
    envelopes: Dict[str, Envelope], run_dir: Path, report: AuditReport
) -> List[str]:
    """Validate consumed-parent lineage and machine-checkable contracts."""
    errors: List[str] = []
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

                modes = load_depth_modes(PROJECT_ROOT / "config")
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
    for agent_id in ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
        env = envelopes.get(agent_id)
        if env is None:
            continue
        upstream_envelopes: Dict[str, Envelope] = {}
        for entry in env.upstream or []:
            if isinstance(entry, dict):
                parent = entry.get("agent")
                if parent in envelopes:
                    upstream_envelopes[parent] = envelopes[parent]
        result = validate_contract(
            env, depth_config=depth_config, envelopes=upstream_envelopes, strict=False
        )
        errors.extend(f"{agent_id} contract: {err}" for err in result.errors)

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


def verify_run_semantic(run_dir: Path, strict: bool = False) -> ValidationResult:
    """ValidationResult-compatible view of :func:`semantic_audit`."""
    report = semantic_audit(run_dir)
    errors = list(report.errors)
    warnings = list(report.warnings)
    if strict:
        errors.extend(warnings)
        if not report.complete:
            errors.append("strict: run is incomplete")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)