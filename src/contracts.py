"""
Machine-checkable agent contracts.

The human-readable contracts live in ``docs/contracts/*.md``. This module encodes
the parts that must be enforced mechanically: required artifacts, structural
shapes, cross-agent linkage, section coverage, and scorecard arithmetic.

Severity model:

- Structural and semantic violations are errors: the run must not advance.
- Unknown section keys and injected normalization defaults are warnings; in
  strict mode injected defaults become errors.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from src.data_gaps import ALLOWED_DATA_GAPS, documented_null, missing_data_map
from src.calculation_witness import (
    CalculationWitness,
    PROVENANCE_MODEL_REPORTED,
    validate_witness,
)
from src.envelope import Envelope
from src.utils.beneish import (
    BENEISH_INPUT_KEYS, CALCULATOR_VERSION, beneish_score, classify,
)
from src.validators import ValidationResult

BENEISH_INDEX_KEYS = tuple(BENEISH_INPUT_KEYS)

AGENT_IDS = ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7")

CONTRACT_DOCS: Dict[str, str] = {
    "A1": "docs/contracts/A1_GOD_Gate.md",
    "A2": "docs/contracts/A2_Business_Market_Peers.md",
    "A3": "docs/contracts/A3_Quant_Valuation_Technicals.md",
    "A4": "docs/contracts/A4_Leadership_Governance_ESG.md",
    "A5": "docs/contracts/A5_News_Sentiment_Flows.md",
    "A6B": "docs/contracts/A6_Synthesis.md",
    "A6R": "docs/contracts/A6_Synthesis.md",
    "A6": "docs/contracts/A6_Synthesis.md",
    "A7": "docs/contracts/A7_Final_Assembly.md",
}

REQUIRED_ARTIFACTS: Dict[str, List[str]] = {
    "A1": ["price_timestamp_eet", "shares_basic", "shares_diluted", "market_cap_check"],
    "A2": ["PEER_LIST", "MoatTrend", "TopCustomerPct", "TopSupplierPct"],
    "A3": [
        "AltmanZ",
        "BeneishM",
        "OCF_vs_NI_Flag",
        "PEG_TTM",
        "PEG_Fwd",
        "HypeFlag",
        "PeerTable",
        "TechnicalSet",
    ],
    "A4": [
        "CEO_Pay_Ratio",
        "Auditor",
        "AuditorTenure",
        "GlassdoorScore",
        "AttritionNote",
        "ESG_Momentum",
        "LowSampleSizeCultureData",
    ],
    "A5": [
        "SentimentSplit",
        "SocialVsFundDivergence",
        "MajorLitigation",
        "AuditorChangeYN",
        "InsiderNet12m",
        "InstitutionalOwnershipTrend",
        "RegulatoryWatchlist",
    ],
    "A6B": ["BullCase"],
    "A6R": ["BearRebuttals"],
    "A6": ["DangerZoneDashboard", "ScenarioTable", "Catalysts", "Headwinds"],
    "A7": ["Scorecard", "SWOT", "Glossary", "comparison_fields"],
}

SECTION_CONTRACTS: Dict[str, List[str]] = {
    "A1": ["RAW_DATA_PACK"],
    "A2": [
        "SECTION_1_COMPANY_PROFILE",
        "SECTION_2_BUSINESS_MODEL",
        "SECTION_3_MARKET_AND_PEERS",
    ],
    "A3": [
        "SECTION_4_FINANCIAL_FORENSICS",
        "SECTION_5_VALUATION",
        "SECTION_12_TECHNICALS",
    ],
    "A4": ["SECTION_6_LEADERSHIP_GOVERNANCE", "SECTION_7_CULTURE_ESG"],
    "A5": ["SECTION_8_NEWS_SENTIMENT", "SECTION_9_FLOW_LITIGATION"],
    "A6B": ["SECTION_10_BULL_CASE"],
    "A6R": ["SECTION_11_BEAR_CASE"],
    "A6": ["SECTION_14_SYNTHESIS"],
    "A7": [
        "SECTION_0_EXEC_SUMMARY",
        "SECTION_13_IPO_HISTORY",
        "SECTION_15_SWOT",
        "SECTION_16_SCORECARD",
        "SECTION_17_GLOSSARY",
        "SECTION_18_LAY_SUMMARY",
    ],
}

OPTIONAL_SECTIONS: Dict[str, Set[str]] = {
    "A1": {"SECTION_1_SEC_INTAKE", "MISSING_DATA_TABLE"},
    "A2": {"AI_STRATEGY", "MISSING_DATA_TABLE"},
    "A3": {"MISSING_DATA_TABLE"},
    "A4": {"MISSING_DATA_TABLE"},
    "A5": {"MISSING_DATA_TABLE"},
    "A6B": {"MISSING_DATA_TABLE"},
    "A6R": {"MISSING_DATA_TABLE"},
    "A6": {"MISSING_DATA_TABLE"},
    "A7": {"MISSING_DATA_TABLE"},
}

SCORECARD_CATEGORIES = (
    "Financials",
    "Moat",
    "Growth",
    "Valuation",
    "MgmtGov",
    "RiskInverse",
    "ESG",
    "Market",
)
COMPARISON_FIELD_KEYS = (
    "quality_score_10",
    "valuation_score_10",
    "expected_5y_irr_base_pct",
    "downside_case_5y_irr_pct",
    "fcf_yield_ttm_pct",
    "roic_ttm_pct",
    "net_debt_to_ebitda",
    "moat_score_10",
    "reinvestment_runway_score_10",
    "balance_sheet_score_10",
    "fragility_score_10",
    "evidence_quality_score_10",
    "data_completeness_score_10",
)

_SECTION_RE = re.compile(r"^SECTION_(\d+)", re.IGNORECASE)


def _is_num(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _to_float(value: Any) -> Optional[float]:
    if _is_num(value):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.replace(",", "").strip())
            return parsed if math.isfinite(parsed) else None
        except ValueError:
            return None
    return None


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _in_enum(value: Any, options: Iterable[str]) -> bool:
    return isinstance(value, str) and value.strip().lower() in {
        opt.lower() for opt in options
    }


def _section_number(key: str) -> Optional[int]:
    match = _SECTION_RE.match(key)
    return int(match.group(1)) if match else None


def _check_a1(artifacts: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if not _is_text(artifacts.get("price_timestamp_eet")):
        errors.append("price_timestamp_eet must be a non-empty string")
    for key in ("shares_basic", "shares_diluted"):
        value = _to_float(artifacts.get(key))
        if value is None or value <= 0:
            errors.append(f"{key} must be a positive number")
    cap_check = artifacts.get("market_cap_check")
    if not isinstance(cap_check, dict):
        errors.append("market_cap_check must be an object")
    else:
        price, shares, cap = (
            _to_float(cap_check.get("price")),
            _to_float(cap_check.get("shares")),
            _to_float(cap_check.get("cap")),
        )
        if price is None or shares is None or cap is None:
            errors.append("market_cap_check requires numeric price, shares and cap")
        elif price <= 0 or shares <= 0 or cap <= 0:
            errors.append("market_cap_check price, shares and cap must be positive")
        elif abs(cap - price * shares) / (price * shares) > 0.05:
            errors.append(
                "market_cap_check.cap deviates from price × shares by more than 5%"
            )
    return errors


def _check_a2(artifacts: Mapping[str, Any], missing=None) -> List[str]:
    missing = missing or {}
    errors: List[str] = []
    peers = artifacts.get("PEER_LIST")
    if not documented_null(artifacts, "PEER_LIST", missing) and (
        not isinstance(peers, list)
        or len(peers) < 3
        or not all(_is_text(p) for p in peers)
    ):
        errors.append("PEER_LIST must contain at least three ticker strings")
    if not _in_enum(
        artifacts.get("MoatTrend"), ("Expanding", "Stable", "Eroding", "Unclear")
    ):
        errors.append("MoatTrend must be one of Expanding|Stable|Eroding|Unclear")
    for key in ("TopCustomerPct", "TopSupplierPct"):
        value = artifacts.get(key)
        if documented_null(artifacts, key, missing):
            continue
        if isinstance(value, str):
            if not value.strip():
                errors.append(f"{key} must not be blank")
        elif isinstance(value, dict):
            if _to_float(value.get("percent")) is None:
                errors.append(
                    f"{key}.percent must be numeric when an object is provided"
                )
        else:
            errors.append(f"{key} must be a disclosure object or an explicit string")
    return errors


def _check_a3(artifacts: Mapping[str, Any], missing=None) -> List[str]:
    missing = missing or {}
    errors: List[str] = []
    altman = artifacts.get("AltmanZ")
    if not isinstance(altman, dict):
        errors.append("AltmanZ must be an object")
    elif altman.get("score") is None:
        if not _is_text(altman.get("classification")):
            errors.append("AltmanZ without a numeric score requires a classification")
    elif not _is_num(altman.get("score")):
        errors.append("AltmanZ.score must be numeric or null")
    beneish = artifacts.get("BeneishM")
    beneish_errors, _ = _check_beneish(beneish, artifacts, missing)
    errors.extend(beneish_errors)
    if not _in_enum(
        artifacts.get("OCF_vs_NI_Flag"), ("Aligned", "Divergent", "Negative")
    ):
        errors.append("OCF_vs_NI_Flag must be one of Aligned|Divergent|Negative")
    for key in ("PEG_TTM", "PEG_Fwd"):
        if _to_float(artifacts.get(key)) is None and not documented_null(artifacts, key, missing):
            errors.append(f"{key} must be numeric or documented unavailable")
    if not documented_null(artifacts, "HypeFlag", missing) and not _in_enum(artifacts.get("HypeFlag"), ("Reality", "Balanced", "Hype Risk")):
        errors.append("HypeFlag must be one of Reality|Balanced|Hype Risk")
    if not isinstance(artifacts.get("PeerTable"), list) and not documented_null(artifacts, "PeerTable", missing):
        errors.append("PeerTable must be a list")
    technical = artifacts.get("TechnicalSet")
    if not isinstance(technical, dict):
        errors.append("TechnicalSet must be an object")
    else:
        for key in ("SMA_50", "SMA_200", "RSI_14"):
            if not _is_num(technical.get(key)) and not documented_null(artifacts, f"TechnicalSet.{key}", missing):
                errors.append(f"TechnicalSet.{key} must be numeric or documented unavailable")
    return errors


def _check_beneish(
    beneish: Any, artifacts: Mapping[str, Any], missing: Mapping[str, str]
) -> Tuple[List[str], List[str]]:
    """
    Beneish must be either a recomputed number or an explicit unknown.

    Three acceptable states:

    1. Deterministic: ``provenance='deterministic'`` plus a witness carrying all
       eight index ratios, so any reader can recompute the score.
    2. Documented unavailable: an explicit null score, with the reason recorded
       either on the artifact or in ``metrics.missing_data``.
    3. Model-reported or legacy: a bare score with no recomputation. Permitted
       outside strict mode, but only when it says so — a reader must never
       mistake an invented number for a computed one.

    An automated run always produces one of states 1-3, because the runner
    stamps provenance during normalization. An *unlabelled* bare score can only
    arrive from a hand-written controller/legacy envelope; that is reported as a
    warning so the historical identity is preserved and marked rather than
    silently resealed or rejected outright. Strict mode promotes it to an error.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(beneish, dict):
        return ["BeneishM must be an object"], warnings

    score = beneish.get("score")

    # State 2 — explicitly unavailable.
    if score is None or beneish.get("feasible") is False:
        if score is not None or beneish.get("raw_score") is not None:
            return ["an unavailable BeneishM must not carry a numeric score"], warnings
        if _is_text(beneish.get("unavailable_reason")):
            return errors, warnings
        if documented_null(artifacts, "BeneishM.score", missing) and _is_text(
            beneish.get("classification")
        ):
            return errors, warnings
        return [
            "an unavailable BeneishM requires either an unavailable_reason or a "
            "documented missing_data entry for BeneishM.score"
        ], warnings

    if not _is_num(score):
        return ["BeneishM.score must be numeric or an explicit unavailable result"], warnings

    # State 3 — model-reported, labelled.
    if beneish.get("provenance") == PROVENANCE_MODEL_REPORTED:
        return errors, warnings

    # State 1 — deterministic, recomputable.
    witness = beneish.get("calculation_witness")
    if beneish.get("provenance") == "deterministic" and isinstance(witness, dict):
        if witness.get("provenance") != "deterministic":
            errors.append("BeneishM.calculation_witness must record deterministic provenance")
        if witness.get("formula_id") != "beneish_m_score":
            errors.append("BeneishM.calculation_witness must name the beneish_m_score formula")
        inputs = witness.get("inputs")
        if not isinstance(inputs, dict) or set(inputs) != set(BENEISH_INDEX_KEYS):
            errors.append(
                "BeneishM.calculation_witness.inputs must carry all eight index ratios "
                "so the score can be independently recomputed"
            )
        else:
            try:
                expected = beneish_score(**inputs)
            except (ValueError, TypeError, OverflowError):
                errors.append("BeneishM witness inputs must be finite numeric ratios")
            else:
                for label, actual, target in (
                    ("witness.result", witness.get("result"), expected),
                    ("raw_score", beneish.get("raw_score"), expected),
                    ("score", score, round(expected, 2)),
                ):
                    if not _is_num(actual) or not math.isclose(
                        actual, target, rel_tol=1e-12, abs_tol=1e-12
                    ):
                        errors.append(f"BeneishM.{label} disagrees with deterministic replay")
                if beneish.get("classification") != classify(expected):
                    errors.append("BeneishM classification disagrees with the raw-score policy")
        if witness.get("formula_version") != CALCULATOR_VERSION:
            errors.append("BeneishM witness formula version is unsupported")
        if beneish.get("calculator_version") != CALCULATOR_VERSION:
            errors.append("BeneishM calculator version is unsupported")
        units = witness.get("units")
        if not isinstance(units, dict) or any(
            units.get(key) != "ratio" for key in BENEISH_INDEX_KEYS
        ):
            errors.append("BeneishM witness inputs require ratio units")
        return errors, warnings

    # Legacy / unlabelled: a bare score with no recorded provenance at all.
    warnings.append(
        "BeneishM carries a bare score with no recorded provenance and no index "
        "ratios; it was not recomputed by the deterministic calculator. Treat as "
        "unverified (typical of a legacy or hand-authored controller envelope)."
    )
    return errors, warnings


