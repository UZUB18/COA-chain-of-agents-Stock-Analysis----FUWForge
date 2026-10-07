"""
Deterministic financial calculation engine for FUWForge.
Implements arithmetic standards from institutional underwriting doctrine (stock-analyst):
- Altman Z-Score & Beneish M-Score
- Accruals Ratio & Cash Conversion
- Return-Driver Stack & 5-Year Forward Expected Returns
- Standardized Comparison Vector generation
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SCORECARD_WEIGHTS: Dict[str, float] = {
    "Financials": 0.25,
    "Moat": 0.20,
    "Growth": 0.15,
    "Valuation": 0.15,
    "MgmtGov": 0.10,
    "RiskInverse": 0.10,
    "ESG": 0.025,
    "Market": 0.025,
}


def resolve_weights(raw: Any) -> Tuple[Dict[str, float], List[str]]:
    """
    Resolve a scorecard weight override.

    Accepts ``None``/``"default"`` (canonical weights), a mapping, or a JSON
    object string. Returns (weights, errors); on errors the canonical weights
    are returned so callers can degrade safely.
    """
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "default")):
        return dict(DEFAULT_SCORECARD_WEIGHTS), []

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return dict(DEFAULT_SCORECARD_WEIGHTS), ["weights string is not valid JSON"]

    if not isinstance(raw, dict):
        return dict(DEFAULT_SCORECARD_WEIGHTS), [
            "weights must be a mapping of category -> weight"
        ]

    expected = set(DEFAULT_SCORECARD_WEIGHTS)
    missing = sorted(expected - set(raw))
    unknown = sorted(set(raw) - expected)
    errors: List[str] = []
    if missing:
        errors.append("weights missing categories: " + ", ".join(missing))
    if unknown:
        errors.append("weights contain unknown categories: " + ", ".join(unknown))

    resolved: Dict[str, float] = {}
    for category in DEFAULT_SCORECARD_WEIGHTS:
        value = raw.get(category)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"weights.{category} must be numeric")
            continue
        if value < 0:
            errors.append(f"weights.{category} must not be negative")
            continue
        resolved[category] = float(value)

    if not errors:
        total = sum(resolved.values())
        if abs(total - 1.0) > 0.001:
            errors.append(f"weights must sum to 1.0 (found {total:.4f})")

    if errors:
        return dict(DEFAULT_SCORECARD_WEIGHTS), errors
    return {category: resolved[category] for category in DEFAULT_SCORECARD_WEIGHTS}, []


def cagr(start_val: float, end_val: float, years: float) -> Optional[float]:
    """Calculate Compound Annual Growth Rate as a percentage."""
    if start_val <= 0 or end_val <= 0 or years <= 0:
        return None
    try:
        return round(((end_val / start_val) ** (1.0 / years) - 1.0) * 100.0, 2)
    except (ZeroDivisionError, OverflowError):
        return None


def fcf_conversion(fcf: float, net_income: float) -> Optional[float]:
    """FCF to Net Income conversion percentage."""
    if net_income == 0:
        return None
    return round((fcf / net_income) * 100.0, 2)


def accruals_ratio(
    net_income: float, ocf: float, avg_total_assets: float
) -> Optional[float]:
    """
    Balance sheet / cash flow accruals ratio.
    Negative indicates higher earnings quality; high positive indicates aggressive accruals.
    """
    if avg_total_assets <= 0:
        return None
    return round((net_income - ocf) / avg_total_assets, 4)


def fcf_yield(fcf: float, market_cap: float) -> Optional[float]:
    """Free Cash Flow yield as a percentage."""
    if market_cap <= 0:
        return None
    return round((fcf / market_cap) * 100.0, 2)


def roic(nopat: float, invested_capital: float) -> Optional[float]:
    """Return on Invested Capital as a percentage."""
    if invested_capital <= 0:
        return None
    return round((nopat / invested_capital) * 100.0, 2)


def net_debt_to_ebitda(
    total_debt: float, cash: float, ebitda: float
) -> Optional[float]:
    """Net debt to EBITDA ratio."""
    if ebitda <= 0:
        return None
    return round((total_debt - cash) / ebitda, 2)


def altman_z_score(
    working_capital: float,
    total_assets: float,
    retained_earnings: float,
    ebit: float,
    market_cap: float,
    total_liabilities: float,
    revenue: float,
) -> Dict[str, Any]:
    """
    Classic Altman Z-Score for public manufacturing / operating corporations:
    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 0.999*X5
    """
    if total_assets <= 0 or total_liabilities <= 0:
        return {
            "score": None,
            "classification": "Indeterminate",
            "feasible": False,
            "inputs": {},
        }

    x1 = working_capital / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x4 = market_cap / total_liabilities
    x5 = revenue / total_assets

    score = round(1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 0.999 * x5, 2)

    if score > 2.99:
        classification = "Safe Zone"
    elif score >= 1.81:
        classification = "Grey Zone"
    else:
        classification = "Distress Zone"

    return {
        "score": score,
        "classification": classification,
        "feasible": True,
        "inputs": {
            "X1_wc_ta": round(x1, 4),
            "X2_re_ta": round(x2, 4),
            "X3_ebit_ta": round(x3, 4),
            "X4_mc_tl": round(x4, 4),
            "X5_rev_ta": round(x5, 4),
        },
    }


def beneish_m_score(
    dsri: float = 1.0,
    gmi: float = 1.0,
    aqi: float = 1.0,
    sgi: float = 1.0,
    depi: float = 1.0,
    sgai: float = 1.0,
    lvgi: float = 1.0,
    tata: float = 0.0,
) -> Dict[str, Any]:
    """
    8-variable Beneish M-Score for earnings manipulation detection.
    M > -1.78 suggests high probability of earnings manipulation.
    """
    m_score = (
        -4.84
        + 0.920 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.037 * tata
        + 0.0327 * lvgi
    )
    rounded = round(m_score, 2)
    classification = (
        "High Manipulation Risk" if rounded > -1.78 else "Unlikely Manipulator"
    )

    return {
        "score": rounded,
        "classification": classification,
        "feasible": True,
        "flags": {
            "DSRI_high": dsri > 1.3,
            "GMI_deteriorating": gmi > 1.2,
            "AQI_high": aqi > 1.25,
            "SGI_high": sgi > 1.3,
            "TATA_high": tata > 0.05,
        },
    }


def return_driver_stack(
    starting_yield: float,
    fundamental_growth: float,
    multiple_change: float,
    capital_allocation: float = 0.0,
) -> Dict[str, Any]:
    """
    5-Year Return-Driver Stack:
    Expected IRR ≈ Starting Yield + Fundamental Growth + Multiple Change ± Capital Allocation
    """
    total_irr = round(
        starting_yield + fundamental_growth + multiple_change + capital_allocation, 2
    )
    return {
        "expected_5y_irr_base_pct": total_irr,
        "starting_yield_pct": round(starting_yield, 2),
        "fundamental_growth_pct": round(fundamental_growth, 2),
        "multiple_change_annualized_pct": round(multiple_change, 2),
        "capital_allocation_tailwind_drag_pct": round(capital_allocation, 2),
    }


def generate_comparison_fields(
    quality_score_10: float,
    valuation_score_10: float,
    expected_5y_irr_base_pct: float,
    downside_case_5y_irr_pct: float,
    fcf_yield_ttm_pct: Optional[float] = None,
    roic_ttm_pct: Optional[float] = None,
    net_debt_to_ebitda_val: Optional[float] = None,
    moat_score_10: Optional[float] = None,
    reinvestment_runway_score_10: Optional[float] = None,
    balance_sheet_score_10: Optional[float] = None,
    fragility_score_10: Optional[float] = None,
    evidence_quality_score_10: float = 9.0,
    data_completeness_score_10: float = 9.0,
) -> Dict[str, Any]:
    """
    Produces the standardized 13-metric comparison vector from stock-analyst,
    enabling downstream multi-stock portfolio screeners and ranking tables.
    """
    return {
        "quality_score_10": round(quality_score_10, 1),
        "valuation_score_10": round(valuation_score_10, 1),
        "expected_5y_irr_base_pct": round(expected_5y_irr_base_pct, 1),
        "downside_case_5y_irr_pct": round(downside_case_5y_irr_pct, 1),
        "fcf_yield_ttm_pct": round(fcf_yield_ttm_pct, 2)
        if fcf_yield_ttm_pct is not None
        else None,
        "roic_ttm_pct": round(roic_ttm_pct, 1) if roic_ttm_pct is not None else None,
        "net_debt_to_ebitda": round(net_debt_to_ebitda_val, 2)
        if net_debt_to_ebitda_val is not None
        else None,
        "moat_score_10": round(moat_score_10, 1) if moat_score_10 is not None else None,
        "reinvestment_runway_score_10": round(reinvestment_runway_score_10, 1)
        if reinvestment_runway_score_10 is not None
        else None,
        "balance_sheet_score_10": round(balance_sheet_score_10, 1)
        if balance_sheet_score_10 is not None
        else None,
        "fragility_score_10": round(fragility_score_10, 1)
        if fragility_score_10 is not None
        else None,
        "evidence_quality_score_10": round(evidence_quality_score_10, 1),
        "data_completeness_score_10": round(data_completeness_score_10, 1),
    }
