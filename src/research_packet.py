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

from src.data_gaps import missing_data_map
from src.envelope import Envelope
from src.resources import get_schema_path
from src.run_semantic_audit import _load_fact_ledger, semantic_audit
from src.utils.beneish import CALCULATOR_VERSION as BENEISH_CALCULATOR_VERSION
from src.utils.checksum import checksum_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = get_schema_path("research_packet_v1.json")

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


def file_sha256(path: Union[str, Path]) -> str:
    """Compute the SHA-256 digest of a file (formatted as 'sha256:<64-hex>')."""
    return checksum_file(Path(path))


def _load_run_envelopes(run_dir: Path) -> Dict[str, Envelope]:
    """Load all envelopes in a run directory."""
    envelope_files = sorted(run_dir.glob("*_envelope.json"))
    envelopes: Dict[str, Envelope] = {}
    for ef in envelope_files:
        try:
            data = json.loads(ef.read_text(encoding="utf-8"))
            agent_id = str((data.get("agent") or {}).get("id") or ef.stem.replace("_envelope", "").upper())
            envelopes[agent_id] = Envelope.from_dict(data)
        except Exception:
            continue
    return envelopes


def _load_run_manifest(run_dir: Path) -> Dict[str, Any]:
    """Load run manifest if present."""
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.is_file():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _is_finite_number(val: Any) -> bool:
    """Check if val is an int or float that represents a finite number."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    try:
        f = float(val)
        return math.isfinite(f)
    except (OverflowError, ValueError, TypeError):
        return False


def _project_fact(
    f: Any,
    *,
    default_ticker: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Canonically project a typed Fact or fact dictionary to research_packet_v1 schema.

    Maps schema aliases (instant -> period_instant, availability_time -> filing_date,
    filing_accession -> accession), strips forbidden raw source keys from dimensions/metadata/
    source_location, normalizes enums and accounting basis, and guarantees consistent field types.
    """
    if hasattr(f, "to_dict"):
        f_dict = f.to_dict()
    elif isinstance(f, dict):
        f_dict = dict(f)
    else:
        return None

    # Correct fact projection schema aliases:
    # Schema uses: period_instant, filing_date, accession.
    # Real Fact uses: instant, availability_time, filing_accession.
    period_instant = f_dict.get("period_instant") or f_dict.get("instant")
    filing_date = f_dict.get("filing_date") or f_dict.get("availability_time")
    accession = f_dict.get("accession") or f_dict.get("filing_accession")

    # Normalize accounting basis
    basis = f_dict.get("accounting_basis")
    if basis is not None and hasattr(basis, "value"):
        basis = basis.value
    elif basis is not None:
        basis = str(basis)

    # Normalize kind
    kind_val = f_dict.get("kind")
    if kind_val is not None and hasattr(kind_val, "value"):
        kind_val = kind_val.value
    elif kind_val is not None:
        kind_val = str(kind_val)

    source_loc = f_dict.get("source_location")
    if isinstance(source_loc, dict):
        source_loc = {k: v for k, v in source_loc.items() if k not in FORBIDDEN_SOURCE_KEYS}
    elif not isinstance(source_loc, str):
        source_loc = None

    dims = f_dict.get("dimensions")
    if not isinstance(dims, dict):
        dims = None
    else:
        dims = {k: v for k, v in dims.items() if k not in FORBIDDEN_SOURCE_KEYS}

    meta = f_dict.get("metadata")
    if not isinstance(meta, dict):
        meta = None
    else:
        meta = {k: v for k, v in meta.items() if k not in FORBIDDEN_SOURCE_KEYS}

    val = f_dict.get("value")
    if val is not None and not isinstance(val, bool):
        try:
            f_val = float(val)
            if math.isfinite(f_val):
                val = f_val
        except (OverflowError, ValueError, TypeError):
            pass

    company = f_dict.get("company")
    if not company and default_ticker:
        company = default_ticker

    return {
        "id": str(f_dict.get("id")),
        "company": str(company) if company is not None else None,
        "concept": str(f_dict.get("concept")),
        "value": val,
        "unit": str(f_dict["unit"]) if f_dict.get("unit") is not None else None,
        "currency": str(f_dict["currency"]) if f_dict.get("currency") is not None else None,
        "period_start": str(f_dict["period_start"]) if f_dict.get("period_start") is not None else None,
        "period_end": str(f_dict["period_end"]) if f_dict.get("period_end") is not None else None,
        "period_instant": str(period_instant) if period_instant is not None else None,
        "filing_date": str(filing_date) if filing_date is not None else None,
        "accession": str(accession) if accession is not None else None,
        "source_location": source_loc,
        "accounting_basis": basis,
        "kind": kind_val,
        "supersedes": str(f_dict["supersedes"]) if f_dict.get("supersedes") is not None else None,
        "missing_reason": str(f_dict["missing_reason"]) if f_dict.get("missing_reason") is not None else None,
        "extractor_version": str(f_dict["extractor_version"]) if f_dict.get("extractor_version") is not None else None,
        "dimensions": dims,
        "metadata": meta,
    }