def _check_a4(artifacts: Mapping[str, Any], missing=None) -> List[str]:
    missing = missing or {}
    errors: List[str] = []
    pay = _to_float(artifacts.get("CEO_Pay_Ratio"))
    if not documented_null(artifacts, "CEO_Pay_Ratio", missing) and (pay is None or pay < 0):
        errors.append("CEO_Pay_Ratio must be a non-negative number")
    for key in ("Auditor", "AuditorTenure", "AttritionNote"):
        if not _is_text(artifacts.get(key)) and not (key == "AttritionNote" and documented_null(artifacts, key, missing)):
            errors.append(f"{key} must be a non-empty string")
    glassdoor = _to_float(artifacts.get("GlassdoorScore"))
    if not documented_null(artifacts, "GlassdoorScore", missing) and (glassdoor is None or not 0.0 <= glassdoor <= 5.0):
        errors.append("GlassdoorScore must be a number between 0 and 5")
    if not _in_enum(
        artifacts.get("ESG_Momentum"),
        ("Improving", "Stable", "Deteriorating", "Unclear"),
    ):
        errors.append(
            "ESG_Momentum must be one of Improving|Stable|Deteriorating|Unclear"
        )
    if documented_null(artifacts, "GlassdoorScore", missing) and artifacts.get("LowSampleSizeCultureData") is not True:
        errors.append("Unavailable GlassdoorScore requires LowSampleSizeCultureData=true")
    if not isinstance(artifacts.get("LowSampleSizeCultureData"), bool):
        errors.append("LowSampleSizeCultureData must be a boolean")
    return errors


