"""
Typed financial facts and filing chronology ledger.

Provides structured, dated, typed financial facts with anti-look-ahead guards,
restatement / supersession tracking, unit consistency checks, and schema validation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from src.resources import get_schema_path
from src.validators import ValidationResult

try:
    from jsonschema import Draft7Validator  # type: ignore
except ImportError:  # pragma: no cover
    Draft7Validator = None  # type: ignore


SCHEMA_PATH = get_schema_path("financial_fact_v1.json")


class FactKind(str, Enum):
    """Classification of the fact record."""

    REPORTED = "reported"
    FORECAST = "forecast"
    RESTATEMENT = "restatement"
    DERIVED = "derived"
    ESTIMATE = "estimate"


class AccountingBasis(str, Enum):
    """Accounting standards basis."""

    US_GAAP = "US-GAAP"
    IFRS = "IFRS"
    NON_GAAP = "NON-GAAP"
    OTHER = "OTHER"
    TAX = "TAX"
    CASH = "CASH"


# Standard concept expected units registry for sanity and scale checks
STANDARD_CONCEPT_UNITS: Dict[str, str] = {
    "Revenues": "USD",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "USD",
    "SalesRevenueNet": "USD",
    "NetIncomeLoss": "USD",
    "GrossProfit": "USD",
    "OperatingIncomeLoss": "USD",
    "OperatingExpenses": "USD",
    "ResearchAndDevelopmentExpense": "USD",
    "Assets": "USD",
    "Liabilities": "USD",
    "StockholdersEquity": "USD",
    "CashAndCashEquivalentsAtCarryingValue": "USD",
    "OperatingCashFlow": "USD",
    "NetCashProvidedByUsedInOperatingActivities": "USD",
    "CapitalExpenditures": "USD",
    "PaymentsToAcquirePropertyPlantAndEquipment": "USD",
    "FreeCashFlow": "USD",
    "CommonStockSharesOutstanding": "shares",
    "CommonStockSharesIssued": "shares",
    "EntityCommonStockSharesOutstanding": "shares",
    "WeightedAverageNumberOfSharesOutstandingBasic": "shares",
    "WeightedAverageNumberOfDilutedSharesOutstanding": "shares",
    "EarningsPerShareBasic": "USD/share",
    "EarningsPerShareDiluted": "USD/share",
}


def parse_iso_datetime(value: Union[str, datetime, date]) -> datetime:
    """
    Parse an ISO date or datetime string into a UTC timezone-aware datetime.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 0, 0, 0, tzinfo=timezone.utc)
    if not isinstance(value, str):
        raise TypeError(f"Expected datetime, date, or str, got {type(value).__name__}")

    raw = value.strip()
    if not raw:
        raise ValueError("Empty datetime string")

    # Handle trailing Z
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # Try YYYY-MM-DD format
        try:
            d = date.fromisoformat(raw)
            dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
        except ValueError as err:
            raise ValueError(f"Invalid ISO datetime string: {value}") from err

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