def _compare_projected_facts(
    fid: str,
    pkt_fact: Dict[str, Any],
    canonical_fact: Dict[str, Any],
) -> List[str]:
    """
    Compare every projected field of a packet fact against the canonical projection
    of the reconstructed ledger fact.
    """
    diffs: List[str] = []

    # Extra fields not in canonical projection
    extra_keys = set(pkt_fact.keys()) - set(canonical_fact.keys())
    if extra_keys:
        diffs.append(
            f"Fact '{fid}' contains unexpected extra fields: {', '.join(sorted(extra_keys))}"
        )

    # Value comparison with exact finite numeric equality and None checking
    pkt_val = pkt_fact.get("value")
    can_val = canonical_fact.get("value")
    if pkt_val is None and can_val is None:
        pass
    elif pkt_val is None or can_val is None:
        diffs.append(
            f"Fact '{fid}' value mismatch: packet has {pkt_val!r}, reconstructed ledger has {can_val!r}"
        )
    elif isinstance(pkt_val, bool) or not isinstance(pkt_val, (int, float)):
        diffs.append(
            f"Fact '{fid}' value must be numeric float, got {type(pkt_val).__name__}"
        )
    elif isinstance(can_val, bool) or not isinstance(can_val, (int, float)):
        diffs.append(
            f"Fact '{fid}' canonical value must be numeric float, got {type(can_val).__name__}"
        )
    elif not _is_finite_number(pkt_val):
        diffs.append(
            f"Fact '{fid}' value must be finite (not NaN or Inf)"
        )
        if pkt_val != can_val:
            diffs.append(
                f"Fact '{fid}' value mismatch: packet has {pkt_val!r}, reconstructed ledger has {can_val!r}"
            )
    elif not _is_finite_number(can_val):
        diffs.append(
            f"Fact '{fid}' canonical value must be finite (not NaN or Inf)"
        )
        if pkt_val != can_val:
            diffs.append(
                f"Fact '{fid}' value mismatch: packet has {pkt_val!r}, reconstructed ledger has {can_val!r}"
            )
    elif pkt_val != can_val:
        diffs.append(
            f"Fact '{fid}' value mismatch: packet has {pkt_val!r}, reconstructed ledger has {can_val!r}"
        )

    comparable_fields = (
        "company",
        "concept",
        "unit",
        "currency",
        "period_start",
        "period_end",
        "period_instant",
        "filing_date",
        "accession",
        "source_location",
        "accounting_basis",
        "kind",
        "supersedes",
        "missing_reason",
        "extractor_version",
        "dimensions",
        "metadata",
    )
    for field in comparable_fields:
        pkt_field_val = pkt_fact.get(field)
        can_field_val = canonical_fact.get(field)
        if pkt_field_val != can_field_val:
            diffs.append(
                f"Fact '{fid}' {field} mismatch: packet has {pkt_field_val!r}, reconstructed ledger has {can_field_val!r}"
            )

    return diffs


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
    envelopes = _load_run_envelopes(run_dir)
    manifest = _load_run_manifest(run_dir)

    summary_path = run_dir / "run_summary.json"
    summary: Dict[str, Any] = {}
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}

    # Extract company identity
    params: Dict[str, Any] = {}
    if "A1" in envelopes and envelopes["A1"].params:
        params = envelopes["A1"].params or {}
    elif envelopes:
        for env in envelopes.values():
            if env.params:
                params = env.params
                break
    elif manifest:
        params = {"ticker": manifest.get("ticker"), "analysis_date": manifest.get("analysis_date")}

    ticker = params.get("ticker") or manifest.get("ticker") or summary.get("params", {}).get("ticker") or summary.get("ticker")
    if not ticker and (run_dir / "evidence" / "evidence.json").is_file():
        try:
            ev_raw = json.loads((run_dir / "evidence" / "evidence.json").read_text(encoding="utf-8"))
            ticker = ev_raw.get("ticker")
        except Exception:
            pass
    ticker = str(ticker or "UNKNOWN").strip().upper()

    company_name = str(
        params.get("company")
        or manifest.get("company")
        or summary.get("params", {}).get("company")
        or summary.get("company")
        or ticker
    )

    analysis_date = (
        params.get("analysis_date")
        or manifest.get("analysis_date")
        or summary.get("params", {}).get("analysis_date")
        or summary.get("analysis_date")
    )
    if not analysis_date and (run_dir / "evidence" / "evidence.json").is_file():
        try:
            ev_raw = json.loads((run_dir / "evidence" / "evidence.json").read_text(encoding="utf-8"))
            analysis_date = ev_raw.get("analysis_date") or (
                ev_raw.get("intake_coverage", {}).get("analysis_date")
                if isinstance(ev_raw.get("intake_coverage"), dict) else None
            )
            if not analysis_date:
                doc_dates = [d.get("doc_date") for d in ev_raw.get("documents", []) if isinstance(d, dict) and d.get("doc_date")]
                if doc_dates:
                    analysis_date = max(doc_dates)
        except Exception:
            pass
    if not analysis_date:
        raise ValueError(
            "analysis_date is missing from run metadata; refusing to export research packet without a verified analysis date"
        )
    analysis_date = str(analysis_date).strip()

    run_id = str(
        manifest.get("run_id")
        or summary.get("run_id")
        or ((next(iter(envelopes.values())).metrics or {}).get("run_id", "") if envelopes else "")
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

    # Reconstruct typed facts from verified evidence snapshot, run files, and envelopes
    ledger_problems: List[str] = []
    fact_ledger = _load_fact_ledger(
        run_dir,
        envelopes,
        problems=ledger_problems,
        manifest=manifest,
        ticker=ticker if ticker != "UNKNOWN" else None,
        analysis_date=analysis_date,
    )
    if ledger_problems:
        raise ValueError(
            "Fact ledger verification failed:\n- " + "\n- ".join(ledger_problems)
        )

    facts_list: List[Any] = list(fact_ledger.values())

    # Bound facts: strip any raw text/unbounded data and map schema aliases
    bounded_facts: List[Dict[str, Any]] = []
    for f in facts_list:
        bf = _project_fact(f, default_ticker=ticker)
        if bf is not None:
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
                                "date": doc.get("filing_date") or doc.get("doc_date") or doc.get("date"),
                                "filing_date": doc.get("filing_date") or doc.get("doc_date"),
                                "form_type": doc.get("form") or doc.get("type"),
                                "accession": doc.get("accession"),
                                "checksum": doc.get("content_sha256") or doc.get("body_sha256"),
                                "verification": "retrieved",
                            }
                        )
        except Exception as exc:
            raise ValueError(f"Evidence documents in evidence.json could not be loaded: {exc}") from exc

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
            overall = scorecard.get("Overall", {})
            overall_score = overall.get("score") if isinstance(overall, dict) else scorecard.get("total_score")
            claims.append(
                {
                    "id": "scorecard_total",
                    "agent_id": "A7",
                    "category": "scorecard",
                    "statement": f"Overall score: {overall_score if overall_score is not None else 'N/A'}",
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
    # A packet leaves the run directory and is read outside it. If the run was
    # produced by the fixture provider, the packet itself must say so: the
    # exported file otherwise carries no trace of the backend that wrote it.
    configuration = manifest.get("configuration") or {}
    if str(configuration.get("provider") or summary.get("provider") or "").strip().lower() == "mock":
        limitations.append(
            "Provenance: generated by the mock provider from built-in fixtures. "
            "Every figure is synthetic; this packet is a pipeline smoke test, not research."
        )
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
        raw_missing = (env.metrics or {}).get("missing_data")
        missing_map = missing_data_map(raw_missing) if raw_missing else {}
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

    When `run_dir` is omitted, validation is structural-only: it verifies schema conformance,
    internal claim fact/calculation references, boundedness, and approval status, but does not
    verify source truth without run directory context.

    When `run_dir` is provided, facts are verified against the reconstructed fact ledger
    from verified evidence snapshots, run files, and envelopes. Any unknown, missing,
    or altered facts (including value, unit, period, availability, accession, company, concept,
    and basis) are rejected.
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
    schema_file = SCHEMA_PATH if SCHEMA_PATH.is_file() else get_schema_path("research_packet_v1.json")
    if schema_file.is_file():
        try:
            import jsonschema

            schema = json.loads(schema_file.read_text(encoding="utf-8"))
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
            if not run_dir.exists() or not run_dir.is_dir():
                errors.append(f"Run directory not found: {run_dir}")
            else:
                env_map = _load_run_envelopes(run_dir)
                manifest = _load_run_manifest(run_dir)

                a1_params: Dict[str, Any] = {}
                if "A1" in env_map and env_map["A1"].params:
                    a1_params = env_map["A1"].params or {}
                else:
                    a1_path = run_dir / "a1_envelope.json"
                    if a1_path.is_file():
                        try:
                            a1_data = json.loads(a1_path.read_text(encoding="utf-8"))
                            a1_params = a1_data.get("params") or {}
                        except Exception:
                            pass
                if a1_params.get("ticker") and pkt_ticker != a1_params.get("ticker"):
                    errors.append(
                        f"Company ticker mismatch: packet has {pkt_ticker!r}, but run A1 specifies {a1_params.get('ticker')!r}"
                    )
                if a1_params.get("company") and pkt_name != a1_params.get("company"):
                    errors.append(
                        f"Company name mismatch: packet has {pkt_name!r}, but run A1 specifies {a1_params.get('company')!r}"
                    )

                # Validate fact ledger against run_dir
                val_problems: List[str] = []
                fact_ledger = _load_fact_ledger(
                    run_dir,
                    envelopes=env_map,
                    problems=val_problems,
                    manifest=manifest,
                    ticker=pkt_ticker,
                    analysis_date=packet.get("analysis_date"),
                )
                for prob in val_problems:
                    errors.append(f"Fact ledger verification failed: {prob}")

                canonical_facts: Dict[str, Dict[str, Any]] = {}
                for f in fact_ledger.values():
                    proj = _project_fact(f, default_ticker=pkt_ticker)
                    if proj and proj.get("id"):
                        canonical_facts[str(proj["id"])] = proj

                pkt_fact_map: Dict[str, Dict[str, Any]] = {}
                for fact in packet.get("facts", []):
                    if isinstance(fact, dict) and fact.get("id"):
                        fid = str(fact["id"])
                        if fid in pkt_fact_map:
                            errors.append(
                                f"Duplicate or conflicting fact ID '{fid}' in packet facts ledger"
                            )
                        pkt_fact_map[fid] = fact

                pkt_ids = set(pkt_fact_map.keys())
                can_ids = set(canonical_facts.keys())

                for fid in sorted(pkt_ids - can_ids):
                    errors.append(
                        f"Packet contains unknown fact '{fid}' not present in reconstructed fact ledger"
                    )
                for fid in sorted(can_ids - pkt_ids):
                    errors.append(
                        f"Packet is missing fact '{fid}' present in reconstructed fact ledger"
                    )
                for fid in sorted(pkt_ids & can_ids):
                    field_diffs = _compare_projected_facts(fid, pkt_fact_map[fid], canonical_facts[fid])
                    errors.extend(field_diffs)

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
    seen_facts: Dict[str, Dict[str, Any]] = {}
    known_fact_ids: Set[str] = set()
    for fact in packet.get("facts", []):
        if not isinstance(fact, dict):
            errors.append(f"Fact entry must be a dictionary, got {type(fact).__name__}")
            continue
        fid = fact.get("id")
        if not fid:
            errors.append("Fact entry missing required 'id'")
            continue
        if fid in seen_facts:
            errors.append(
                f"Duplicate or conflicting fact ID '{fid}' in packet facts ledger"
            )
        else:
            seen_facts[fid] = fact
            known_fact_ids.add(fid)
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
                elif not _is_finite_number(f_val):
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