def _check_a5(artifacts: Mapping[str, Any], missing=None) -> List[str]:
    missing = missing or {}
    errors: List[str] = []
    split = artifacts.get("SentimentSplit")
    if documented_null(artifacts, "SentimentSplit", missing):
        pass
    elif not isinstance(split, dict):
        errors.append("SentimentSplit must be an object")
    else:
        shares = [_to_float(split.get(k)) for k in ("pos", "neu", "neg")]
        if any(v is None or not 0.0 <= v <= 1.0 for v in shares):
            errors.append("SentimentSplit pos/neu/neg must be numbers between 0 and 1")
        elif not 0.9 <= sum(shares) <= 1.1:  # type: ignore[arg-type]
            errors.append("SentimentSplit pos/neu/neg must sum to ~1.0")
    if not documented_null(artifacts, "SocialVsFundDivergence", missing) and not _in_enum(
        artifacts.get("SocialVsFundDivergence"), ("Aligned", "Mild", "Pump Risk")
    ):
        errors.append("SocialVsFundDivergence must be one of Aligned|Mild|Pump Risk")
    if not isinstance(artifacts.get("MajorLitigation"), list) and not documented_null(artifacts, "MajorLitigation", missing):
        errors.append("MajorLitigation must be a list")
    if not documented_null(artifacts, "AuditorChangeYN", missing) and not _in_enum(artifacts.get("AuditorChangeYN"), ("Yes", "No")):
        errors.append("AuditorChangeYN must be Yes or No")
    insider = artifacts.get("InsiderNet12m")
    if not documented_null(artifacts, "InsiderNet12m", missing) and (not isinstance(insider, dict) or not _in_enum(
        insider.get("direction"), ("NetBuy", "NetSell", "Neutral")
    )):
        errors.append("InsiderNet12m.direction must be one of NetBuy|NetSell|Neutral")
    trend = artifacts.get("InstitutionalOwnershipTrend")
    if not documented_null(artifacts, "InstitutionalOwnershipTrend", missing) and (not isinstance(trend, dict) or not _in_enum(
        trend.get("direction"), ("Up", "Down", "Flat")
    )):
        errors.append(
            "InstitutionalOwnershipTrend.direction must be one of Up|Down|Flat"
        )
    if not isinstance(artifacts.get("RegulatoryWatchlist"), list) and not documented_null(artifacts, "RegulatoryWatchlist", missing):
        errors.append("RegulatoryWatchlist must be a list")
    return errors


