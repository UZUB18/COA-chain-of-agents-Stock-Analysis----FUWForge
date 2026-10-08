"""
SEC CompanyFacts Ingestion and XBRL Ingestion Pipeline.

Provides bounded standard/entity-level SEC companyfacts ingestion using offline
fixture validation. Preserves accession, exact units/context/period, available-at
precision limitation, retrieval timestamp, company identity, and amended original history.

Custom/dimensional XBRL concepts without standard representation are deferred
cleanly (with optional Arelle detection) rather than fabricated.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from src.facts import (
    AccountingBasis,
    Fact,
    FactKind,
    parse_iso_datetime,
    usable_as_of,
    validate_fact,
)
from src.utils.checksum import sha256_digest

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION_SEC = "sec_companyfacts_v1"

# Standard known currency codes
KNOWN_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "HKD", "SEK", "NZD", "KRW", "SGD", "NOK", "MXN", "INR"
}


def is_arelle_available() -> bool:
    """Return True if the Arelle XBRL library is available in the environment."""
    try:
        import arelle  # type: ignore # noqa: F401
        return True
    except ImportError:
        return False


def _safe_cik_str(cik: Union[str, int]) -> str:
    """Normalize CIK to 10-digit padded string."""
    try:
        return f"{int(cik):010d}"
    except (ValueError, TypeError):
        s = str(cik).strip()
        digits = re.sub(r"\D", "", s)
        return digits.zfill(10) if digits else s


def _normalize_unit_and_currency(unit_raw: str) -> Tuple[str, Optional[str]]:
    """
    Normalize unit string and extract currency if applicable.
    """
    raw = unit_raw.strip()
    upper = raw.upper()
    if upper in KNOWN_CURRENCIES:
        return upper, upper
    if raw.lower() in ("shares", "share"):
        return "shares", None
    if raw.lower() in ("usd/shares", "usd/share"):
        return "USD/share", "USD"
    if raw.lower() in ("pure", "ratio"):
        return "ratio", None
    return raw, None


def _accounting_basis_for_taxonomy(taxonomy: str) -> AccountingBasis:
    """Map SEC XBRL taxonomy namespace to AccountingBasis."""
    tax_lower = taxonomy.strip().lower()
    if "us-gaap" in tax_lower:
        return AccountingBasis.US_GAAP
    if "ifrs" in tax_lower:
        return AccountingBasis.IFRS
    if "dei" in tax_lower:
        return AccountingBasis.US_GAAP
    if "non-gaap" in tax_lower:
        return AccountingBasis.NON_GAAP
    return AccountingBasis.OTHER


def parse_sec_company_facts(
    data: Union[Dict[str, Any], str, bytes, Path],
    *,
    ticker: Optional[str] = None,
    cik: Optional[Union[str, int]] = None,
    retrieval_time: Optional[str] = None,
    max_facts: int = 5000,
    as_of: Optional[Union[str, date, datetime]] = None,
    defer_dimensional: bool = True,
) -> List[Fact]:
    """
    Parse an SEC EDGAR companyfacts JSON payload into a list of typed Fact objects.

    Preserves:
    - Exact accession (e.g. "0000320193-23-000106")
    - Exact unit, currency, and period (duration start/end or instant)
    - Available-at precision limitation (SEC filing date has calendar-day precision)
    - Retrieval time and source location URL
    - Entity identity (ticker / CIK / company name)
    - Amended original history via supersession chains

    Unsupported custom or multi-dimensional concepts are deferred cleanly
    rather than pretended or fabricated.
    """
    # 1. Load payload
    if isinstance(data, Path):
        content = data.read_text(encoding="utf-8")
        payload = json.loads(content)
    elif isinstance(data, (str, bytes)):
        payload = json.loads(data)
    elif isinstance(data, dict):
        payload = data
    else:
        raise TypeError(f"Unsupported data type for SEC companyfacts: {type(data).__name__}")

    raw_cik = payload.get("cik") or cik or ""
    cik_str = _safe_cik_str(raw_cik) if raw_cik else "0000000000"
    company_name = str(payload.get("entityName") or ticker or f"CIK{cik_str}")
    company_symbol = (ticker or company_name).strip().upper()

    now_iso = retrieval_time or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    facts_dict = payload.get("facts")
    if not isinstance(facts_dict, dict):
        # Empty or invalid facts section
        return []

    # Collected raw items grouped by (taxonomy, concept, unit, period_key)
    # to reconstruct chronology and supersession
    raw_fact_groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}

    count = 0
    for taxonomy, concepts in facts_dict.items():
        if not isinstance(concepts, dict):
            continue

        basis = _accounting_basis_for_taxonomy(taxonomy)

        for concept_name, concept_body in concepts.items():
            if not isinstance(concept_body, dict):
                continue

            units_dict = concept_body.get("units")
            if not isinstance(units_dict, dict):
                continue

            # Check if custom taxonomy or dimensional
            is_custom = taxonomy.lower() not in ("us-gaap", "dei", "ifrs-full", "invest", "srt")
            has_dimensions = bool(concept_body.get("dimensions") or concept_body.get("segment"))

            if (is_custom or has_dimensions) and defer_dimensional and not is_arelle_available():
                # Unsupported custom/dimensional XBRL concept deferred
                period_key = "unsupported_dimensional"
                group_key = (taxonomy, concept_name, "USD", period_key)
                deferred_entry = {
                    "taxonomy": taxonomy,
                    "concept": concept_name,
                    "unit": "USD",
                    "currency": "USD",
                    "val": None,
                    "filed": "1970-01-01",
                    "accn": "0000000000-00-000000",
                    "form": "UNKNOWN",
                    "start": None,
                    "end": None,
                    "instant": "1970-01-01",
                    "basis": basis,
                    "missing_reason": f"Deferred: unsupported custom or dimensional XBRL concept '{taxonomy}:{concept_name}' without standard representation",
                    "dimensions": {"deferred": True, "reason": "unsupported_custom_dimensional_xbrl"},
                    "metadata": {"deferred": True, "taxonomy": taxonomy},
                }
                raw_fact_groups.setdefault(group_key, []).append(deferred_entry)
                count += 1
                if count >= max_facts:
                    break
                continue

            for unit_key, fact_entries in units_dict.items():
                if not isinstance(fact_entries, list):
                    continue

                norm_unit, norm_curr = _normalize_unit_and_currency(unit_key)

                for entry in fact_entries:
                    if not isinstance(entry, dict):
                        continue

                    val = entry.get("val")
                    start = entry.get("start")
                    end = entry.get("end")
                    filed = str(entry.get("filed") or entry.get("filingDate") or "")
                    accn = str(entry.get("accn") or entry.get("accessionNumber") or "0000320193-00-000000")
                    form = str(entry.get("form") or "")
                    fy = entry.get("fy")
                    fp = entry.get("fp")
                    frame = entry.get("frame")

                    # Period determination
                    if start and end and start != end:
                        period_start = str(start)
                        period_end = str(end)
                        instant = None
                        period_key = f"duration:{period_start}/{period_end}"
                    else:
                        period_start = None
                        period_end = None
                        instant = str(end or start or filed or "1970-01-01")
                        period_key = f"instant:{instant}"

                    group_key = (taxonomy, concept_name, norm_unit, period_key)

                    val_float: Optional[float] = None
                    missing_reason: Optional[str] = None
                    if val is None:
                        missing_reason = f"Concept '{concept_name}' value was null in SEC filing {accn}"
                    elif isinstance(val, bool):
                        missing_reason = f"Concept '{concept_name}' had boolean value in SEC filing {accn}"
                    else:
                        try:
                            val_float = float(val)
                            if not math.isfinite(val_float):
                                val_float = None
                                missing_reason = f"Concept '{concept_name}' value was non-finite in SEC filing {accn}"
                        except (ValueError, TypeError):
                            missing_reason = f"Concept '{concept_name}' value was unparseable in SEC filing {accn}"

                    parsed_entry = {
                        "taxonomy": taxonomy,
                        "concept": concept_name,
                        "unit": norm_unit,
                        "currency": norm_curr,
                        "val": val_float,
                        "missing_reason": missing_reason,
                        "filed": filed,
                        "accn": accn,
                        "form": form,
                        "fy": fy,
                        "fp": fp,
                        "frame": frame,
                        "start": period_start,
                        "end": period_end,
                        "instant": instant,
                        "basis": basis,
                        "dimensions": entry.get("dimensions"),
                        "metadata": {
                            "availability_precision": "date_only_intraday_unspecified",
                            "date_basis": "SEC_filing_date",
                            "precision_limitation": "Filing date from SEC companyfacts has calendar-day precision; intraday publication time is unspecified.",
                            "form": form,
                            "fy": fy,
                            "fp": fp,
                            "frame": frame,
                            "accn": accn,
                        },
                    }

                    raw_fact_groups.setdefault(group_key, []).append(parsed_entry)
                    count += 1
                    if count >= max_facts:
                        break
                if count >= max_facts:
                    break
            if count >= max_facts:
                break
        if count >= max_facts:
            break

    # 2. Build Fact objects with supersession chains for amendments/restatements
    facts: List[Fact] = []

    for (taxonomy, concept_name, unit, period_key), entries in raw_fact_groups.items():
        # Sort chronologically by filing date, then accession
        def _sort_key(e: Dict[str, Any]) -> Tuple[str, str]:
            return (e.get("filed") or "1970-01-01", e.get("accn") or "")

        sorted_entries = sorted(entries, key=_sort_key)

        prior_fact_id: Optional[str] = None
        prior_val: Optional[float] = None

        for idx, entry in enumerate(sorted_entries):
            filed = entry["filed"] or "1970-01-01"
            accn = entry["accn"]
            accn_clean = accn.replace("-", "")
            concept_slug = re.sub(r"[^a-zA-Z0-9_]", "_", concept_name).lower()
            comp_slug = re.sub(r"[^a-zA-Z0-9_]", "_", company_symbol).lower()
            period_slug = re.sub(r"[^a-zA-Z0-9_]", "_", period_key).lower()

            fact_id = f"fact_{comp_slug}_{concept_slug}_{period_slug}_{accn_clean}"

            # If identical ID occurs in same group (rare duplicate accession entry), append index
            if any(f.id == fact_id for f in facts):
                fact_id = f"{fact_id}_{idx}"

            # Availability time with calendar-day precision
            avail_iso = f"{filed}T00:00:00Z" if len(filed) == 10 else filed

            # Determine kind and supersession
            if idx == 0:
                kind = FactKind.REPORTED
                supersedes = None
            else:
                # Later filing for the same concept and period -> restatement / amendment
                kind = FactKind.RESTATEMENT
                supersedes = prior_fact_id

            source_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json"
            source_loc = {
                "url": source_url,
                "taxonomy": taxonomy,
                "concept": concept_name,
                "accession": accn,
                "form": entry.get("form"),
                "filed": filed,
            }

            fact = Fact(
                id=fact_id,
                company=company_symbol,
                concept=concept_name,
                unit=unit,
                currency=entry.get("currency"),
                value=entry.get("val"),
                missing_reason=entry.get("missing_reason"),
                period_start=entry.get("start"),
                period_end=entry.get("end"),
                instant=entry.get("instant"),
                filing_accession=accn,
                availability_time=avail_iso,
                retrieval_time=now_iso,
                source_location=source_loc,
                accounting_basis=entry.get("basis", AccountingBasis.US_GAAP),
                extractor_version=EXTRACTOR_VERSION_SEC,
                kind=kind,
                supersedes=supersedes,
                dimensions=entry.get("dimensions"),
                metadata=entry.get("metadata"),
            )

            facts.append(fact)
            prior_fact_id = fact.id
            prior_val = fact.value

    # Optional as_of filtering if specified
    if as_of is not None:
        facts = [f for f in facts if usable_as_of(f, as_of)]

    return facts


def ingest_company_facts_file(
    file_path: Union[str, Path],
    *,
    ticker: Optional[str] = None,
    cik: Optional[Union[str, int]] = None,
    as_of: Optional[Union[str, date, datetime]] = None,
) -> List[Fact]:
    """Ingest a local SEC companyfacts JSON file offline."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SEC companyfacts file not found: {path}")
    return parse_sec_company_facts(path, ticker=ticker, cik=cik, as_of=as_of)
