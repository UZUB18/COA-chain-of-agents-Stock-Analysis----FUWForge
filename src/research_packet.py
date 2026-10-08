"""
Versioned research-packet export.

Emits a bounded, reproducible research artifact for downstream review and dashboard
consumption. Contains typed facts, calculation witnesses, claim references, bounded
source metadata, calculator/formula versions, explicit limitations, and audit state.

Rules:
1. The packet does NOT claim dashboard approval; it states plainly that it is an
   unapproved research artifact pending human review.
2. Full filing texts, raw agent transcripts, and personal data are never embedded.
3. The written file is hashed externally (e.g. SHA-256 of the immutable output bytes);
   no self-referential content checksum is embedded within the packet.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from src.envelope import Envelope
from src.run_semantic_audit import semantic_audit
from src.utils.beneish import CALCULATOR_VERSION as BENEISH_CALCULATOR_VERSION
from src.utils.checksum import checksum_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "research_packet_v1.json"

PACKET_SCHEMA_VERSION = "research_packet_v1"
DISCLAIMER_TEXT = (
    "Unapproved research artifact pending human review; does not constitute "
    "dashboard approval or investment recommendation."
)
APPROVAL_STATUS_PENDING = "pending_human_review"

#: Forbidden keys in bounded source references or artifacts (raw texts or transcripts).
FORBIDDEN_SOURCE_KEYS = frozenset(
    {
        "text",
        "raw_text",
        "body",
        "content",
        "transcript",
        "raw_html",
        "excerpt",
        "prompt",
        "raw_response",
        "system_prompt",
        "agent_transcript",
        "transcript_tail",
        "full_text",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _today_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def file_sha256(path: Union[str, Path]) -> str:
    """Compute the SHA-256 digest of a file (formatted as 'sha256:<64-hex>')."""
    return checksum_file(Path(path))


def build_packet(
    run_dir: Union[str, Path],
    *,
    packet_version: str = PACKET_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """
    Build a bounded, versioned research packet from a run directory.

    Reads the run's envelopes, manifest, and summaries, and extracts bounded facts,
    calculation witnesses, claims, source references, versions, and limitations.
    """
    run_dir = Path(run_dir)
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    envelope_files = sorted(run_dir.glob("*_envelope.json"))
    envelopes: Dict[str, Envelope] = {}
    for ef in envelope_files:
        try:
            data = json.loads(ef.read_text(encoding="utf-8"))
            agent_id = str((data.get("agent") or {}).get("id") or ef.stem.replace("_envelope", "").upper())
            envelopes[agent_id] = Envelope.from_dict(data)
        except Exception:
            continue

    manifest_path = run_dir / "run_manifest.json"
    manifest: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    summary_path = run_dir / "run_summary.json"
    summary: Dict[str, Any] = {}
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}

    # Extract company identity
    params: Dict[str, Any] = {}
    if "A1" in envelopes:
        params = envelopes["A1"].params or {}
    elif envelopes:
        params = next(iter(envelopes.values())).params or {}
    elif manifest:
        params = {"ticker": manifest.get("ticker"), "analysis_date": manifest.get("analysis_date")}

    ticker = str(params.get("ticker") or manifest.get("ticker") or "UNKNOWN").upper()
    company_name = str(params.get("company") or summary.get("company") or ticker)
    analysis_date = str(params.get("analysis_date") or manifest.get("analysis_date") or _today_date())

    run_id = str(
        manifest.get("run_id")
        or summary.get("run_id")
        or (next(iter(envelopes.values())).metrics or {}).get("run_id", "")
        or run_dir.name
    )

    # Perform semantic audit
    audit_report = semantic_audit(run_dir)
    audit_dict = audit_report.to_dict()

    status = (
        manifest.get("status")
        or ("completed" if audit_report.complete and not audit_report.halted and not audit_report.errors else (
            "halted" if audit_report.halted else "incomplete"
        ))
    )

    # Extract typed facts if ledger exists
    facts_list: List[Dict[str, Any]] = []
    facts_path: Optional[Path] = None
    if (run_dir / "facts").is_dir():
        facts_path = run_dir / "facts"
    elif (run_dir / "facts.json").is_file():
        facts_path = run_dir / "facts.json"
    elif (run_dir / "fact_ledger.json").is_file():
        facts_path = run_dir / "fact_ledger.json"

    if facts_path:
        from src.facts import load_fact_ledger

        try:
            loaded = load_fact_ledger(facts_path)
            facts_list = [f.to_dict() for f in loaded]
        except Exception:
            facts_list = []

    if not facts_list:
        for env in envelopes.values():
            art_facts = env.artifacts.get("facts") or env.artifacts.get("fact_ledger")
            if isinstance(art_facts, list):
                for f in art_facts:
                    if isinstance(f, dict) and "id" in f:
                        facts_list.append(dict(f))

    # Bound facts: strip any raw text/unbounded data
    bounded_facts: List[Dict[str, Any]] = []
    for f in facts_list:
        if not isinstance(f, dict):
            continue
        bf = {
            "id": str(f.get("id")),
            "company": str(f.get("company") or ticker),
            "concept": str(f.get("concept")),
            "value": f.get("value"),
            "unit": f.get("unit"),
            "currency": f.get("currency"),
            "period_start": f.get("period_start"),
            "period_end": f.get("period_end"),
            "period_instant": f.get("period_instant"),
            "filing_date": f.get("filing_date"),
            "accession": f.get("accession"),
            "source_location": f.get("source_location") if isinstance(f.get("source_location"), dict) else None,
            "accounting_basis": f.get("accounting_basis"),
            "kind": f.get("kind"),
            "supersedes": f.get("supersedes"),
            "missing_reason": f.get("missing_reason"),
            "extractor_version": f.get("extractor_version"),
        }
        bounded_facts.append(bf)

    # Extract calculation witnesses
    calculations: List[Dict[str, Any]] = []
    seen_calcs: Set[str] = set()
    for agent_id, env in sorted(envelopes.items()):
        for art_name, art_val in env.artifacts.items():
            if isinstance(art_val, dict):
                witness = art_val.get("calculation_witness")
                if isinstance(witness, dict) and witness.get("formula_id"):
                    key = f"{witness.get('formula_id')}:{witness.get('calculator_id')}:{witness.get('result')}"
                    if key not in seen_calcs:
                        seen_calcs.add(key)
                        calculations.append(witness)
                elif art_val.get("formula_id") and art_val.get("calculator_id"):
                    key = f"{art_val.get('formula_id')}:{art_val.get('calculator_id')}:{art_val.get('result')}"
                    if key not in seen_calcs:
                        seen_calcs.add(key)
                        calculations.append(art_val)
            elif isinstance(art_val, list):
                for item in art_val:
                    if isinstance(item, dict):
                        witness = item.get("calculation_witness")
                        if isinstance(witness, dict) and witness.get("formula_id"):
                            key = f"{witness.get('formula_id')}:{witness.get('calculator_id')}:{witness.get('result')}"
                            if key not in seen_calcs:
                                seen_calcs.add(key)
                                calculations.append(witness)

    # Extract bounded source references
    sources: List[Dict[str, Any]] = []
    seen_sources: Set[str] = set()
    for env in envelopes.values():
        for src in env.sources:
            if not isinstance(src, dict):
                continue
            src_id = str(src.get("id") or src.get("url") or f"src_{len(sources) + 1}")
            if src_id in seen_sources:
                continue
            seen_sources.add(src_id)
            sources.append(
                {
                    "id": src_id,
                    "title": src.get("title"),
                    "url": src.get("url"),
                    "date": src.get("date") or src.get("filing_date"),
                    "filing_date": src.get("filing_date"),
                    "form_type": src.get("form_type"),
                    "accession": src.get("accession"),
                    "checksum": src.get("checksum") or src.get("sha256") or src.get("body_sha256"),
                    "verification": src.get("verification"),
                }
            )

    evidence_json = run_dir / "evidence" / "evidence.json"
    if evidence_json.is_file():
        try:
            ev_data = json.loads(evidence_json.read_text(encoding="utf-8"))
            for doc in ev_data.get("documents", []):
                if isinstance(doc, dict):
                    doc_id = str(doc.get("id") or doc.get("url") or f"doc_{len(sources) + 1}")
                    if doc_id not in seen_sources:
                        seen_sources.add(doc_id)
                        sources.append(
                            {
                                "id": doc_id,
                                "title": doc.get("title"),
                                "url": doc.get("url"),
                                "date": doc.get("filing_date") or doc.get("date"),
                                "filing_date": doc.get("filing_date"),
                                "form_type": doc.get("form"),
                                "accession": doc.get("accession"),
                                "checksum": doc.get("body_sha256"),
                                "verification": "retrieved",
                            }
                        )
        except Exception:
            pass

    # Extract claims
    claims: List[Dict[str, Any]] = []
    # 1. Bull cases from A6B / A6
    bull_items = []
    if "A6B" in envelopes:
        bull_items = envelopes["A6B"].artifacts.get("BullCase", [])
    elif "A6" in envelopes:
        bull_items = envelopes["A6"].artifacts.get("BullCase", [])
    if isinstance(bull_items, list):
        for idx, item in enumerate(bull_items):
            if isinstance(item, dict):
                ev = item.get("evidence")
                ev_list = ev if isinstance(ev, list) else ([ev] if ev else [])
                f_ids = item.get("fact_ids") or [e for e in ev_list if isinstance(e, str) and e.startswith("fact_")]
                claims.append(
                    {
                        "id": str(item.get("id") or f"B{idx + 1}"),
                        "agent_id": "A6B",
                        "category": "bull_thesis",
                        "statement": item.get("thesis"),
                        "thesis": item.get("thesis"),
                        "evidence": [str(x) for x in ev_list],
                        "fact_ids": [str(x) for x in f_ids],
                        "calculation_ids": [str(x) for x in (item.get("calculation_ids") or [])],
                        "impact": item.get("impact"),
                        "probability": item.get("probability"),
                        "severity": item.get("severity"),
                        "details": None,
                    }
                )

    # 2. Bear rebuttals from A6R / A6
    bear_items = []
    if "A6R" in envelopes:
        bear_items = envelopes["A6R"].artifacts.get("BearRebuttals", [])
    elif "A6" in envelopes:
        bear_items = envelopes["A6"].artifacts.get("BearRebuttals", [])
    if isinstance(bear_items, list):
        for idx, item in enumerate(bear_items):
            if isinstance(item, dict):
                claims.append(
                    {
                        "id": f"bear_rebuttal_{item.get('bull_id', idx + 1)}",
                        "agent_id": "A6R",
                        "category": "bear_rebuttal",
                        "statement": item.get("counter"),
                        "thesis": None,
                        "evidence": [str(x) for x in (item.get("evidence") or [])],
                        "fact_ids": [str(x) for x in (item.get("fact_ids") or [])],
                        "calculation_ids": [str(x) for x in (item.get("calculation_ids") or [])],
                        "impact": None,
                        "probability": item.get("probability"),
                        "severity": item.get("severity"),
                        "details": {"bull_id": item.get("bull_id")},
                    }
                )

    # 3. Danger zone dashboard from A6
    if "A6" in envelopes:
        dz_items = envelopes["A6"].artifacts.get("DangerZoneDashboard", [])
        if isinstance(dz_items, list):
            for idx, item in enumerate(dz_items):
                if isinstance(item, dict):
                    claims.append(
                        {
                            "id": f"danger_{item.get('category', idx + 1)}",
                            "agent_id": "A6",
                            "category": "danger_zone",
                            "statement": f"{item.get('category')}: {item.get('flag')} - {item.get('note')}",
                            "thesis": None,
                            "evidence": [],
                            "fact_ids": [],
                            "calculation_ids": [],
                            "impact": None,
                            "probability": None,
                            "severity": item.get("flag"),
                            "details": item,
                        }
                    )

        catalysts = envelopes["A6"].artifacts.get("Catalysts", [])
        if isinstance(catalysts, list):
            for idx, cat in enumerate(catalysts):
                claims.append(
                    {
                        "id": f"catalyst_{idx + 1}",
                        "agent_id": "A6",
                        "category": "catalyst",
                        "statement": str(cat),
                        "thesis": None,
                        "evidence": [],
                        "fact_ids": [],
                        "calculation_ids": [],
                        "impact": None,
                        "probability": None,
                        "severity": None,
                        "details": None,
                    }
                )

        headwinds = envelopes["A6"].artifacts.get("Headwinds", [])
        if isinstance(headwinds, list):
            for idx, head in enumerate(headwinds):
                claims.append(
                    {
                        "id": f"headwind_{idx + 1}",
                        "agent_id": "A6",
                        "category": "headwind",
                        "statement": str(head),
                        "thesis": None,
                        "evidence": [],
                        "fact_ids": [],
                        "calculation_ids": [],
                        "impact": None,
                        "probability": None,
                        "severity": None,
                        "details": None,
                    }
                )

    # 4. Scorecard & comparison fields from A7
    if "A7" in envelopes:
        scorecard = envelopes["A7"].artifacts.get("Scorecard")
        if isinstance(scorecard, dict):
            claims.append(
                {
                    "id": "scorecard_total",
                    "agent_id": "A7",
                    "category": "scorecard",
                    "statement": f"Overall score: {scorecard.get('total_score', 'N/A')}",
                    "thesis": None,
                    "evidence": [],
                    "fact_ids": [],
                    "calculation_ids": [],
                    "impact": None,
                    "probability": None,
                    "severity": None,
                    "details": scorecard,
                }
            )
        comp = envelopes["A7"].artifacts.get("comparison_fields")
        if isinstance(comp, dict):
            claims.append(
                {
                    "id": "comparison_fields",
                    "agent_id": "A7",
                    "category": "comparison_fields",
                    "statement": f"Quality score {comp.get('quality_score_10')}, moat {comp.get('moat_score_10')}",
                    "thesis": None,
                    "evidence": [],
                    "fact_ids": [],
                    "calculation_ids": [],
                    "impact": None,
                    "probability": None,
                    "severity": None,
                    "details": comp,
                }
            )

    # Collect versions
    versions: Dict[str, str] = {
        "packet_schema_version": packet_version,
        "beneish_m_score": BENEISH_CALCULATOR_VERSION,
        "run_manifest_version": "run_manifest_v1",
        "envelope_schema_version": "2.1",
        "fact_schema_version": "financial_fact_v1",
    }
    for k, v in manifest.get("calculator_versions", {}).items():
        versions[str(k)] = str(v)

    # Compile limitations
    limitations: List[str] = [DISCLAIMER_TEXT]
    if not audit_report.complete:
        limitations.append(
            "Run is incomplete; results reflect a partial agent graph and must not be treated as a full report."
        )
    if audit_report.halted:
        limitations.append("Run was halted early during execution.")
    if audit_report.overall_state == "failed":
        limitations.append("Run failed semantic or calculation verification checks.")
    if not bounded_facts:
        limitations.append("No typed financial fact ledger was attached to this run.")

    for w in audit_report.warnings:
        msg = f"Audit warning: {w}"
        if msg not in limitations:
            limitations.append(msg)
    for e in audit_report.errors:
        msg = f"Audit error: {e}"
        if msg not in limitations:
            limitations.append(msg)

    # Missing data from envelopes
    for agent_id, env in sorted(envelopes.items()):
        missing_map = (env.metrics or {}).get("missing_data") or {}
        for key, reason in missing_map.items():
            lim = f"Missing data ({agent_id}.{key}): {reason}"
            if lim not in limitations:
                limitations.append(lim)

    return {
        "schema_version": packet_version,
        "run_id": run_id,
        "company": {
            "ticker": ticker,
            "name": company_name,
        },
        "analysis_date": analysis_date,
        "generated_at": _now_iso(),
        "status": status,
        "approval_status": APPROVAL_STATUS_PENDING,
        "disclaimer": DISCLAIMER_TEXT,
        "audit": audit_dict,
        "facts": bounded_facts,
        "calculations": calculations,
        "claims": claims,
        "sources": sources,
        "versions": versions,
        "limitations": limitations,
    }


def validate_packet(
    packet: Dict[str, Any],
    *,
    run_dir: Optional[Union[str, Path]] = None,
    expected_ticker: Optional[str] = None,
    expected_company: Optional[str] = None,
) -> List[str]:
    """
    Validate a research packet against schemas/research_packet_v1.json and semantic rules.

    Checks:
    - Schema version matches PACKET_SCHEMA_VERSION
    - Structural conformance against JSON schema
    - Company identity (matches A1 params / expected parameters)
    - Fact references in claims: every fact ID referenced must exist in packet.facts
    - Explicit audit state is present and consistent
    - Limitations list is present and non-empty
    - Unapproved status is preserved (cannot claim dashboard approval)
    - Boundedness (no raw transcripts or full filing bodies)
    """
    errors: List[str] = []

    if not isinstance(packet, dict):
        return ["Research packet must be a dictionary"]

    # 1. Schema version check
    version = packet.get("schema_version")
    if version != PACKET_SCHEMA_VERSION:
        errors.append(
            f"Unsupported schema version: {version!r}; expected {PACKET_SCHEMA_VERSION!r}"
        )

    # 2. JSON Schema validation
    if SCHEMA_PATH.is_file():
        try:
            import jsonschema

            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            validator = jsonschema.Draft202012Validator(schema)
            for err in validator.iter_errors(packet):
                path_str = "/".join(str(p) for p in err.path) or "<root>"
                errors.append(f"packet schema error at {path_str}: {err.message}")
        except ImportError:
            pass
    else:
        errors.append("research_packet_v1.json schema file is missing")

    # 3. Company identity
    company = packet.get("company")
    if isinstance(company, dict):
        pkt_ticker = company.get("ticker")
        pkt_name = company.get("name")
        if not pkt_ticker:
            errors.append("Packet company is missing ticker")
        if not pkt_name:
            errors.append("Packet company is missing name")

        # Compare with run_dir A1 params if provided
        if run_dir:
            run_dir = Path(run_dir)
            a1_path = run_dir / "a1_envelope.json"
            if a1_path.is_file():
                try:
                    a1_data = json.loads(a1_path.read_text(encoding="utf-8"))
                    a1_params = a1_data.get("params") or {}
                    if a1_params.get("ticker") and pkt_ticker != a1_params.get("ticker"):
                        errors.append(
                            f"Company ticker mismatch: packet has {pkt_ticker!r}, but run A1 specifies {a1_params.get('ticker')!r}"
                        )
                    if a1_params.get("company") and pkt_name != a1_params.get("company"):
                        errors.append(
                            f"Company name mismatch: packet has {pkt_name!r}, but run A1 specifies {a1_params.get('company')!r}"
                        )
                except Exception:
                    pass

        # Compare with expected params if provided
        if expected_ticker and pkt_ticker != expected_ticker:
            errors.append(
                f"Company ticker mismatch: packet has {pkt_ticker!r}, expected {expected_ticker!r}"
            )
        if expected_company and pkt_name != expected_company:
            errors.append(
                f"Company name mismatch: packet has {pkt_name!r}, expected {expected_company!r}"
            )

        # Ensure all facts match company ticker
        for fact in packet.get("facts", []):
            if isinstance(fact, dict) and fact.get("company"):
                if fact.get("company") != pkt_ticker:
                    errors.append(
                        f"Fact '{fact.get('id')}' company '{fact.get('company')}' does not match packet ticker '{pkt_ticker}'"
                    )

    # 4. Fact ID and calculation ID references in claims
    known_fact_ids = {
        f.get("id")
        for f in packet.get("facts", [])
        if isinstance(f, dict) and f.get("id")
    }
    known_calc_ids = set()
    for c in packet.get("calculations", []):
        if isinstance(c, dict):
            if c.get("formula_id"):
                known_calc_ids.add(str(c["formula_id"]))
            if c.get("calculator_id"):
                known_calc_ids.add(str(c["calculator_id"]))
            if c.get("id"):
                known_calc_ids.add(str(c["id"]))

    for claim in packet.get("claims", []):
        if isinstance(claim, dict):
            claim_id = claim.get("id", "<unnamed>")
            for fid in claim.get("fact_ids") or []:
                if fid not in known_fact_ids:
                    errors.append(
                        f"Claim '{claim_id}' references fact ID '{fid}', which is missing from the packet's facts ledger"
                    )
            for cid in claim.get("calculation_ids") or []:
                if cid not in known_calc_ids:
                    errors.append(
                        f"Claim '{claim_id}' references calculation ID '{cid}', which is missing from the packet's calculations"
                    )

    # 5. Explicit audit state check
    audit = packet.get("audit")
    if not isinstance(audit, dict):
        errors.append("Packet must contain an explicit audit dictionary")
    else:
        overall = audit.get("overall_state")
        if overall not in ("verified", "incomplete", "failed"):
            errors.append(
                f"Invalid audit overall_state: {overall!r}; must be 'verified', 'incomplete', or 'failed'"
            )
        levels = audit.get("levels")
        if not isinstance(levels, dict) or not levels:
            errors.append("Packet audit must contain per-level audit states")
        if audit.get("complete") is False and overall == "verified":
            errors.append(
                "Audit overall_state cannot be 'verified' when the run is marked incomplete"
            )

    # 6. Approval status check
    approval = packet.get("approval_status")
    if approval in ("approved", "published", "dashboard_approved"):
        errors.append(
            f"Packet cannot claim approval status {approval!r}; must be pending human review"
        )

    # 7. Limitations check
    limitations = packet.get("limitations")
    if not isinstance(limitations, list) or len(limitations) == 0:
        errors.append("Packet must include a non-empty limitations list")

    # 8. Boundedness: source references and structured artifacts must not contain full text/body
    for src in packet.get("sources", []):
        if isinstance(src, dict):
            forbidden = set(src.keys()) & FORBIDDEN_SOURCE_KEYS
            if forbidden:
                errors.append(
                    f"Source '{src.get('id')}' contains unbounded keys ({', '.join(sorted(forbidden))}); source references must be metadata only"
                )

    for fact in packet.get("facts", []):
        if isinstance(fact, dict):
            forbidden = set(fact.keys()) & FORBIDDEN_SOURCE_KEYS
            if forbidden:
                errors.append(
                    f"Fact '{fact.get('id')}' contains unbounded keys ({', '.join(sorted(forbidden))}); raw transcripts or full text are forbidden in facts"
                )
            f_val = fact.get("value")
            if f_val is not None:
                if isinstance(f_val, bool) or not isinstance(f_val, (int, float)):
                    errors.append(f"Fact '{fact.get('id')}' value must be a numeric float or null, got {type(f_val).__name__}")
                elif not isinstance(f_val, bool) and isinstance(f_val, (int, float)) and not math.isfinite(f_val):
                    errors.append(f"Fact '{fact.get('id')}' value must be finite (not NaN or Inf)")

    for claim in packet.get("claims", []):
        if isinstance(claim, dict):
            forbidden = set(claim.keys()) & FORBIDDEN_SOURCE_KEYS
            if forbidden:
                errors.append(
                    f"Claim '{claim.get('id')}' contains unbounded keys ({', '.join(sorted(forbidden))})"
                )

    return errors


def export_packet(
    run_dir: Union[str, Path],
    output_path: Union[str, Path],
    *,
    packet: Optional[Dict[str, Any]] = None,
    packet_version: str = PACKET_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """
    Build, validate, and write a versioned research packet to output_path.

    Writes canonical UTF-8 JSON. Computes the external SHA-256 digest of the written file.
    Raises ValueError on validation failure.
    """
    run_path = Path(run_dir)
    out_path = Path(output_path)

    if packet is None:
        packet = build_packet(run_path, packet_version=packet_version)

    errors = validate_packet(packet, run_dir=run_path)
    if errors:
        raise ValueError(
            "Research packet validation failed:\n- " + "\n- ".join(errors)
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    out_path.write_text(serialized, encoding="utf-8")

    digest = file_sha256(out_path)
    return {
        "path": str(out_path),
        "sha256": digest,
        "bytes": out_path.stat().st_size,
        "packet": packet,
    }
