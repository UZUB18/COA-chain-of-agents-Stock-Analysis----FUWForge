"""Provider-neutral canonical envelope schema used in prompts and Gemini requests."""
from __future__ import annotations

from typing import Any, Dict

from src.contracts import REQUIRED_ARTIFACTS, SECTION_CONTRACTS
from src.data_gaps import ALLOWED_DATA_GAPS
from src.utils.finance import resolve_weights


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required}


def array(items):
    return {"type": "array", "items": items}


def nullable(shape):
    return {"anyOf": [shape, {"type": "null"}]}


TEXT = {"type": "string"}
NUMBER = {"type": ["number", "null"]}
BOOL = {"type": "boolean"}
TEXTS = array(TEXT)

ARTIFACT_SHAPES = {
    "price_timestamp_eet": TEXT, "shares_basic": NUMBER, "shares_diluted": NUMBER,
    "market_cap_check": obj({"price": NUMBER, "shares": NUMBER, "cap": NUMBER}),
    "PEER_LIST": nullable(TEXTS), "MoatTrend": {"type": "string", "enum": ["Expanding", "Stable", "Eroding", "Unclear"]},
    "TopCustomerPct": {"anyOf": [TEXT, obj({"percent": NUMBER, "note": TEXT}, required=["percent"]), {"type": "null"}]},
    "TopSupplierPct": {"anyOf": [TEXT, obj({"percent": NUMBER, "note": TEXT}, required=["percent"]), {"type": "null"}]},
    "AltmanZ": obj({"score": NUMBER, "classification": TEXT}),
    "BeneishM": obj({"score": NUMBER, "classification": TEXT, "feasible": BOOL}, required=["score", "classification"]),
    "OCF_vs_NI_Flag": {"type": "string", "enum": ["Aligned", "Divergent", "Negative"]},
    "PEG_TTM": NUMBER, "PEG_Fwd": NUMBER,
    "HypeFlag": nullable({"type": "string", "enum": ["Reality", "Balanced", "Hype Risk"]}),
    "PeerTable": nullable(array(obj({"ticker": TEXT, "PE": NUMBER, "PEG": NUMBER, "EV_EBITDA": NUMBER}))),
    "TechnicalSet": obj({"SMA_50": NUMBER, "SMA_200": NUMBER, "RSI_14": NUMBER}),
    "CEO_Pay_Ratio": NUMBER, "Auditor": TEXT, "AuditorTenure": TEXT, "GlassdoorScore": NUMBER,
    "AttritionNote": nullable(TEXT), "ESG_Momentum": {"type": "string", "enum": ["Improving", "Stable", "Deteriorating", "Unclear"]},
    "LowSampleSizeCultureData": BOOL,
    "SentimentSplit": nullable(obj({"pos": NUMBER, "neu": NUMBER, "neg": NUMBER, "lookback_days": NUMBER})),
    "SocialVsFundDivergence": nullable({"type": "string", "enum": ["Aligned", "Mild", "Pump Risk"]}),
    "MajorLitigation": nullable(TEXTS), "AuditorChangeYN": nullable({"type": "string", "enum": ["Yes", "No"]}),
    "InsiderNet12m": nullable(obj({"direction": {"type": "string", "enum": ["NetBuy", "NetSell", "Neutral"]}, "shares": NUMBER, "value": NUMBER})),
    "InstitutionalOwnershipTrend": nullable(obj({"direction": {"type": "string", "enum": ["Up", "Down", "Flat"]}, "delta_pct": NUMBER})),
    "RegulatoryWatchlist": nullable(TEXTS),
    "BullCase": array(obj({"id": TEXT, "thesis": TEXT, "evidence": TEXTS, "impact": TEXT})),
    "BearRebuttals": array(obj({"bull_id": TEXT, "probability": {"type": "string", "enum": ["High", "Med", "Low"]}, "severity": {"type": "string", "enum": ["High", "Med", "Low"]}, "counter": TEXT})),
    "DangerZoneDashboard": array(obj({"category": TEXT, "flag": {"type": "string", "enum": ["🟢", "🟡", "🔴"]}, "sources": TEXTS, "note": TEXT})),
    "ScenarioTable": obj({name: obj({key: TEXT for key in ("Revenue", "EPS", "FCFPerShare", "Drivers")}) for name in ("Bull", "Base", "Bear")}),
    "Catalysts": TEXTS, "Headwinds": TEXTS,
    "SWOT": obj({key: TEXTS for key in ("Strengths", "Weaknesses", "Opportunities", "Threats", "Watchlist")}),
    "Glossary": array(obj({"term": TEXT, "definition": TEXT})),
    "comparison_fields": obj({key: NUMBER for key in (
        "quality_score_10", "valuation_score_10", "expected_5y_irr_base_pct", "downside_case_5y_irr_pct",
        "fcf_yield_ttm_pct", "roic_ttm_pct", "net_debt_to_ebitda", "moat_score_10",
        "reinvestment_runway_score_10", "balance_sheet_score_10", "fragility_score_10",
        "evidence_quality_score_10", "data_completeness_score_10",
    )}),
}


def envelope_schema(agent_id: str, params: Dict[str, Any], depth_config: Dict[str, Any]) -> Dict[str, Any]:
    weights, _ = resolve_weights(params.get("weights"))
    artifacts = {key: ARTIFACT_SHAPES[key] for key in REQUIRED_ARTIFACTS[agent_id] if key != "Scorecard"}
    if agent_id == "A7":
        artifacts["Scorecard"] = obj({
            **{key: obj({"weight": NUMBER, "score": NUMBER, "weighted": NUMBER}) for key in weights},
            "Overall": obj({"score": NUMBER, "rating_band": {"type": "string", "enum": ["Buy", "Hold", "Sell"]}}),
        })
    skipped = set(depth_config.get("skip_sections", []))
    sections = [key for key in SECTION_CONTRACTS[agent_id] if not any(key.startswith(f"SECTION_{num}_") for num in skipped)]
    metrics = {"data_checksum": TEXT}
    allowed_gaps = sorted(ALLOWED_DATA_GAPS.get(agent_id, set()))
    if allowed_gaps:
        metrics["missing_data"] = array(obj({"field": {"type": "string", "enum": allowed_gaps}, "reason": TEXT}))
    return obj({
        "schema_version": {"type": "string", "enum": ["2.1"]},
        "agent": obj({"id": {"type": "string", "enum": [agent_id]}, "name": TEXT}),
        "gates": obj({"passed": BOOL, "notes": TEXTS}),
        "sections_md": obj({**{key: TEXT for key in sections}, "MISSING_DATA_TABLE": TEXT}, required=sections),
        "artifacts": obj(artifacts),
        "sources": array(obj({"url": TEXT})),
        "warnings": TEXTS,
        "metrics": obj(metrics),
        "confidence": NUMBER,
    })