@dataclass(frozen=True)
class Fact:
    """
    Typed financial fact record.
    """

    id: str
    company: str
    concept: str
    unit: str
    filing_accession: str
    availability_time: str
    retrieval_time: str
    source_location: Union[str, Dict[str, Any]]
    accounting_basis: Union[AccountingBasis, str]
    extractor_version: str
    kind: Union[FactKind, str] = FactKind.REPORTED
    value: Optional[float] = None
    currency: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    instant: Optional[str] = None
    supersedes: Optional[str] = None
    missing_reason: Optional[str] = None
    dimensions: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    schema_version: str = "financial_fact_v1"

    def is_unavailable(self) -> bool:
        """Return True if this fact represents an explicit unavailable/missing input."""
        return self.value is None

    def is_available(self) -> bool:
        """Return True if a concrete numeric value is available."""
        return self.value is not None

    def is_reported(self) -> bool:
        """Return True if this is a reported financial fact (including restatements)."""
        k = str(self.kind).lower()
        if isinstance(self.kind, FactKind):
            k = self.kind.value
        return k in (FactKind.REPORTED.value, FactKind.RESTATEMENT.value)

    def is_forecast(self) -> bool:
        """Return True if this fact represents forward-looking guidance or forecast."""
        k = str(self.kind).lower()
        if isinstance(self.kind, FactKind):
            k = self.kind.value
        return k in (FactKind.FORECAST.value, FactKind.ESTIMATE.value)

    def period_key(self) -> str:
        """Standardized string identifier for the economic period."""
        if self.instant:
            return f"instant:{self.instant}"
        if self.period_start and self.period_end:
            return f"duration:{self.period_start}/{self.period_end}"
        return "unknown_period"

    def dimensions_key(self) -> str:
        """Standardized string identifier for dimensions."""
        if not self.dimensions:
            return ""
        items = sorted((str(k), str(v)) for k, v in self.dimensions.items())
        return json.dumps(items, sort_keys=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert fact to a JSON-serializable dictionary."""
        d = asdict(self)
        if isinstance(self.kind, FactKind):
            d["kind"] = self.kind.value
        if isinstance(self.accounting_basis, AccountingBasis):
            d["accounting_basis"] = self.accounting_basis.value
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Fact:
        """Construct Fact from dictionary."""
        d = dict(data)
        # Normalize kind
        kind_val = d.get("kind", FactKind.REPORTED)
        if isinstance(kind_val, str):
            try:
                kind_val = FactKind(kind_val.lower())
            except ValueError:
                kind_val = kind_val.lower()

        # Normalize accounting basis
        basis_val = d.get("accounting_basis", AccountingBasis.US_GAAP)
        if isinstance(basis_val, str):
            normalized = basis_val.upper().replace("_", "-")
            try:
                basis_val = AccountingBasis(normalized)
            except ValueError:
                basis_val = basis_val

        # Handle numeric value (explicit None must stay None, not 0)
        val = d.get("value")
        if val is not None:
            if isinstance(val, bool):
                raise TypeError("Fact value cannot be a boolean")
            val = float(val)

        return cls(
            id=str(d.get("id", "")),
            company=str(d.get("company", "")),
            concept=str(d.get("concept", "")),
            unit=str(d.get("unit", "")),
            filing_accession=str(d.get("filing_accession", "")),
            availability_time=str(d.get("availability_time", "")),
            retrieval_time=str(d.get("retrieval_time", "")),
            source_location=d.get("source_location", ""),
            accounting_basis=basis_val,
            extractor_version=str(d.get("extractor_version", "")),
            kind=kind_val,
            value=val,
            currency=d.get("currency"),
            period_start=d.get("period_start"),
            period_end=d.get("period_end"),
            instant=d.get("instant"),
            supersedes=d.get("supersedes"),
            missing_reason=d.get("missing_reason"),
            dimensions=d.get("dimensions"),
            metadata=d.get("metadata"),
            schema_version=d.get("schema_version", "financial_fact_v1"),
        )


def usable_as_of(fact: Union[Fact, Mapping[str, Any]], cutoff: Union[str, datetime, date]) -> bool:
    """
    Chronology guard: returns True if and only if the fact became publicly available
    on or before the cutoff timestamp (anti-look-ahead).

    A fact whose filing availability / publication time is after the cutoff must NOT
    be considered usable merely because its economic period is earlier.
    """
    avail_str = fact.availability_time if isinstance(fact, Fact) else fact.get("availability_time")
    if not avail_str:
        return False

    try:
        fact_avail_dt = parse_iso_datetime(avail_str)
        cutoff_dt = parse_iso_datetime(cutoff)
    except (ValueError, TypeError):
        return False

    return fact_avail_dt <= cutoff_dt


def validate_fact(
    data: Union[Fact, Mapping[str, Any]], schema_path: Optional[Path] = None
) -> ValidationResult:
    """
    Validate fact dictionary or instance against schema and semantic requirements.
    """
    fact_dict = data.to_dict() if isinstance(data, Fact) else dict(data)
    errors: List[str] = []

    # 1. JSON Schema validation
    active_schema_path = schema_path or (
        SCHEMA_PATH if (isinstance(SCHEMA_PATH, Path) and SCHEMA_PATH.is_file())
        else get_schema_path("financial_fact_v1.json")
    )
    if Draft7Validator is not None and active_schema_path.exists():
        try:
            schema = json.loads(active_schema_path.read_text(encoding="utf-8"))
            validator = Draft7Validator(schema)
            for err in validator.iter_errors(fact_dict):
                path_str = "/".join(map(str, err.path))
                errors.append(f"schema violation at '{path_str}': {err.message}")
        except Exception as e:
            errors.append(f"schema validator error: {e}")

    # 2. Semantic validations
    req_str_fields = [
        ("id", fact_dict.get("id")),
        ("company", fact_dict.get("company")),
        ("concept", fact_dict.get("concept")),
        ("unit", fact_dict.get("unit")),
        ("filing_accession", fact_dict.get("filing_accession")),
        ("extractor_version", fact_dict.get("extractor_version")),
    ]
    for field_name, field_val in req_str_fields:
        if not field_val or not isinstance(field_val, str) or not field_val.strip():
            errors.append(f"Field '{field_name}' must be a non-empty string")

    # Value & Missing reason
    val = fact_dict.get("value")
    if val is None:
        missing_reason = fact_dict.get("missing_reason")
        if not missing_reason or not isinstance(missing_reason, str) or not missing_reason.strip():
            errors.append("When 'value' is None, 'missing_reason' must be a non-empty string")
    else:
        if isinstance(val, bool):
            errors.append("Field 'value' cannot be a boolean")
        elif not isinstance(val, (int, float)):
            errors.append(f"Field 'value' must be a numeric float or int, got {type(val).__name__}")
        elif math.isnan(val) or math.isinf(val):
            errors.append("Field 'value' must be finite (not NaN or Infinity)")

    # Period definition: either (period_start & period_end) or instant
    start = fact_dict.get("period_start")
    end = fact_dict.get("period_end")
    instant = fact_dict.get("instant")

    has_duration = bool(start and end)
    has_instant = bool(instant)

    if not has_duration and not has_instant:
        errors.append("Fact must specify either ('period_start' and 'period_end') or 'instant'")
    elif has_duration and has_instant:
        errors.append("Fact cannot specify both duration ('period_start'/'period_end') and 'instant'")
    elif has_duration:
        if (start and not end) or (end and not start):
            errors.append("Duration facts require both 'period_start' and 'period_end'")
        else:
            try:
                s_dt = parse_iso_datetime(start)
                e_dt = parse_iso_datetime(end)
                if s_dt > e_dt:
                    errors.append(f"'period_start' ({start}) cannot be after 'period_end' ({end})")
            except (ValueError, TypeError) as err:
                errors.append(f"Invalid duration period dates: {err}")
    elif has_instant:
        try:
            parse_iso_datetime(instant)
        except (ValueError, TypeError) as err:
            errors.append(f"Invalid 'instant' date: {err}")

    # Timestamps
    for ts_field in ("availability_time", "retrieval_time"):
        ts_val = fact_dict.get(ts_field)
        if not ts_val or not isinstance(ts_val, str):
            errors.append(f"Field '{ts_field}' must be a valid ISO datetime string")
        else:
            try:
                parse_iso_datetime(ts_val)
            except (ValueError, TypeError) as err:
                errors.append(f"Invalid ISO datetime for '{ts_field}': {err}")

    # Kind
    kind_val = fact_dict.get("kind")
    valid_kinds = {k.value for k in FactKind}
    if str(kind_val).lower() not in valid_kinds:
        errors.append(f"Invalid 'kind': '{kind_val}'. Must be one of {sorted(valid_kinds)}")

    # Accounting basis
    basis_val = fact_dict.get("accounting_basis")
    valid_bases = {b.value for b in AccountingBasis}
    normalized_basis = str(basis_val).upper().replace("_", "-")
    if normalized_basis not in valid_bases:
        errors.append(f"Invalid 'accounting_basis': '{basis_val}'. Must be one of {sorted(valid_bases)}")

    # Source location
    src_loc = fact_dict.get("source_location")
    if not src_loc:
        errors.append("Field 'source_location' must be provided")
    elif isinstance(src_loc, dict):
        if not src_loc.get("url"):
            errors.append("Dictionary 'source_location' must contain 'url'")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_fact_dict(data: Mapping[str, Any]) -> List[str]:
    """Validate a fact dict and return list of error strings."""
    return validate_fact(data).errors


def flag_unit_mismatch(fact: Union[Fact, Mapping[str, Any]], expected_unit: str) -> List[str]:
    """
    Check if a fact's unit matches the expected unit.
    Flags differences, scale mismatches (e.g. thousands vs standard units), and share basis mismatches.
    """
    f_unit = fact.unit if isinstance(fact, Fact) else str(fact.get("unit", ""))
    f_concept = fact.concept if isinstance(fact, Fact) else str(fact.get("concept", ""))

    errors: List[str] = []
    norm_actual = f_unit.strip().lower()
    norm_expected = expected_unit.strip().lower()

    if norm_actual != norm_expected:
        msg = f"Unit mismatch for concept '{f_concept}': expected '{expected_unit}', found '{f_unit}'"
        # Check specific known scale / basis discrepancies
        if "thousand" in norm_actual and "thousand" not in norm_expected:
            msg += " (detected 1,000x scale unit error: reported in thousands instead of standard units)"
        elif "million" in norm_actual and "million" not in norm_expected:
            msg += " (detected 1,000,000x scale unit error: reported in millions instead of standard units)"
        elif "diluted" in norm_actual and "basic" in norm_expected:
            msg += " (detected share count basis mismatch: diluted shares vs expected basic shares)"
        elif "basic" in norm_actual and "diluted" in norm_expected:
            msg += " (detected share count basis mismatch: basic shares vs expected diluted shares)"
        errors.append(msg)

    return errors


def flag_concept_unit_mismatch(
    fact: Union[Fact, Mapping[str, Any]],
    expected_concept_units: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """
    Check if a fact's unit is consistent with standard expected units for its concept.
    """
    registry = expected_concept_units or STANDARD_CONCEPT_UNITS
    concept = fact.concept if isinstance(fact, Fact) else str(fact.get("concept", ""))
    expected = registry.get(concept)
    if expected:
        return flag_unit_mismatch(fact, expected)
    return []


def check_ledger_consistency(facts: Iterable[Union[Fact, Mapping[str, Any]]]) -> List[str]:
    """
    Check a set of facts for inconsistencies:
    - Differing units or currencies for the same entity + concept + period without supersedes.
    - Differing share count basis without supersedes.
    - Circular supersession chains.
    """
    fact_objects: List[Fact] = []
    for item in facts:
        if isinstance(item, Fact):
            fact_objects.append(item)
        else:
            fact_objects.append(Fact.from_dict(item))

    errors: List[str] = []
    id_map = {f.id: f for f in fact_objects if f.id}

    # 1. Circular supersession check
    for f in fact_objects:
        visited: Set[str] = set()
        curr: Optional[str] = f.supersedes
        while curr:
            if curr == f.id or curr in visited:
                errors.append(f"Circular supersedes chain detected starting at fact '{f.id}' referencing '{curr}'")
                break
            visited.add(curr)
            parent = id_map.get(curr)
            curr = parent.supersedes if parent else None

    # 2. Group by (company, concept, period_key, dimensions_key, kind)
    grouped: Dict[Tuple[str, str, str, str, str], List[Fact]] = {}
    for f in fact_objects:
        key = (f.company, f.concept, f.period_key(), f.dimensions_key(), str(f.kind))
        grouped.setdefault(key, []).append(f)

    for (comp, conc, p_key, d_key, k_val), group in grouped.items():
        if len(group) <= 1:
            continue

        # Check pairs for unlinked conflicts
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                f1, f2 = group[i], group[j]
                # Check if f1 supersedes f2 or f2 supersedes f1 (or transitive)
                is_superseded = (
                    f1.supersedes == f2.id
                    or f2.supersedes == f1.id
                    or _is_ancestor(f1, f2.id, id_map)
                    or _is_ancestor(f2, f1.id, id_map)
                )
                if not is_superseded:
                    # Check unit difference
                    if f1.unit.lower() != f2.unit.lower():
                        errors.append(
                            f"Conflicting units for {comp} '{conc}' ({p_key}) between fact '{f1.id}' ({f1.unit}) "
                            f"and '{f2.id}' ({f2.unit}) without supersedes relationship"
                        )
                    # Check currency difference
                    if (f1.currency or "").lower() != (f2.currency or "").lower():
                        errors.append(
                            f"Conflicting currency for {comp} '{conc}' ({p_key}) between fact '{f1.id}' ({f1.currency}) "
                            f"and '{f2.id}' ({f2.currency}) without supersedes relationship"
                        )
                    # Check value difference
                    if f1.value != f2.value and f1.is_available() and f2.is_available():
                        errors.append(
                            f"Conflicting values for {comp} '{conc}' ({p_key}) between fact '{f1.id}' ({f1.value}) "
                            f"and '{f2.id}' ({f2.value}) without supersedes relationship"
                        )

    return errors


def _is_ancestor(fact: Fact, target_id: str, id_map: Dict[str, Fact]) -> bool:
    curr: Optional[str] = fact.supersedes
    while curr:
        if curr == target_id:
            return True
        parent = id_map.get(curr)
        curr = parent.supersedes if parent else None
    return False


def resolve_as_of(
    facts: Iterable[Union[Fact, Mapping[str, Any]]],
    concept: str,
    cutoff: Union[str, datetime, date],
    *,
    company: Optional[str] = None,
    period_end: Optional[str] = None,
    instant: Optional[str] = None,
    kind: Union[FactKind, str] = FactKind.REPORTED,
    allow_forecasts: bool = False,
    dimensions: Optional[Dict[str, Any]] = None,
) -> Optional[Fact]:
    """
    Resolve the authoritative fact as of a given cutoff timestamp.

    - Excludes facts published AFTER cutoff (anti-look-ahead).
    - Forecast records are excluded by default and NEVER satisfy reported-fact lookup.
    - Superseded facts are replaced by their superseding record ONLY if the superseding
      fact was also published on or before the cutoff.
    - If a restatement was published after cutoff, the pre-restatement original fact is returned.
    """
    parsed_facts: List[Fact] = []
    for item in facts:
        if isinstance(item, Fact):
            parsed_facts.append(item)
        else:
            parsed_facts.append(Fact.from_dict(item))

    # Normalize target kind
    target_kind = kind.value if isinstance(kind, FactKind) else str(kind).lower()

    # Filter candidates
    candidates: List[Fact] = []
    for f in parsed_facts:
        if f.concept != concept:
            continue
        if company and f.company.upper() != company.upper():
            continue
        if period_end and f.period_end != period_end:
            continue
        if instant and f.instant != instant:
            continue
        if dimensions is not None and (f.dimensions or {}) != dimensions:
            continue

        # Kind guard: forecasts must never satisfy reported lookup
        f_kind = f.kind.value if isinstance(f.kind, FactKind) else str(f.kind).lower()
        if not allow_forecasts and f_kind in (FactKind.FORECAST.value, FactKind.ESTIMATE.value):
            continue
        if target_kind != "any":
            if target_kind in (FactKind.REPORTED.value, FactKind.RESTATEMENT.value):
                if f_kind not in (FactKind.REPORTED.value, FactKind.RESTATEMENT.value):
                    continue
            elif f_kind != target_kind:
                continue

        # Chronology guard: must be available as of cutoff
        if not usable_as_of(f, cutoff):
            continue

        candidates.append(f)

    if not candidates:
        return None

    # Handle supersession among available candidates
    candidate_ids = {c.id for c in candidates}
    superseded_ids = {c.supersedes for c in candidates if c.supersedes and c.supersedes in candidate_ids}

    # Filter out facts that have been superseded by an available candidate
    active = [c for c in candidates if c.id not in superseded_ids]

    if not active:
        # Fallback if circular
        active = candidates

    # Sort remaining active by availability_time descending (latest publication first)
    def _sort_key(fact: Fact) -> Tuple[datetime, str]:
        try:
            dt = parse_iso_datetime(fact.availability_time)
        except Exception:
            dt = datetime.min.replace(tzinfo=timezone.utc)
        return (dt, fact.id)

    active.sort(key=_sort_key, reverse=True)
    return active[0]


def filter_facts_as_of(
    facts: Iterable[Union[Fact, Mapping[str, Any]]],
    cutoff: Union[str, datetime, date],
    *,
    allow_forecasts: bool = False,
) -> List[Fact]:
    """
    Filter a collection of facts to those available on or before the cutoff timestamp.
    Forecast records are excluded by default unless allow_forecasts is True.
    """
    results: List[Fact] = []
    for item in facts:
        fact = item if isinstance(item, Fact) else Fact.from_dict(item)
        if not allow_forecasts and fact.is_forecast():
            continue
        if usable_as_of(fact, cutoff):
            results.append(fact)
    return results


def _is_sec_companyfacts_payload(data: Any) -> bool:
    return isinstance(data, dict) and "facts" in data and ("cik" in data or "entityName" in data)


def load_fact_ledger(source: Union[str, Path, Iterable[Any]]) -> List[Fact]:
    """
    Load financial facts from a directory of JSON files, a single JSON file, or an iterable.
    Automatically handles SEC EDGAR companyfacts JSON payloads.
    """
    from src.sec_facts import parse_sec_company_facts

    facts: List[Fact] = []

    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            for json_file in sorted(path.glob("*.json")):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if _is_sec_companyfacts_payload(data):
                        facts.extend(parse_sec_company_facts(data))
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                facts.append(Fact.from_dict(item))
                    elif isinstance(data, dict):
                        facts.append(Fact.from_dict(data))
                except Exception as err:
                    raise ValueError(f"Error loading fact file '{json_file}': {err}") from err
            for jsonl_file in sorted(path.glob("*.jsonl")):
                try:
                    with jsonl_file.open("r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                facts.append(Fact.from_dict(json.loads(line)))
                except Exception as err:
                    raise ValueError(f"Error loading fact file '{jsonl_file}': {err}") from err
        elif path.is_file():
            try:
                if path.suffix == ".jsonl":
                    with path.open("r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                facts.append(Fact.from_dict(json.loads(line)))
                else:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if _is_sec_companyfacts_payload(data):
                        facts.extend(parse_sec_company_facts(data))
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                facts.append(Fact.from_dict(item))
                    elif isinstance(data, dict):
                        facts.append(Fact.from_dict(data))
            except Exception as err:
                raise ValueError(f"Error loading fact file '{path}': {err}") from err
        else:
            raise FileNotFoundError(f"Fact ledger source not found: {path}")
    elif isinstance(source, Mapping) and _is_sec_companyfacts_payload(source):
        facts.extend(parse_sec_company_facts(dict(source)))
    elif isinstance(source, Iterable):
        for item in source:
            if isinstance(item, Fact):
                facts.append(item)
            elif isinstance(item, dict):
                if _is_sec_companyfacts_payload(item):
                    facts.extend(parse_sec_company_facts(item))
                else:
                    facts.append(Fact.from_dict(item))
            elif isinstance(item, (str, Path)):
                facts.extend(load_fact_ledger(item))
            else:
                raise TypeError(f"Unsupported fact item type: {type(item).__name__}")
    else:
        raise TypeError(f"Unsupported source type: {type(source).__name__}")

    return facts