def _check_bull_case(artifacts: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    cases = artifacts.get("BullCase")
    if not isinstance(cases, list) or not cases:
        errors.append("BullCase must be a non-empty list")
        return errors
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            errors.append(f"BullCase[{index}] must be an object")
            continue
        if not _is_text(case.get("id")):
            errors.append(f"BullCase[{index}].id must be a non-empty string")
        if not _is_text(case.get("thesis")):
            errors.append(f"BullCase[{index}].thesis must be a non-empty string")
        evidence = case.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(_is_text(item) for item in evidence)
        ):
            errors.append(
                f"BullCase[{index}].evidence must cite at least one upstream artifact"
            )
    return errors


def _check_bear_rebuttals(rebuttals: Any, bull_ids: Optional[Set[str]]) -> List[str]:
    errors: List[str] = []
    if not isinstance(rebuttals, list) or not rebuttals:
        errors.append("BearRebuttals must be a non-empty list")
        return errors
    for index, item in enumerate(rebuttals, start=1):
        if not isinstance(item, dict):
            errors.append(f"BearRebuttals[{index}] must be an object")
            continue
        bull_id = item.get("bull_id")
        if not _is_text(bull_id):
            errors.append(f"BearRebuttals[{index}].bull_id must be a non-empty string")
        elif bull_ids is not None and bull_id not in bull_ids:
            errors.append(
                f"BearRebuttals[{index}].bull_id '{bull_id}' does not match any BullCase id"
            )
        if not _in_enum(item.get("probability"), ("High", "Med", "Low", "Medium")):
            errors.append(f"BearRebuttals[{index}].probability must be High|Med|Low")
        if not _in_enum(item.get("severity"), ("High", "Med", "Low", "Medium")):
            errors.append(f"BearRebuttals[{index}].severity must be High|Med|Low")
        if not _is_text(item.get("counter")):
            errors.append(f"BearRebuttals[{index}].counter must be a non-empty string")
    return errors


