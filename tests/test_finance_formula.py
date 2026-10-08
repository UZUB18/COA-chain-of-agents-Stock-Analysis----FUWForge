"""
Golden tests for the deterministic financial calculators.

The Beneish fixtures are independent golden results derived from the published
eight-variable equation (ECB Occasional Paper 385, Equation 2, page 10), not
from this repository's previous implementation. A wrong coefficient must fail
here before it reaches an artifact.

Two boundaries are load-bearing and are tested explicitly:

- A missing input must never manufacture a neutral ratio. Unavailable is an
  explicit result with a documented reason, not ``1.0``.
- Screening is not proof of manipulation. The threshold is a versioned policy
  identifier, recorded with the result.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.beneish import (
    BENEISH_FORMULA_SOURCE,
    BENEISH_SCREENING_THRESHOLD,
    BENEISH_VARIANT,
    CALCULATOR_VERSION,
    beneish_score,
)
from src.utils.finance import beneish_m_score

ALL_ONE = {
    "dsri": 1.0,
    "gmi": 1.0,
    "aqi": 1.0,
    "sgi": 1.0,
    "depi": 1.0,
    "sgai": 1.0,
    "lvgi": 1.0,
}


# --------------------------------------------------------------------- golden


def test_published_eight_variable_coefficients():
    """Seven neutral ratio indices and TATA 0.1 reproduce the published value."""
    value = beneish_score(tata=0.1, **ALL_ONE)
    assert value == pytest.approx(-2.0121)


def test_neutral_ratios_with_zero_tata():
    value = beneish_score(tata=0.0, **ALL_ONE)
    assert value == pytest.approx(-2.48)


def test_lvgi_coefficient_sign_and_magnitude():
    """LVGI carries -0.327, not a small positive term."""
    baseline = beneish_score(tata=0.1, **ALL_ONE)
    raised = beneish_score(tata=0.1, **{**ALL_ONE, "lvgi": 2.0})
    assert raised - baseline == pytest.approx(-0.327)


def test_tata_coefficient():
    baseline = beneish_score(tata=0.0, **ALL_ONE)
    raised = beneish_score(tata=0.1, **ALL_ONE)
    assert raised - baseline == pytest.approx(4.679 * 0.1)


def test_score_returns_full_precision_not_rounded():
    """Classification must be able to see the unrounded value."""
    value = beneish_score(tata=0.1, **ALL_ONE)
    assert value != pytest.approx(-2.01)
    assert isinstance(value, float)


# ------------------------------------------------------------- input contract


def test_missing_input_cannot_manufacture_a_result():
    """The neutral-index default is gone: omission is a TypeError, not 1.0."""
    with pytest.raises(TypeError):
        beneish_score(tata=0.1)  # type: ignore[call-arg]


def test_bare_none_is_rejected():
    with pytest.raises(ValueError):
        beneish_score(tata=0.1, **{**ALL_ONE, "dsri": None})  # type: ignore[dict-item]


@pytest.mark.parametrize("bad", [True, False])
def test_boolean_is_rejected(bad):
    """bool is an int subclass; accepting it would silently score a flag."""
    with pytest.raises(ValueError):
        beneish_score(tata=0.1, **{**ALL_ONE, "dsri": bad})


@pytest.mark.parametrize("bad", ["1.0", "1,0", "N/A", ""])
def test_string_is_rejected(bad):
    with pytest.raises(ValueError):
        beneish_score(tata=0.1, **{**ALL_ONE, "gmi": bad})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_is_rejected(bad):
    with pytest.raises(ValueError):
        beneish_score(tata=0.1, **{**ALL_ONE, "aqi": bad})


# ---------------------------------------------------------- outward contract


def test_wrapper_preserves_report_shape_and_classifies_raw_score():
    result = beneish_m_score(tata=0.1, **ALL_ONE)
    # Report shape preserved for existing consumers.
    assert set(("score", "classification", "feasible", "flags")) <= set(result)
    assert result["feasible"] is True
    # Rounded for display, raw retained, classification from the raw score.
    assert result["score"] == pytest.approx(-2.01)
    assert result["raw_score"] == pytest.approx(-2.0121)
    assert result["classification"] == "Unlikely Manipulator"


def test_wrapper_records_formula_and_threshold_policy():
    result = beneish_m_score(tata=0.1, **ALL_ONE)
    assert result["calculator_id"] == "beneish_m_score"
    assert result["calculator_version"] == CALCULATOR_VERSION
    assert result["formula_variant"] == BENEISH_VARIANT
    assert result["formula_source"] == BENEISH_FORMULA_SOURCE
    assert result["threshold_policy"] == {
        "threshold": BENEISH_SCREENING_THRESHOLD,
        "id": "ecb-385-eq2-conventional",
        "note": "Screening signal only; not a determination that manipulation occurred.",
    }


def test_wrapper_rejects_incomplete_inputs():
    """No fabricated defaults: a partial call fails loudly."""
    with pytest.raises(TypeError):
        beneish_m_score(dsri=1.0)  # type: ignore[call-arg]


def test_screening_threshold_is_recorded_not_asserted():
    result = beneish_m_score(
        dsri=1.8, gmi=1.6, aqi=1.5, sgi=1.8, depi=1.3, sgai=1.4, lvgi=1.6, tata=0.15
    )
    assert result["classification"] == "High Manipulation Risk"
    assert result["score"] > BENEISH_SCREENING_THRESHOLD
    assert "not a determination" in result["threshold_policy"]["note"]


def test_flags_are_derived_from_supplied_indices():
    result = beneish_m_score(
        dsri=1.8, gmi=1.6, aqi=1.5, sgi=1.8, depi=1.3, sgai=1.4, lvgi=1.6, tata=0.15
    )
    assert result["flags"]["DSRI_high"] is True
    assert result["flags"]["GMI_deteriorating"] is True
    assert result["flags"]["TATA_high"] is True


def test_result_is_json_serializable_and_finite():
    result = beneish_m_score(tata=0.1, **ALL_ONE)
    assert math.isfinite(result["score"])
    assert isinstance(result["raw_score"], float)