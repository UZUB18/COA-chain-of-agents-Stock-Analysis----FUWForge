"""Runner-computed Altman Z-score inputs from typed facts and verified market data.

The classic Altman (1968) public-manufacturer model needs five ratios over one
aligned annual period plus the market value of equity. Accounting operands come
from typed SEC facts already filtered to the analysis cutoff; market equity is
the verified completed-day close from a versioned market transform times the
latest reported shares outstanding.

Nothing here guesses. A missing fact, a mismatched period or currency, an
ambiguous share count, or a legacy evidence pack without a versioned market
snapshot yields an explicit unavailable result with its reason. The market
close is not a filed fact, so X4 is recorded with its evidence source and
disclosed as not fact-bound rather than presented as source-backed.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.forensic_inputs import (
    _basis_of,
    _get,
    _is_annual,
    _is_instant,
    _period_key,
    _period_spec,
    _rank,
    eval_expression,
)

CALCULATOR_ID = "altman_z_score"
CALCULATOR_VERSION = "1.0.0"
FORMULA_VARIANT = "altman-1968-public-manufacturer-5-variable"
COEFFICIENTS = {"X1_wc_ta": 1.2, "X2_re_ta": 1.4, "X3_ebit_ta": 3.3, "X4_mc_tl": 0.6, "X5_rev_ta": 0.999}
SAFE_ABOVE = 2.99
DISTRESS_BELOW = 1.81
INPUT_KEYS = tuple(COEFFICIENTS)

REVENUE_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
)
SHARE_CONCEPTS = ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")
_ALIGN_DAYS = 7

CONVENTIONS = {
    "variant": "Classic Altman (1968) five-variable model for public manufacturing/operating companies; "
               "applicability to financial companies is not checked.",
    "ebit": "EBIT is OperatingIncomeLoss for the annual period ending with the balance-sheet date.",
    "liabilities": "Total liabilities is Liabilities, or LiabilitiesAndStockholdersEquity minus "
                   "StockholdersEquity on the same date; never assets minus equity.",
    "market_equity": "Market value of equity is the last eligible completed daily close from the versioned "
                     "market history times the latest reported shares outstanding at the cutoff. Prices are "
                     "provider-adjusted, not a point-in-time corporate-action archive.",
    "thresholds": f"Classified on the unrounded score: > {SAFE_ABOVE} Safe Zone, {DISTRESS_BELOW}-{SAFE_ABOVE} "
                  f"Grey Zone, < {DISTRESS_BELOW} Distress Zone. A screening convention, not a finding.",
}


def classify(raw_score: float) -> str:
    if raw_score > SAFE_ABOVE:
        return "Safe Zone"
    if raw_score >= DISTRESS_BELOW:
        return "Grey Zone"
    return "Distress Zone"


def altman_score(inputs: Mapping[str, float]) -> float:
    """Unrounded score from the five ratios (math.fsum, no intermediate rounding)."""
    return math.fsum(COEFFICIENTS[key] * float(inputs[key]) for key in INPUT_KEYS)


def unavailable(reason: str) -> Dict[str, Any]:
    return {
        "feasible": False,
        "inputs": {},
        "unavailable_reason": reason,
        "calculator_id": CALCULATOR_ID,
        "calculator_version": CALCULATOR_VERSION,
        "conventions": dict(CONVENTIONS),
    }


def _currency(fact: Any) -> str:
    return str(_get(fact, "currency") or _get(fact, "unit") or "").upper()


def _days_apart(a: str, b: str) -> Optional[int]:
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return None


def _usable(fact: Any, concept: str) -> bool:
    return (
        str(_get(fact, "concept") or "") == concept
        and _get(fact, "value") is not None
        and not _basis_of(fact).endswith("OTHER")
        and str(getattr(_get(fact, "kind"), "value", _get(fact, "kind")) or "reported")
        in ("reported", "restatement")
        and not _get(fact, "dimensions")
    )


def _instant(ledger: Mapping[str, Any], concept: str, instant: str) -> Optional[Any]:
    found = [f for f in ledger.values() if _usable(f, concept) and _is_instant(f)
             and str(_get(f, "instant") or "") == instant]
    return max(found, key=_rank) if found else None


def _annual_ending_near(ledger: Mapping[str, Any], concepts: Tuple[str, ...], instant: str) -> Optional[Any]:
    for concept in concepts:
        found = []
        for f in ledger.values():
            if not (_usable(f, concept) and _is_annual(f)):
                continue
            gap = _days_apart(str(_get(f, "period_end") or ""), instant)
            if gap is not None and gap <= _ALIGN_DAYS:
                found.append(f)
        if found:
            return max(found, key=_rank)
    return None


def compute_altman_inputs(ledger: Mapping[str, Any], market: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Five Altman ratios and the unrounded score, or an explicit unavailable result.

    ``ledger`` maps fact id -> fact and must already be filtered to the cutoff.
    ``market`` is ``EvidencePack.market_snapshot()`` (versioned packs only).
    """
    if not market or not isinstance(market.get("last_close"), (int, float)) or isinstance(market.get("last_close"), bool):
        return unavailable(
            "No versioned completed-day market close in the evidence pack, so market value of equity "
            "cannot be computed from verified data."
        )
    close = float(market["last_close"])
    if not math.isfinite(close) or close <= 0:
        return unavailable("The verified market close is not a positive finite price.")

    instants = sorted(
        {str(_get(f, "instant")) for f in ledger.values() if _usable(f, "Assets") and _is_instant(f)},
        reverse=True,
    )
    reasons: List[str] = []
    for instant in instants:
        roles: Dict[str, Any] = {
            "total_assets": _instant(ledger, "Assets", instant),
            "current_assets": _instant(ledger, "AssetsCurrent", instant),
            "current_liabilities": _instant(ledger, "LiabilitiesCurrent", instant),
            "retained_earnings": _instant(ledger, "RetainedEarningsAccumulatedDeficit", instant),
            "ebit": _annual_ending_near(ledger, ("OperatingIncomeLoss",), instant),
            "revenue": _annual_ending_near(ledger, REVENUE_CONCEPTS, instant),
        }
        liabilities = _instant(ledger, "Liabilities", instant)
        liabilities_note = "Liabilities"
        if liabilities is None:
            lse = _instant(ledger, "LiabilitiesAndStockholdersEquity", instant)
            se = _instant(ledger, "StockholdersEquity", instant)
            if lse is not None and se is not None:
                roles["liabilities_and_equity"], roles["stockholders_equity"] = lse, se
                liabilities_note = "LiabilitiesAndStockholdersEquity - StockholdersEquity"
            else:
                roles["total_liabilities"] = None
        else:
            roles["total_liabilities"] = liabilities
        missing = [role for role, fact in roles.items() if fact is None]
        if missing:
            reasons.append(f"{instant}: missing {', '.join(sorted(missing))}")
            continue
        currencies = {_currency(f) for f in roles.values()}
        if len(currencies) != 1 or currencies != {"USD"}:
            reasons.append(f"{instant}: accounting facts are not all USD ({sorted(currencies)}); "
                           "the US market close is in USD")
            continue
        bases = {_basis_of(f) for f in roles.values()}
        if len(bases) != 1:
            reasons.append(f"{instant}: mixed accounting bases {sorted(bases)}")
            continue
        break
    else:
        detail = "; ".join(reasons[:3]) if reasons else "no total-assets balance sheet at the cutoff"
        return unavailable(f"No aligned annual balance sheet with every Altman input ({detail}).")

    shares = [f for concept in SHARE_CONCEPTS for f in ledger.values()
              if _usable(f, concept) and _is_instant(f)]
    if not shares:
        return unavailable("No reported shares-outstanding fact at the cutoff.")
    latest = max(str(_get(f, "instant")) for f in shares)
    latest_shares = [f for f in shares if str(_get(f, "instant")) == latest]
    if len({float(_get(f, "value")) for f in latest_shares}) != 1:
        return unavailable(f"Ambiguous shares outstanding on {latest} (several differing values, "
                           "e.g. share classes); not summed against one price.")
    share_fact = max(latest_shares, key=_rank)
    shares_value = float(_get(share_fact, "value"))
    if shares_value <= 0:
        return unavailable("Reported shares outstanding is not positive.")

    value = {role: float(_get(fact, "value")) for role, fact in roles.items()}
    if "total_liabilities" in roles:
        total_liabilities = value["total_liabilities"]
    else:
        total_liabilities = value["liabilities_and_equity"] - value["stockholders_equity"]
    if value["total_assets"] <= 0 or total_liabilities <= 0:
        return unavailable("Total assets and total liabilities must be positive.")
    market_cap = close * shares_value

    derivations: Dict[str, Any] = {}
    inputs: Dict[str, float] = {}
    fact_ids: Dict[str, str] = {}
    periods: Dict[str, Any] = {}
    bases: Dict[str, str] = {}

    def bind(key: str, expression: str, operands: List[Tuple[str, str]]) -> None:
        env = {name: value[role] for name, role in operands}
        inputs[key] = eval_expression(expression, env)
        ids = [str(_get(roles[role], "id")) for _, role in operands]
        derivations[key] = {"expression": expression, "operand_fact_ids": ids,
                            "operand_names": [name for name, _ in operands]}
        fact_ids[key] = ids[0]
        periods[key] = {_period_key(roles[role]): _period_spec(roles[role]) for _, role in operands}
        bases[key] = _basis_of(roles[operands[0][1]])

    bind("X1_wc_ta", "(ca - cl) / ta", [("ca", "current_assets"), ("cl", "current_liabilities"), ("ta", "total_assets")])
    bind("X2_re_ta", "re / ta", [("re", "retained_earnings"), ("ta", "total_assets")])
    bind("X3_ebit_ta", "ebit / ta", [("ebit", "ebit"), ("ta", "total_assets")])
    bind("X5_rev_ta", "rev / ta", [("rev", "revenue"), ("ta", "total_assets")])
    # X4 uses the market close, which is not a filed fact: no derivation, so the
    # audit discloses it as an unverified input instead of a bound one.
    inputs["X4_mc_tl"] = market_cap / total_liabilities

    raw = altman_score(inputs)
    if not math.isfinite(raw):
        return unavailable("The computed ratios did not produce a finite score.")
    return {
        "feasible": True,
        "inputs": inputs,
        "raw_score": raw,
        "fact_ids": fact_ids,
        "derivations": derivations,
        "periods": periods,
        "bases": bases,
        "balance_sheet_date": instant,
        "market_equity": {
            "close": close,
            "bar_date": market.get("last_bar_date"),
            "evidence_document_id": market.get("document_id"),
            "transform_version": market.get("transform_version"),
            "shares_outstanding": shares_value,
            "shares_fact_id": str(_get(share_fact, "id")),
            "shares_date": latest,
            "market_cap": market_cap,
            "total_liabilities": total_liabilities,
            "total_liabilities_source": liabilities_note,
            "liabilities_fact_ids": [str(_get(roles[r], "id")) for r in
                                     ("total_liabilities", "liabilities_and_equity", "stockholders_equity")
                                     if r in roles],
        },
        "calculator_id": CALCULATOR_ID,
        "calculator_version": CALCULATOR_VERSION,
        "conventions": dict(CONVENTIONS),
    }