def _check_linkage(rebuttals: Any, bull_ids: Optional[Set[str]]) -> List[str]:
    """A6 checks that upstream rebuttals map onto known bull IDs, without re-validating their shape."""
    errors: List[str] = []
    if not isinstance(rebuttals, list) or bull_ids is None:
        return errors
    for index, item in enumerate(rebuttals, start=1):
        if not isinstance(item, dict):
            continue
        bull_id = item.get("bull_id")
        if _is_text(bull_id) and bull_id not in bull_ids:
            errors.append(
                f"BearRebuttals[{index}].bull_id '{bull_id}' does not match any BullCase id"
            )
    return errors


def _check_a6(
    artifacts: Mapping[str, Any], bull_ids: Optional[Set[str]], rebuttals: Any
) -> List[str]:
    errors: List[str] = []
    dashboard = artifacts.get("DangerZoneDashboard")
    if not isinstance(dashboard, list) or not dashboard:
        errors.append("DangerZoneDashboard must be a non-empty list")
    else:
        for index, row in enumerate(dashboard, start=1):
            if (
                not isinstance(row, dict)
                or not _is_text(row.get("category"))
                or not _is_text(row.get("flag"))
                or not _is_text(row.get("note"))
            ):
                errors.append(
                    f"DangerZoneDashboard[{index}] requires category, flag and note strings"
                )
    scenarios = artifacts.get("ScenarioTable")
    if not isinstance(scenarios, dict):
        errors.append("ScenarioTable must be an object")
    else:
        by_lower = {str(k).lower(): v for k, v in scenarios.items()}
        for name in ("bull", "base", "bear"):
            scenario = by_lower.get(name)
            if not isinstance(scenario, dict) or not scenario:
                errors.append(
                    f"ScenarioTable.{name.title()} must be a populated object"
                )
                continue
            keys_lower = {str(k).lower() for k in scenario.keys()}
            if not {"revenue", "eps"} <= keys_lower:
                errors.append(
                    f"ScenarioTable.{name.title()} must include Revenue and EPS"
                )
    if not isinstance(artifacts.get("Catalysts"), list) or not artifacts.get(
        "Catalysts"
    ):
        errors.append("Catalysts must be a non-empty list")
    if not isinstance(artifacts.get("Headwinds"), list) or not artifacts.get(
        "Headwinds"
    ):
        errors.append("Headwinds must be a non-empty list")
    if rebuttals is not None:
        errors.extend(_check_linkage(rebuttals, bull_ids))
    return errors


