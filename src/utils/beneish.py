"""Eight-variable Beneish arithmetic; screening is not proof of manipulation.

Published coefficients: ECB Occasional Paper 385, Equation 2, page 10.

Corrections relative to the previous in-repo helper, both confirmed against the
published equation rather than against prior behaviour:

- ``TATA`` carries ``+4.679`` (was ``+4.037``).
- ``LVGI`` carries ``-0.327`` (was ``+0.0327``): the sign and magnitude both
  differ. Holding other inputs fixed, increasing LVGI lowers this equation's
  score; this coefficient alone is not a causal interpretation of leverage risk.

The neutral-index defaults of the old wrapper were removed as well. A function
signature that supplies ``1.0`` for an unknown input fabricates a ratio and
quietly produces a plausible-looking score. Every input is now explicit; an
unknown input is an unavailable result at the orchestration boundary, never a
neutral value.

``math.fsum`` is used so the score does not depend on summation order.

Boundaries stated plainly:

- Applicability, the derivation of each index, and the interpretation of the
  screening threshold belong to the caller and must be recorded with sources.
- Crossing the threshold is a screening signal. It is not a finding that
  earnings manipulation occurred, and this module does not make one.
- Hash integrity elsewhere in the system proves which bytes were supplied; it
  proves nothing about whether the inputs were the right ones.
"""

from __future__ import annotations

import math
from typing import Any, Dict

#: Stable identity for this calculator, recorded in artifacts and witnesses.
CALCULATOR_ID = "beneish_m_score"
CALCULATOR_VERSION = "2.0.0"
BENEISH_VARIANT = "m-score-8-variable"
BENEISH_FORMULA_SOURCE = (
    "ECB Occasional Paper 385, Equation 2, p.10 (eight-variable Beneish M-Score)"
)
BENEISH_SCREENING_THRESHOLD = -1.78

#: Coeffs are frozen in module constants so a witness can record them verbatim
#: and a reviewer can diff them against the published equation.
BENEISH_COEFFICIENTS: Dict[str, float] = {
    "intercept": -4.84,
    "dsri": 0.920,
    "gmi": 0.528,
    "aqi": 0.404,
    "sgi": 0.892,
    "depi": 0.115,
    "sgai": -0.172,
    "lvgi": -0.327,
    "tata": 4.679,
}

BENEISH_INPUT_KEYS = (
    "dsri",
    "gmi",
    "aqi",
    "sgi",
    "depi",
    "sgai",
    "lvgi",
    "tata",
)

THRESHOLD_POLICY = {
    "id": "ecb-385-eq2-conventional",
    "threshold": BENEISH_SCREENING_THRESHOLD,
    "note": "Screening signal only; not a determination that manipulation occurred.",
}


def _require_finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{label} must be an explicit finite number; a missing input is an "
            "unavailable result, not a neutral ratio"
        )
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be an explicit finite number")
    return float(value)


def beneish_score(
    *,
    dsri: float,
    gmi: float,
    aqi: float,
    sgi: float,
    depi: float,
    sgai: float,
    lvgi: float,
    tata: float,
) -> float:
    """
    Return the unrounded eight-variable Beneish M-Score.

    All eight indices are keyword-only and required. Returns full precision;
    classification belongs to the caller and must use this unrounded value.
    """
    values = {
        "dsri": _require_finite(dsri, "dsri"),
        "gmi": _require_finite(gmi, "gmi"),
        "aqi": _require_finite(aqi, "aqi"),
        "sgi": _require_finite(sgi, "sgi"),
        "depi": _require_finite(depi, "depi"),
        "sgai": _require_finite(sgai, "sgai"),
        "lvgi": _require_finite(lvgi, "lvgi"),
        "tata": _require_finite(tata, "tata"),
    }
    result = math.fsum(
        (
            BENEISH_COEFFICIENTS["intercept"],
            BENEISH_COEFFICIENTS["dsri"] * values["dsri"],
            BENEISH_COEFFICIENTS["gmi"] * values["gmi"],
            BENEISH_COEFFICIENTS["aqi"] * values["aqi"],
            BENEISH_COEFFICIENTS["sgi"] * values["sgi"],
            BENEISH_COEFFICIENTS["depi"] * values["depi"],
            BENEISH_COEFFICIENTS["sgai"] * values["sgai"],
            BENEISH_COEFFICIENTS["lvgi"] * values["lvgi"],
            BENEISH_COEFFICIENTS["tata"] * values["tata"],
        )
    )
    if not math.isfinite(result):
        raise ValueError("score exceeds the supported numeric range")
    return result  # Classify the unrounded score; round only for presentation.


def classify(score: float) -> str:
    """Classify an unrounded score under the recorded threshold policy."""
    _require_finite(score, "score")
    if score > BENEISH_SCREENING_THRESHOLD:
        return "High Manipulation Risk"
    return "Unlikely Manipulator"


def unavailable_beneish(reason: str) -> Dict[str, Any]:
    """
    Explicit unavailable result.

    Replaces the old behaviour of substituting neutral indices and reporting a
    fabricated score. The score stays unknown; only the reason is supplied.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("an unavailable Beneish result requires a documented reason")
    return {
        "score": None,
        "raw_score": None,
        "classification": "Unavailable",
        "feasible": False,
        "unavailable_reason": reason.strip(),
        "flags": {},
        "calculator_id": CALCULATOR_ID,
        "calculator_version": CALCULATOR_VERSION,
        "formula_variant": BENEISH_VARIANT,
        "formula_source": BENEISH_FORMULA_SOURCE,
        "threshold_policy": dict(THRESHOLD_POLICY),
    }


def beneish_result(**indices: Any) -> Dict[str, Any]:
    """
    Artifact-shaped result: rounded display score, raw score, provenance.

    Accepts exactly the eight index keys. Any other key, or any missing index,
    is an error rather than a default.
    """
    unexpected = sorted(set(indices) - set(BENEISH_INPUT_KEYS))
    if unexpected:
        raise ValueError("unexpected Beneish inputs: " + ", ".join(unexpected))
    missing = sorted(set(BENEISH_INPUT_KEYS) - set(indices))
    if missing:
        raise ValueError(
            "missing Beneish inputs: "
            + ", ".join(missing)
            + " (use unavailable_beneish() to record an unknown result)"
        )

    raw = beneish_score(**indices)
    dsri = float(indices["dsri"])
    gmi = float(indices["gmi"])
    aqi = float(indices["aqi"])
    sgi = float(indices["sgi"])
    tata = float(indices["tata"])
    return {
        "score": round(raw, 2),
        "raw_score": raw,
        "classification": classify(raw),
        "feasible": True,
        "flags": {
            "DSRI_high": dsri > 1.3,
            "GMI_deteriorating": gmi > 1.2,
            "AQI_high": aqi > 1.25,
            "SGI_high": sgi > 1.3,
            "TATA_high": tata > 0.05,
        },
        "calculator_id": CALCULATOR_ID,
        "calculator_version": CALCULATOR_VERSION,
        "formula_variant": BENEISH_VARIANT,
        "formula_source": BENEISH_FORMULA_SOURCE,
        "threshold_policy": dict(THRESHOLD_POLICY),
    }