def altman_inputs_for(ledger: Mapping[str, Any], market: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Like :func:`compute_altman_inputs` but never raises (an optional enrichment)."""
    try:
        return compute_altman_inputs(ledger, market)
    except Exception as exc:  # noqa: BLE001 - reported as an explicit gap
        return unavailable(f"Altman input computation failed locally ({type(exc).__name__}: {exc}).")


def altman_artifact(runner: Mapping[str, Any]) -> Dict[str, Any]:
    """The A3 ``AltmanZ`` artifact for a runner result (feasible or unavailable)."""
    from src.calculation_witness import CalculationWitness

    if not runner.get("feasible"):
        reason = str(runner.get("unavailable_reason") or "Altman inputs unavailable.")
        return {
            "score": None, "raw_score": None, "classification": "Unavailable", "feasible": False,
            "inputs": {}, "unavailable_reason": reason, "provenance": "unavailable",
            "calculator_id": CALCULATOR_ID, "calculator_version": CALCULATOR_VERSION,
            "calculation_witness": CalculationWitness(
                formula_id=CALCULATOR_ID, formula_version=CALCULATOR_VERSION, calculator_id=CALCULATOR_ID,
                result=None, provenance="unavailable", notes=reason,
            ).to_dict(),
        }
    inputs = {key: float(runner["inputs"][key]) for key in INPUT_KEYS}
    raw = altman_score(inputs)
    witness = CalculationWitness(
        formula_id=CALCULATOR_ID,
        formula_version=CALCULATOR_VERSION,
        calculator_id=CALCULATOR_ID,
        result=raw,
        inputs=inputs,
        parameters={
            "formula_variant": FORMULA_VARIANT,
            "coefficients": dict(COEFFICIENTS),
            "threshold_policy": {"safe_above": SAFE_ABOVE, "distress_below": DISTRESS_BELOW},
            "market_equity": dict(runner.get("market_equity") or {}),
            "balance_sheet_date": runner.get("balance_sheet_date"),
            "index_source": "runner_computed",
        },
        input_fact_ids=dict(runner.get("fact_ids") or {}),
        units={key: "ratio" for key in INPUT_KEYS},
        notes="Unrounded score; X4 uses the verified market close and is not bound to a filed fact.",
        derivations=dict(runner.get("derivations") or {}),
        input_periods=dict(runner.get("periods") or {}),
        input_bases=dict(runner.get("bases") or {}),
    ).to_dict()
    return {
        "score": round(raw, 2),
        "raw_score": raw,
        "classification": classify(raw),
        "feasible": True,
        "inputs": inputs,
        "provenance": "deterministic",
        "index_source": "runner_computed",
        "calculator_id": CALCULATOR_ID,
        "calculator_version": CALCULATOR_VERSION,
        "formula_variant": FORMULA_VARIANT,
        "conventions": dict(runner.get("conventions") or CONVENTIONS),
        "calculation_witness": witness,
    }


def replay_errors(witness: Mapping[str, Any]) -> List[str]:
    """Arithmetic replay of an Altman witness: exact score from its five inputs."""
    errors: List[str] = []
    inputs = witness.get("inputs")
    if not isinstance(inputs, Mapping) or any(key not in inputs for key in INPUT_KEYS):
        return ["Altman witness is missing one of its five ratio inputs"]
    try:
        raw = altman_score({key: float(inputs[key]) for key in INPUT_KEYS})
    except (TypeError, ValueError):
        return ["Altman witness inputs are not numeric"]
    result = witness.get("result")
    if isinstance(result, bool) or not isinstance(result, (int, float)) or float(result) != raw:
        errors.append(f"Altman arithmetic mismatch: recorded result {result!r} != recomputed {raw!r}")
    parameters = witness.get("parameters") or {}
    if parameters.get("coefficients") not in (None, COEFFICIENTS):
        errors.append("Altman witness coefficients differ from the versioned calculator")
    market = parameters.get("market_equity") if isinstance(parameters, Mapping) else None
    if isinstance(market, Mapping):
        try:
            mc = float(market["close"]) * float(market["shares_outstanding"])
            x4 = mc / float(market["total_liabilities"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            errors.append("Altman witness market-equity record is incomplete")
        else:
            if x4 != float(inputs["X4_mc_tl"]):
                errors.append("Altman X4 does not equal recorded close x shares / total liabilities")
    return errors
