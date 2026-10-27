"""
Run-directory verification for FUWForge.

Re-verifies everything that makes a run auditable: envelope schema compliance,
content checksums, upstream lineage references, and the provenance hash chain.
Missing integrity metadata (legacy runs, unsealed controller-mode envelopes) is
reported as a warning unless ``strict`` is set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from src.envelope import Envelope
from src.provenance import verify_chain
from src.resources import get_schema_path
from src.utils.checksum import envelope_content_checksum
from src.validators import EnvelopeValidator, ValidationResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The semantic audit lives in ``src.run_semantic_audit``; re-exported here so
# ``verify-run`` consumers keep one import surface.
from src.run_semantic_audit import (  # noqa: E402
    AUDIT_LEVELS,
    AuditReport,
    build_run_manifest,
    find_graph_cycles,
    replay_beneish,
    semantic_audit,
    validate_run_manifest,
    verify_run_semantic,
)

__all__ = [
    "AUDIT_LEVELS",
    "AuditReport",
    "build_run_manifest",
    "find_graph_cycles",
    "replay_beneish",
    "semantic_audit",
    "validate_run_manifest",
    "verify_run",
    "verify_run_semantic",
]


def verify_run(run_dir: Path, strict: bool = False) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    run_dir = Path(run_dir)

    if not run_dir.is_dir():
        return ValidationResult(valid=False, errors=[f"not a directory: {run_dir}"])

    validator = EnvelopeValidator(get_schema_path("envelope_v2_1.json"))
    checksums: Dict[str, str] = {}
    envelopes: Dict[str, Envelope] = {}

    envelope_files = sorted(run_dir.glob("*_envelope.json"))
    if not envelope_files:
        errors.append("no *_envelope.json files found")

    for path in envelope_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON ({exc})")
            continue
        except Exception as exc:
            errors.append(f"{path.name}: error reading envelope ({exc})")
            continue

        if not isinstance(data, dict):
            errors.append(f"{path.name}: envelope root is not a dictionary")
            continue

        schema_result = validator.validate(data)
        errors.extend(f"{path.name}: {err}" for err in schema_result.errors)

        agent_id = str((data.get("agent") or {}).get("id") or path.stem.replace("_envelope", "").upper())
        if agent_id in envelopes:
            errors.append(f"duplicate role ID '{agent_id}' appears in more than one envelope ({path.name})")
            continue

        env = Envelope.from_dict(data)
        envelopes[agent_id] = env

        recorded = env.content_checksum
        if recorded:
            actual = envelope_content_checksum(data)
            if actual != recorded:
                errors.append(
                    f"{path.name}: content checksum mismatch (file was modified after sealing)"
                )
            else:
                checksums[agent_id] = actual
        else:
            warnings.append(
                f"{path.name}: no content_checksum (legacy or unsealed; run `controller seal`)"
            )

    retrieved = [
        source
        for env in envelopes.values()
        for source in env.sources
        if isinstance(source, dict) and source.get("verification") == "retrieved"
    ]
    evidence_path = run_dir / "evidence" / "evidence.json"
    if retrieved or evidence_path.exists():
        try:
            from src.evidence import EvidencePack

            first = next(iter(envelopes.values())) if envelopes else None
            if not first:
                raise ValueError("no envelopes found to verify evidence")
            pack = EvidencePack(evidence_path, ticker=first.params.get("ticker", "UNKNOWN"), analysis_date=first.params.get("analysis_date", "UNKNOWN"))
            canonical = {source["id"]: source for source in pack.sources()}
            for source in retrieved:
                src_id = source.get("id")
                if src_id not in canonical:
                    errors.append(f"retrieved source '{src_id}' missing from cached evidence pack")
                elif source != canonical[src_id]:
                    errors.append("retrieved source metadata does not match the cached evidence pack")
            summary_path = run_dir / "run_summary.json"
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    if isinstance(summary, dict) and summary.get("evidence_checksum") and summary.get("evidence_checksum") != pack.checksum:
                        errors.append("run_summary evidence checksum does not match the cached pack")
                except Exception:
                    pass
        except Exception as exc:
            errors.append(f"evidence verification failed: {exc}")

    # Build graph and check cycles
    graph_adj: Dict[str, List[str]] = {}
    for agent_id, env in envelopes.items():
        graph_adj[agent_id] = [
            str(ref.get("agent"))
            for ref in (env.upstream or [])
            if isinstance(ref, dict) and ref.get("agent")
        ]

    cycles = find_graph_cycles(graph_adj)
    for cycle in cycles:
        errors.append(f"cycle detected in upstream graph: {' -> '.join(cycle)}")

    for agent_id, env in envelopes.items():
        seen = set()
        for reference in env.upstream:
            if not isinstance(reference, dict):
                errors.append(f"{agent_id}: upstream entry is not a dictionary")
                continue
            ref_agent = reference.get("agent")
            ref_checksum = reference.get("content_checksum")
            if not ref_agent:
                errors.append(f"{agent_id}: upstream entry missing agent id")
                continue
            if ref_agent == agent_id:
                errors.append(f"{agent_id}: self-referential upstream cycle ({agent_id} -> {agent_id})")
                continue
            if ref_agent in seen:
                errors.append(f"{agent_id}: duplicate upstream entry for {ref_agent}")
            seen.add(ref_agent)
            if ref_agent in checksums:
                if checksums[ref_agent] != ref_checksum:
                    errors.append(
                        f"{agent_id}: upstream {ref_agent} checksum does not match the referenced envelope"
                    )
            elif ref_agent in envelopes:
                warnings.append(
                    f"{agent_id}: upstream {ref_agent} referenced but unsealed"
                )
            else:
                errors.append(
                    f"{agent_id}: upstream {ref_agent} referenced but envelope missing from the run"
                )

    provenance_files = sorted(run_dir.glob("*_provenance.log"))
    if not provenance_files:
        warnings.append("no provenance log found")
    for prov_path in provenance_files:
        chain_result = verify_chain(prov_path)
        errors.extend(f"{prov_path.name}: {err}" for err in chain_result.errors)

    if not list(run_dir.glob("*_report.md")):
        warnings.append("no assembled report found")
    if not list(run_dir.glob("*_artifacts.json")):
        warnings.append("no artifacts bundle found")

    if strict:
        errors.extend(f"strict: {warning}" for warning in warnings)

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)

