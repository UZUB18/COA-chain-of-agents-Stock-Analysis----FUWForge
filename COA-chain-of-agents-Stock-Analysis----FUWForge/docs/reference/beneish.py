"""Eight-variable Beneish arithmetic; screening is not proof of manipulation.

Published coefficients: ECB Occasional Paper 385 (2026), Equation 2, page 10.
All inputs must be supplied. Applicability, input derivations and classification
threshold policy belong to the caller and must be recorded with their sources.
"""
from __future__ import annotations
import math


def beneish_score(*, dsri: float, gmi: float, aqi: float, sgi: float,
                  depi: float, sgai: float, lvgi: float, tata: float) -> float:
    inputs = (dsri, gmi, aqi, sgi, depi, sgai, lvgi, tata)
    for value in inputs:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('all eight inputs must be explicit finite numbers')
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('all eight inputs must be explicit finite numbers')
    result = math.fsum((
        -4.84, 0.920 * dsri, 0.528 * gmi, 0.404 * aqi,
        0.892 * sgi, 0.115 * depi, -0.172 * sgai,
        -0.327 * lvgi, 4.679 * tata,
    ))
    if not math.isfinite(result):
        raise ValueError('score exceeds the supported numeric range')
    return result  # Classify the unrounded score; round only for presentation.