def _check_a7(artifacts: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    scorecard = artifacts.get("Scorecard")
    if not isinstance(scorecard, dict):
        errors.append("Scorecard must be an object")
    else:
        weighted_total = 0.0
        for category in SCORECARD_CATEGORIES:
            cell = scorecard.get(category)
            if not isinstance(cell, dict):
                errors.append(f"Scorecard.{category} must be an object")
                continue
            weight, score, weighted = (
                _to_float(cell.get("weight")),
                _to_float(cell.get("score")),
                _to_float(cell.get("weighted")),
            )
            if weight is None or score is None or weighted is None:
                errors.append(
                    f"Scorecard.{category} requires numeric weight, score and weighted"
                )
                continue
            if not 0.0 <= score <= 10.0:
                errors.append(f"Scorecard.{category}.score must be between 0 and 10")
            if abs(weighted - score * weight) > 0.005:
                errors.append(
                    f"Scorecard.{category}.weighted must equal score × weight"
                )
            weighted_total += weighted
        weights_sum = sum(
            _to_float(cell.get("weight")) or 0.0
            for cat in SCORECARD_CATEGORIES
            for cell in [scorecard.get(cat)]
            if isinstance(cell, dict)
        )
        if abs(weights_sum - 1.0) > 0.001:
            errors.append(
                f"Scorecard weights must sum to 1.0 (found {weights_sum:.4f})"
            )
        overall = scorecard.get("Overall")
        if not isinstance(overall, dict) or _to_float(overall.get("score")) is None:
            errors.append("Scorecard.Overall requires a numeric score")
        elif abs(_to_float(overall["score"]) - weighted_total) > 0.05:
            errors.append(
                "Scorecard.Overall.score must equal the sum of weighted category scores"
            )
        if not _in_enum(
            overall.get("rating_band") if isinstance(overall, dict) else None,
            ("Buy", "Hold", "Sell"),
        ):
            errors.append("Scorecard.Overall.rating_band must be Buy|Hold|Sell")
    swot = artifacts.get("SWOT")
    if not isinstance(swot, dict):
        errors.append("SWOT must be an object")
    else:
        for key in ("Strengths", "Weaknesses", "Opportunities", "Threats", "Watchlist"):
            if not isinstance(swot.get(key), list):
                errors.append(f"SWOT.{key} must be a list")
    glossary = artifacts.get("Glossary")
    if not isinstance(glossary, list) or not glossary:
        errors.append("Glossary must be a non-empty list")
    else:
        for index, entry in enumerate(glossary, start=1):
            if (
                not isinstance(entry, dict)
                or not _is_text(entry.get("term"))
                or not _is_text(entry.get("definition"))
            ):
                errors.append(f"Glossary[{index}] requires term and definition strings")
    fields = artifacts.get("comparison_fields")
    if not isinstance(fields, dict):
        errors.append("comparison_fields must be an object")
    else:
        for key in COMPARISON_FIELD_KEYS:
            if key not in fields:
                errors.append(f"comparison_fields.{key} is required")
            elif fields[key] is not None and not _is_num(fields[key]):
                errors.append(f"comparison_fields.{key} must be numeric or null")
    return errors


_STRUCTURAL_CHECKS = {
    "A1": _check_a1,
    "A2": _check_a2,
    "A3": _check_a3,
    "A4": _check_a4,
    "A5": _check_a5,
    "A6B": _check_bull_case,
    "A7": _check_a7,
}


def _collect_bull_ids(
    envelope: Envelope, envelopes: Mapping[str, Envelope]
) -> Optional[Set[str]]:
    candidates = []
    if envelope.agent.id == "A6B":
        candidates.append(envelope)
    if "A6B" in envelopes:
        candidates.append(envelopes["A6B"])
    for source in candidates:
        cases = source.artifacts.get("BullCase")
        if isinstance(cases, list) and cases:
            ids = {
                str(case.get("id"))
                for case in cases
                if isinstance(case, dict) and case.get("id")
            }
            if ids:
                return ids
    return None


def _collect_rebuttals(envelope: Envelope, envelopes: Mapping[str, Envelope]) -> Any:
    if envelope.agent.id == "A6R":
        return envelope.artifacts.get("BearRebuttals")
    source = envelopes.get("A6R")
    return source.artifacts.get("BearRebuttals") if source else None


def validate_sections(
    envelope: Envelope, depth_config: Optional[Mapping[str, Any]] = None
) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    agent_id = envelope.agent.id
    assigned = set(SECTION_CONTRACTS.get(agent_id, []))
    optional = set(OPTIONAL_SECTIONS.get(agent_id, set()))
    depth = depth_config or {}
    skipped = {
        int(n)
        for n in depth.get("skip_sections", [])
        if str(n).strip().lstrip("-").isdigit()
    }
    if agent_id == "A7" and depth.get("require_lay_summary") is False:
        assigned.discard("SECTION_18_LAY_SUMMARY")

    for key in assigned:
        number = _section_number(key)
        if number is not None and number in skipped:
            continue
        if not _is_text(envelope.sections_md.get(key)):
            errors.append(f"missing required section '{key}'")

    allowed = assigned | optional
    for key, text in envelope.sections_md.items():
        number = _section_number(key)
        if number is not None and number in skipped:
            continue
        if key not in allowed:
            warnings.append(
                f"section '{key}' is not part of agent {agent_id}'s contract"
            )
        # shortcut: only same-sentence proof claims are checked; review other prose separately.
        for sentence in re.split(r"\n|(?<=[.!?])\s+", str(text)):
            if not re.search(r"\b(?:beneish|altman)\b", sentence, re.IGNORECASE):
                continue
            proof = re.search(r"\b(?:verify|verifies|verified|prove|proves|confirm|confirms|establish|establishes)\b", sentence, re.IGNORECASE)
            claim = re.search(r"(?:balance\s+sheet|financial\s+statements?|accounts?|accounting).{0,35}integrity|(?:absence|no|free|without).{0,35}(?:fraud|manipulation)", sentence, re.IGNORECASE)
            if proof and claim and not re.search(r"\b(?:not|never|cannot|can't|doesn't|don't|isn't|neither|without)\b", sentence[max(0, proof.start() - 35):proof.start()], re.IGNORECASE):
                errors.append(f"section '{key}' overstates what forensic scores can prove")
                break
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _collect_embedded_witnesses(
    data: Any,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 10,
    errors: Optional[List[str]] = None,
) -> List[Tuple[str, Any]]:
    """Recursively collect embedded calculation witnesses with their location paths."""
    witnesses: List[Tuple[str, Any]] = []
    if depth > max_depth:
        if isinstance(data, (Mapping, list, CalculationWitness)):
            if errors is not None:
                loc = prefix or "artifacts"
                err_msg = f"{loc}: scan depth limit exceeded ({max_depth})"
                if err_msg not in errors:
                    errors.append(err_msg)
        return witnesses

    if isinstance(data, CalculationWitness):
        witnesses.append((prefix or "witness", data))
        return witnesses

    if isinstance(data, Mapping):
        if "calculation_witness" in data:
            cw_loc = f"{prefix}.calculation_witness" if prefix else "calculation_witness"
            witnesses.append((cw_loc, data["calculation_witness"]))
            for key, val in data.items():
                if key != "calculation_witness":
                    loc = f"{prefix}.{key}" if prefix else str(key)
                    witnesses.extend(
                        _collect_embedded_witnesses(
                            val, prefix=loc, depth=depth + 1, max_depth=max_depth, errors=errors
                        )
                    )
        elif (
            len({"formula_id", "calculator_id", "provenance"}.intersection(data)) >= 2
            and any(key in data for key in ("formula_id", "inputs", "result"))
        ):
            # Recognize witnesses before validating metadata; a missing field
            # must not hide them. Metric containers without witness fields are
            # not direct witnesses (e.g. legacy Beneish indices/raw_score).
            witnesses.append((prefix or "witness", data))
        else:
            for key, val in data.items():
                loc = f"{prefix}.{key}" if prefix else str(key)
                witnesses.extend(
                    _collect_embedded_witnesses(
                        val, prefix=loc, depth=depth + 1, max_depth=max_depth, errors=errors
                    )
                )
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            item_loc = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            witnesses.extend(
                _collect_embedded_witnesses(
                    item, prefix=item_loc, depth=depth + 1, max_depth=max_depth, errors=errors
                )
            )

    return witnesses


def validate_contract(
    envelope: Envelope,
    *,
    depth_config: Optional[Mapping[str, Any]] = None,
    envelopes: Optional[Mapping[str, Envelope]] = None,
    strict: bool = False,
    fact_ledger: Optional[Mapping[str, Any]] = None,
    expected_company: Optional[str] = None,
    cutoff: Optional[Union[str, date, datetime]] = None,
) -> ValidationResult:
    """
    Validate an envelope against its machine-checkable contract.

    ``envelopes`` provides already-accepted upstream envelopes for cross-agent
    checks (bear/bull linkage).
    """
    errors: List[str] = []
    warnings: List[str] = []
    strict_exempt_warnings: List[str] = []
    agent_id = envelope.agent.id
    upstream = envelopes or {}

    missing = [
        key
        for key in REQUIRED_ARTIFACTS.get(agent_id, [])
        if key not in envelope.artifacts
    ]
    if missing:
        errors.append("missing required artifacts: " + ", ".join(missing))

    try:
        data_gaps = missing_data_map(envelope.metrics.get("missing_data"))
    except ValueError as exc:
        data_gaps = {}
        errors.append(str(exc))
    allowed = ALLOWED_DATA_GAPS.get(agent_id, set())
    for path in data_gaps:
        if path not in allowed:
            errors.append(f"missing_data path {path!r} is not allowed for {agent_id}")
        elif not documented_null(envelope.artifacts, path, data_gaps):
            errors.append(f"missing_data.{path} requires an explicitly present null artifact value")
    valid_gaps = {path: reason for path, reason in data_gaps.items() if path in allowed}
    if valid_gaps:
        gap_warning = (
            "Explicit unavailable research inputs: " + ", ".join(sorted(valid_gaps))
        )
        warnings.append(gap_warning)
        strict_exempt_warnings.append(gap_warning)
    checker = _STRUCTURAL_CHECKS.get(agent_id)
    if checker is not None:
        errors.extend(checker(envelope.artifacts, valid_gaps) if agent_id in ("A2", "A3", "A4", "A5") else checker(envelope.artifacts))
    if strict and agent_id == "A3" and isinstance(envelope.artifacts.get("AltmanZ"), dict) and envelope.artifacts["AltmanZ"].get("score") is None and not documented_null(envelope.artifacts, "AltmanZ.score", valid_gaps):
        errors.append("AltmanZ.score=null requires a documented unavailable-data reason")
    if strict and agent_id == "A3" and isinstance(envelope.artifacts.get("BeneishM"), dict) and envelope.artifacts["BeneishM"].get("provenance") == PROVENANCE_MODEL_REPORTED:
        errors.append(
            "strict mode: BeneishM score is model-reported with no index ratios; "
            "supply BeneishM.indices for deterministic recomputation, or record the "
            "score as unavailable"
        )
    if strict and agent_id == "A3" and isinstance(envelope.artifacts.get("BeneishM"), dict):
        bm = envelope.artifacts["BeneishM"]
        if bm.get("provenance") not in ("deterministic", PROVENANCE_MODEL_REPORTED, "unavailable") and bm.get("score") is not None:
            errors.append(
                "strict mode: BeneishM carries an unlabelled legacy score; supply "
                "BeneishM.indices for deterministic recomputation, or mark it unavailable"
            )
    if strict and agent_id == "A7" and isinstance(envelope.artifacts.get("comparison_fields"), dict):
        for key, value in envelope.artifacts["comparison_fields"].items():
            if value is None and not documented_null(envelope.artifacts, f"comparison_fields.{key}", valid_gaps):
                errors.append(f"comparison_fields.{key}=null requires a documented unavailable-data reason")

    if agent_id == "A6":
        bull_ids = _collect_bull_ids(envelope, upstream)
        rebuttals = _collect_rebuttals(envelope, upstream)
        errors.extend(_check_a6(envelope.artifacts, bull_ids, rebuttals))

    if agent_id == "A6R":
        bull_ids = _collect_bull_ids(envelope, upstream)
        errors.extend(
            _check_bear_rebuttals(envelope.artifacts.get("BearRebuttals"), bull_ids)
        )

    if agent_id == "A3":
        # An unlabelled legacy Beneish score is preserved but flagged, so a
        # hand-authored controller envelope keeps its identity while readers
        # know it was never recomputed.
        _, beneish_warnings = _check_beneish(
            envelope.artifacts.get("BeneishM"), envelope.artifacts, valid_gaps
        )
        warnings.extend(beneish_warnings)

    sections = validate_sections(envelope, depth_config)
    errors.extend(sections.errors)
    warnings.extend(sections.warnings)

    effective_company = (
        expected_company
        or (envelope.params.get("ticker") if isinstance(envelope.params, Mapping) else None)
        or (envelope.params.get("company") if isinstance(envelope.params, Mapping) else None)
    )
    effective_cutoff = (
        cutoff
        or (envelope.params.get("analysis_date") if isinstance(envelope.params, Mapping) else None)
        or (envelope.params.get("cutoff") if isinstance(envelope.params, Mapping) else None)
    )

    depth_errors: List[str] = []
    embedded_witnesses = _collect_embedded_witnesses(
        envelope.artifacts, errors=depth_errors
    )
    for err in depth_errors:
        errors.append(err)
    for loc, witness in embedded_witnesses:
        w_errors = validate_witness(
            witness,
            fact_ledger=fact_ledger,
            expected_company=effective_company,
            cutoff=effective_cutoff,
        )
        is_model_reported = (
            (isinstance(witness, CalculationWitness) and witness.provenance == PROVENANCE_MODEL_REPORTED)
            or (isinstance(witness, Mapping) and witness.get("provenance") == PROVENANCE_MODEL_REPORTED)
        )
        for err in w_errors:
            if (not strict and is_model_reported
                    and err.startswith(f"witness claims provenance '{PROVENANCE_MODEL_REPORTED}';")):
                warnings.append(f"{loc}: {err}")
            else:
                errors.append(f"{loc}: {err}")

    if envelope.metrics.get("source_grounding") == "unverified" or any(source.get("verification") == "unverified" for source in envelope.sources):
        message = "source grounding is unverified: no runner-retrieved document evidence"
        if strict:
            errors.append("strict mode: " + message)
        else:
            warnings.append(message)
    if envelope.metrics.get("unverified_source_urls"):
        message = "source citations include URLs outside the retrieved evidence pack"
        if strict:
            errors.append("strict mode: " + message)
        else:
            warnings.append(message)

    injected = [str(key) for key in (envelope.metrics.get("injected_defaults") or [])]
    if injected:
        message = "normalization injected defaults for: " + ", ".join(injected)
        if strict:
            errors.append("strict mode: " + message)
        else:
            warnings.append(message)

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        strict_exempt_warnings=strict_exempt_warnings,
    )
