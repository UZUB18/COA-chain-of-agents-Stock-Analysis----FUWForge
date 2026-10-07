"""Explicit, bounded unavailable-data policy; never substitutes fake values."""
from __future__ import annotations

from typing import Any, Mapping

ALLOWED_DATA_GAPS = {
    "A1": set(),
    "A2": {"PEER_LIST", "TopCustomerPct", "TopSupplierPct"},
    "A3": {"AltmanZ.score", "BeneishM.score", "PEG_TTM", "PEG_Fwd", "HypeFlag", "PeerTable",
           "TechnicalSet.SMA_50", "TechnicalSet.SMA_200", "TechnicalSet.RSI_14"},
    "A4": {"CEO_Pay_Ratio", "GlassdoorScore", "AttritionNote"},
    "A5": {"SentimentSplit", "SocialVsFundDivergence", "MajorLitigation", "AuditorChangeYN",
           "InsiderNet12m", "InstitutionalOwnershipTrend", "RegulatoryWatchlist"},
    "A7": {f"comparison_fields.{key}" for key in (
        "quality_score_10", "valuation_score_10", "expected_5y_irr_base_pct", "downside_case_5y_irr_pct",
        "fcf_yield_ttm_pct", "roic_ttm_pct", "net_debt_to_ebitda", "moat_score_10",
        "reinvestment_runway_score_10", "balance_sheet_score_10", "fragility_score_10",
        "evidence_quality_score_10", "data_completeness_score_10",
    )},
}


def missing_data_map(value: Any) -> dict[str, str]:
    """Accept the native schema's entry list or a legacy programmatic map."""
    if value is None:
        return {}
    if isinstance(value, dict):
        entries = list(value.items())
    elif isinstance(value, list):
        if not all(isinstance(entry, dict) and set(entry) == {"field", "reason"} for entry in value):
            raise ValueError("missing_data entries require exactly field and reason")
        entries = [(entry["field"], entry["reason"]) for entry in value]
    else:
        raise ValueError("missing_data must be a field/reason map or entry list")
    result = {}
    for field, reason in entries:
        if not isinstance(field, str) or not field.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("missing_data requires exact field paths and non-empty evidence-coverage reasons")
        if field in result:
            raise ValueError("missing_data cannot contain duplicate field paths")
        result[field] = reason.strip()
    return result


def documented_null(artifacts: Mapping[str, Any], path: str, missing: Mapping[str, str]) -> bool:
    """Only an explicitly present null at the documented path is unavailable."""
    value: Any = artifacts
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return False
        value = value[key]
    return value is None and bool(missing.get(path))
