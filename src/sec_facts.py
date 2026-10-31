"""
SEC CompanyFacts Ingestion and XBRL Ingestion Pipeline.

Provides bounded standard/entity-level SEC companyfacts ingestion using offline
fixture validation. Preserves accession, exact units/context/period, available-at
precision limitation, retrieval timestamp, company identity, and amended original history.

Custom/dimensional XBRL concepts without standard representation are deferred
cleanly (with optional Arelle detection) rather than fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

#: Concepts that must survive intake truncation, in descending priority.
#:
#: CompanyFacts payloads iterate alphabetically, so a plain "consume records
#: until the cap" loop spends its whole budget inside the ``A``–``D`` concepts
#: (``DeferredIncomeTax*`` alone is thousands of records) and never reaches
#: ``Revenues``, ``NetIncomeLoss``, ``NetCashProvidedByUsedInOperatingActivities``
#: or ``SellingGeneralAndAdministrativeExpense``. Those concepts then have zero
#: ledger entries, so no downstream prompt selection can recover them and every
#: forensic index that needs them is silently unavailable.
#:
#: Ordering here is intake priority only. It is not a claim that these concepts
#: are the right economic inputs; downstream binding still decides that.
PRIORITY_CONCEPTS: Tuple[str, ...] = (
    # Income statement — revenue, margin, earnings, tax.
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "CostOfGoodsAndServicesSold",
    "CostOfRevenue",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "ProfitLoss",
    "ResearchAndDevelopmentExpense",
    "SellingGeneralAndAdministrativeExpense",
    "IncomeTaxExpenseBenefit",
    # Balance sheet — leverage, accruals, receivables, equity.
    "Assets",
    "AssetsCurrent",
    "Liabilities",
    "LiabilitiesCurrent",
    "LiabilitiesAndStockholdersEquity",
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "RetainedEarningsAccumulatedDeficit",
    "AccountsReceivableNetCurrent",
    "AccountsReceivableNet",
    "ReceivablesNetCurrent",
    "AccountsReceivableGrossCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "PropertyPlantAndEquipmentNet",
    "PropertyPlantAndEquipmentGross",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    # Cash flow — operating cash, capex, depreciation and amortization.
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "NetCashProvidedByUsedInInvestingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "Depreciation",
    # Share counts and per-share amounts used by valuation checks.
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "EarningsPerShareDiluted",
)

_PRIORITY_INDEX = {concept: index for index, concept in enumerate(PRIORITY_CONCEPTS)}

#: Taxonomy namespaces whose concepts are standard enough to ingest in full.
STANDARD_TAXONOMIES = ("us-gaap", "dei", "ifrs-full", "invest", "srt")


def concept_priority(concept: str) -> int:
    """Return the intake priority of ``concept`` (lower is kept first)."""
    return _PRIORITY_INDEX.get(str(concept), len(_PRIORITY_INDEX))


def _entry_recency_key(entry: Dict[str, Any]) -> Tuple[str, str]:
    """Descending sort key: newest availability, then newest accession."""
    return (
        str(entry.get("availability_time") or entry.get("filed") or ""),
        str(entry.get("accn") or ""),
    )


@dataclass(frozen=True)
class SealIntakeCoverage:
    """What a bounded CompanyFacts intake actually retained.

    ``fact_count`` alone is not a coverage statement. A payload can yield the
    full record budget and still contain none of the concepts a forensic input
    needs, which is exactly what an alphabetically truncated intake produced.
    This records the retained and dropped concept sets so the difference between
    "the issuer did not tag it" and "our budget discarded it" is never guessed.
    """

    availability_policy: str
    fact_limit: int
    truncated: bool
    records_seen: int
    records_retained: int
    concepts_seen: int
    concepts_retained: int
    concepts_dropped_by_limit: Tuple[str, ...] = ()
    priority_concepts_seen: Tuple[str, ...] = ()
    priority_concepts_missing_from_payload: Tuple[str, ...] = ()
    priority_concepts_dropped_by_limit: Tuple[str, ...] = ()
    #: Custom/dimensional concepts automatic intake does not represent. They
    #: are listed here, never emitted as placeholder facts.
    deferred_concepts: Tuple[str, ...] = ()
    records_excluded_missing_filing_date: int = 0
    records_excluded_missing_accession: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "availability_policy": self.availability_policy,
            "fact_limit": self.fact_limit,
            "truncated": self.truncated,
            "records_seen": self.records_seen,
            "records_retained": self.records_retained,
            "concepts_seen": self.concepts_seen,
            "concepts_retained": self.concepts_retained,
            "concepts_dropped_by_limit": list(self.concepts_dropped_by_limit),
            "priority_concepts_seen": list(self.priority_concepts_seen),
            "priority_concepts_missing_from_payload": list(
                self.priority_concepts_missing_from_payload
            ),
            "priority_concepts_dropped_by_limit": list(
                self.priority_concepts_dropped_by_limit
            ),
            "deferred_concepts": list(self.deferred_concepts),
            "records_excluded_missing_filing_date": self.records_excluded_missing_filing_date,
            "records_excluded_missing_accession": self.records_excluded_missing_accession,
        }


@dataclass(frozen=True)
class FactIntakeReport:
    """Typed facts plus the intake coverage that produced them."""

    facts: List[Fact]
    coverage: SealIntakeCoverage

    def __iter__(self):
        return iter(self.facts)

    def __len__(self) -> int:
        return len(self.facts)

    def __getitem__(self, index):
        return self.facts[index]

#: Point-in-time availability policy for SEC-sourced facts.
#:
#: The SEC ``filed`` field carries a calendar date with **no intraday
#: publication time**. A filing is accepted at an unknown moment on that date;
#: EDGAR may even accept it after the close of business. Therefore:
#:
#: - A fact is treated as available at the **end of its filed date**
#:   (``23:59:59Z``), the latest moment the evidence supports, never at
#:   ``00:00:00Z``.
#: - We never claim a fact was public earlier than the filing date itself.
#: - A fact whose filing date equals the research cutoff day is therefore only
#:   usable by a cutoff at or after the end of that day, which is the
#:   conservative, defensible choice.
#:
#: This is deliberately conservative: it can exclude a fact that was in fact
#: public intraday, but it can never include one that was not yet public.
AVAILABILITY_POLICY = "sec-filed-date-end-of-day-v1"


def _conservative_availability(filed: str) -> Optional[str]:
    """Return the end of the valid filing day, or ``None`` if unknown.

    ``YYYY-MM-DD`` -> ``YYYY-MM-DDT23:59:59Z``. Timestamp fixtures are
    normalised to UTC before choosing their day, so an offset cannot make a
    filing appear available before its actual timestamp. Naive timestamps use
    UTC, consistent with the fact-ledger timestamp policy.

    Unknown dates must not become a historical sentinel: an epoch timestamp
    would pass every modern ``availability <= cutoff`` check. The parser
    excludes these observations instead of manufacturing an availability date.
    """
    raw = str(filed or "").strip()
    if not raw:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            day = date.fromisoformat(raw)
        else:
            # A valid date prefix followed by garbage is not a valid date.
            if not re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", raw):
                return None
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            day = stamp.astimezone(timezone.utc).date()
    except (ValueError, OverflowError):
        return None
    return f"{day.isoformat()}T23:59:59Z"


def _values_equal(a: Optional[float], b: Optional[float]) -> bool:
    """Exact float equality for disclosure comparison (no tolerance)."""
    if a is None or b is None:
        return a is None and b is None
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False

# Standard known currency codes
KNOWN_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "HKD", "SEK", "NZD", "KRW", "SGD", "NOK", "MXN", "INR"
}


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


def parse_sec_company_facts_report(
    data: Union[Dict[str, Any], str, bytes, Path],
    *,
    ticker: Optional[str] = None,
    cik: Optional[Union[str, int]] = None,
    retrieval_time: Optional[str] = None,
    max_facts: Optional[int] = 50000,
    as_of: Optional[Union[str, date, datetime]] = None,
    defer_dimensional: bool = True,
) -> "FactIntakeReport":
    """
    Parse an SEC EDGAR companyfacts JSON payload into typed facts and a coverage report.

    Preserves:
    - Exact accession (e.g. "0000320193-23-000106")
    - Exact unit, currency, and period (duration start/end or instant)
    - Available-at precision limitation (SEC filing date has calendar-day precision)
    - Retrieval time and source location URL
    - Entity identity (ticker / CIK / company name)
    - Amended original history via supersession chains

    Unsupported custom or multi-dimensional concepts are deferred cleanly
    rather than pretended or fabricated.

    ``max_facts`` bounds how many raw observations become facts. The bound is
    applied **after** the whole payload is seen and in :data:`PRIORITY_CONCEPTS`
    order, so intake cannot lose a core statement concept to an unrelated
    concept that merely sorts earlier in the JSON. ``max_facts=None`` ingests
    every observation.

    The returned :class:`FactIntakeReport` states what was retained, what the
    payload genuinely did not tag, and what the bound discarded. Those three are
    different facts about a run and must never be reported as one number.
    """
    if max_facts is not None:
        if isinstance(max_facts, bool) or not isinstance(max_facts, int):
            raise TypeError("max_facts must be an int or None")
        if max_facts <= 0:
            raise ValueError("max_facts must be a positive record budget")
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
        # Empty or invalid facts section: an explicitly empty report, never a
        # silent "no facts were reported by the issuer" conclusion.
        return FactIntakeReport(
            facts=[],
            coverage=SealIntakeCoverage(
                availability_policy=AVAILABILITY_POLICY,
                fact_limit=int(max_facts) if max_facts is not None else 0,
                truncated=False,
                records_seen=0,
                records_retained=0,
                concepts_seen=0,
                concepts_retained=0,
                priority_concepts_missing_from_payload=PRIORITY_CONCEPTS,
            ),
        )

    # Collected raw items grouped by (taxonomy, concept, unit, period_key)
    # to reconstruct chronology and supersession
    raw_fact_groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}

    count = 0
    deferred_concepts: set = set()
    excluded_missing_filing_date = 0
    excluded_missing_accession = 0
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

            if (is_custom or has_dimensions) and defer_dimensional:
                # Automatic intake has no faithful representation for these.
                # Record the gap in coverage; a dated placeholder fact would be
                # "available" at every cutoff and could fill prompt slots.
                deferred_concepts.add(f"{taxonomy}:{concept_name}")
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
                    availability = _conservative_availability(filed)
                    if availability is None:
                        logger.warning(
                            "Excluded SEC observation with unknown or malformed filing date: %s:%s",
                            taxonomy, concept_name,
                        )
                        excluded_missing_filing_date += 1
                        continue
                    accn = str(entry.get("accn") or entry.get("accessionNumber") or "").strip()
                    if not accn:
                        # Never borrow an accession: filing identity is evidence.
                        logger.warning(
                            "Excluded SEC observation with missing accession number: %s:%s",
                            taxonomy, concept_name,
                        )
                        excluded_missing_accession += 1
                        continue
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
                        "availability_time": availability,
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

    # 1b. Bounded intake, decided by concept priority rather than payload order.
    #
    # The limit is applied *after* every record has been seen, so a concept that
    # is important but late in the alphabet is never lost to an earlier concept's
    # long history. Retention is round-robin across concept/unit/period groups
    # ordered by priority, so the budget buys breadth of concepts first and depth
    # of history second; within a group the most recent entries are kept, which
    # preserves the current period and its supersession chain.
    records_seen = count
    concepts_seen = sorted({key[1] for key in raw_fact_groups})
    truncated = False
    records_retained = records_seen
    if max_facts is not None and records_seen > max_facts:
        truncated = True
        ordered_keys = sorted(
            raw_fact_groups,
            key=lambda key: (concept_priority(key[1]), key[1], key[0], key[2], key[3]),
        )
        queues: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {
            key: sorted(raw_fact_groups[key], key=_entry_recency_key, reverse=True)
            for key in ordered_keys
        }
        positions = dict.fromkeys(ordered_keys, 0)
        bounded: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {
            key: [] for key in ordered_keys
        }
        retained = 0
        while retained < max_facts:
            progressed = False
            for key in ordered_keys:
                if retained >= max_facts:
                    break
                position = positions[key]
                if position >= len(queues[key]):
                    continue
                bounded[key].append(queues[key][position])
                positions[key] = position + 1
                retained += 1
                progressed = True
            if not progressed:
                break
        raw_fact_groups = {key: entries for key, entries in bounded.items() if entries}
        records_retained = retained

    concepts_retained = sorted({key[1] for key in raw_fact_groups})
    coverage = SealIntakeCoverage(
        availability_policy=AVAILABILITY_POLICY,
        fact_limit=int(max_facts) if max_facts is not None else records_seen,
        truncated=truncated,
        records_seen=records_seen,
        records_retained=records_retained,
        concepts_seen=len(concepts_seen),
        concepts_retained=len(concepts_retained),
        concepts_dropped_by_limit=tuple(sorted(set(concepts_seen) - set(concepts_retained))),
        priority_concepts_seen=tuple(
            concept for concept in PRIORITY_CONCEPTS if concept in set(concepts_seen)
        ),
        priority_concepts_missing_from_payload=tuple(
            concept for concept in PRIORITY_CONCEPTS if concept not in set(concepts_seen)
        ),
        priority_concepts_dropped_by_limit=tuple(
            concept
            for concept in PRIORITY_CONCEPTS
            if concept in set(concepts_seen) and concept not in set(concepts_retained)
        ),
        deferred_concepts=tuple(sorted(deferred_concepts)),
        records_excluded_missing_filing_date=excluded_missing_filing_date,
        records_excluded_missing_accession=excluded_missing_accession,
    )

    # 2. Build Fact objects with supersession chains for amendments/restatements
    facts: List[Fact] = []
    emitted_ids: Set[str] = set()

    for (taxonomy, concept_name, unit, period_key), entries in raw_fact_groups.items():
        # Sort chronologically by filing date, then accession
        def _sort_key(e: Dict[str, Any]) -> Tuple[str, str]:
            # Compare UTC availability, not lexical timestamps with offsets.
            return (e.get("availability_time") or _conservative_availability(e["filed"]) or "", e.get("accn") or "")

        sorted_entries = sorted(entries, key=_sort_key)

        prior_fact_id: Optional[str] = None
        prior_val: Optional[float] = None
        prior_unit: Optional[str] = None
        prior_basis: Optional[AccountingBasis] = None

        for idx, entry in enumerate(sorted_entries):
            filed = entry["filed"]
            accn = entry["accn"]
            accn_clean = accn.replace("-", "")
            concept_slug = re.sub(r"[^a-zA-Z0-9_]", "_", concept_name).lower()
            comp_slug = re.sub(r"[^a-zA-Z0-9_]", "_", company_symbol).lower()
            period_slug = re.sub(r"[^a-zA-Z0-9_]", "_", period_key).lower()

            fact_id = f"fact_{comp_slug}_{concept_slug}_{period_slug}_{accn_clean}"

            # If identical ID occurs in same group (rare duplicate accession
            # entry), append an index. Membership is tracked in a set: a linear
            # scan here made intake quadratic, which is what forced a small
            # record budget in the first place.
            if fact_id in emitted_ids:
                fact_id = f"{fact_id}_{idx}"

            # Conservative availability: the SEC 'filed' field is a calendar date
            # with no intraday publication time. We therefore treat the fact as
            # available at the END of the filed day, not its start. This never
            # claims the fact was public earlier than the evidence supports
            # (a filing accepted after the close of business is still only
            # knowable from the filing date onward, not from 00:00).
            avail_iso = entry.get("availability_time") or _conservative_availability(filed)
            if avail_iso is None:
                # Defensive guard: no constructed Fact may invent a timestamp.
                continue

            entry_value = entry.get("val")
            entry_unit = entry.get("unit")
            entry_basis = entry.get("basis", AccountingBasis.US_GAAP)

            # Classification of this observation relative to the previous one.
            #
            # Not every later disclosure of the same period is a restatement.
            # The four distinct cases are:
            #
            # - first disclosure                      -> reported
            # - later filing, SAME value              -> repeated_disclosure
            # - later filing, DIFFERENT value         -> restatement
            # - later filing on an /A amendment form  -> amended
            #
            # A repeated disclosure does not supersede the original: the original
            # was never wrong, so discarding it would erase the original
            # provenance. It is recorded as its own observation.
            form = str(entry.get("form") or "").upper()
            is_amendment_form = form.endswith("/A")
            is_same_value = (
                prior_val is not None
                and entry_value is not None
                and _values_equal(prior_val, entry_value)
            )
            is_same_unit = (prior_unit is None or prior_unit == entry_unit)
            is_same_basis = (prior_basis is None or prior_basis == entry_basis)

            if idx == 0:
                kind = FactKind.REPORTED
                supersedes = None
                disclosure_relation = "initial_disclosure"
            elif is_amendment_form:
                kind = FactKind.RESTATEMENT
                supersedes = prior_fact_id
                disclosure_relation = "amended"
            elif is_same_value and is_same_unit and is_same_basis:
                # Same number re-reported in a later filing (e.g. a comparative
                # column restated to an unchanged figure, or the same fact
                # re-tagged). This is a repeat, not a restatement.
                kind = FactKind.REPORTED
                supersedes = None
                disclosure_relation = "repeated_disclosure"
            elif (
                prior_val is not None
                and entry_value is not None
                and not _values_equal(prior_val, entry_value)
            ):
                kind = FactKind.RESTATEMENT
                supersedes = prior_fact_id
                disclosure_relation = "restated"
            else:
                # A later observation that is not a same-value repeat but whose
                # value is not directly comparable (null, or unit/basis changed)
                # is treated as superseding rather than silently coexisting.
                kind = FactKind.RESTATEMENT
                supersedes = prior_fact_id
                disclosure_relation = "superseded_incomparable"

            source_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json"
            source_loc = {
                "url": source_url,
                "taxonomy": taxonomy,
                "concept": concept_name,
                "accession": accn,
                "form": entry.get("form"),
                "filed": filed,
            }

            metadata = dict(entry.get("metadata") or {})
            metadata.update(
                {
                    "disclosure_relation": disclosure_relation,
                    "prior_fact_id": prior_fact_id,
                    "prior_value": prior_val,
                    "availability_policy": AVAILABILITY_POLICY,
                }
            )

            fact = Fact(
                id=fact_id,
                company=company_symbol,
                concept=concept_name,
                unit=unit,
                currency=entry.get("currency"),
                value=entry_value,
                missing_reason=entry.get("missing_reason"),
                period_start=entry.get("start"),
                period_end=entry.get("end"),
                instant=entry.get("instant"),
                filing_accession=accn,
                availability_time=avail_iso,
                retrieval_time=now_iso,
                source_location=source_loc,
                accounting_basis=entry_basis,
                extractor_version=EXTRACTOR_VERSION_SEC,
                kind=kind,
                supersedes=supersedes,
                dimensions=entry.get("dimensions"),
                metadata=metadata,
            )

            facts.append(fact)
            emitted_ids.add(fact.id)
            prior_fact_id = fact.id
            prior_val = fact.value
            prior_unit = unit
            prior_basis = entry_basis

    # Optional as_of filtering if specified
    if as_of is not None:
        facts = [f for f in facts if usable_as_of(f, as_of)]

    return FactIntakeReport(facts=facts, coverage=coverage)


def parse_sec_company_facts(
    data: Union[Dict[str, Any], str, bytes, Path],
    *,
    ticker: Optional[str] = None,
    cik: Optional[Union[str, int]] = None,
    retrieval_time: Optional[str] = None,
    max_facts: Optional[int] = 50000,
    as_of: Optional[Union[str, date, datetime]] = None,
    defer_dimensional: bool = True,
) -> List[Fact]:
    """Parse CompanyFacts into typed facts (list-shaped convenience wrapper).

    Coverage is available from :func:`parse_sec_company_facts_report`, which
    this delegates to; use that when the difference between "not tagged by the
    issuer" and "discarded by our record budget" matters.
    """
    return parse_sec_company_facts_report(
        data,
        ticker=ticker,
        cik=cik,
        retrieval_time=retrieval_time,
        max_facts=max_facts,
        as_of=as_of,
        defer_dimensional=defer_dimensional,
    ).facts


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
