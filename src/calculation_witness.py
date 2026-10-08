"""Calculation witnesses: what arithmetic produced a reported number.

A number in an artifact is only reviewable if the run records *how* it was
produced. A witness pins:

- the formula identity and version (so a corrected formula cannot silently
  pass as the old one),
- the input values, with their units and, where known, the typed fact IDs the
  inputs came from,
- the parameters and the unrounded result.

Two rules make this useful rather than decorative:

1. A witness is a claim about provenance, not proof of correctness. Only code
   that actually computed the value may write ``computed_by = "deterministic"``.
2. A result whose inputs are absent is not given a fabricated neutral value; it
   is recorded as unavailable with a reason, or explicitly attributed to the
   model as unchecked.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

from src.fact_binding import binding_failures, verify_witness_bindings

#: The only accepted provenance for a value produced by local arithmetic.
COMPUTED_BY_DETERMINISTIC = "deterministic"
#: A number that came from a language model with no recomputation behind it.
#: It is preserved and labelled, never silently promoted to a computed result.
PROVENANCE_MODEL_REPORTED = "model_reported_unverified"


@dataclass(frozen=True)
class CalculationWitness:
    """Immutable record of one arithmetic derivation."""

    formula_id: str
    formula_version: str
    calculator_id: str
    result: Optional[float]
    inputs: Mapping[str, Any] = field(default_factory=dict)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    input_fact_ids: Mapping[str, str] = field(default_factory=dict)
    units: Mapping[str, str] = field(default_factory=dict)
    provenance: str = COMPUTED_BY_DETERMINISTIC
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def beneish_witness(
    *,
    indices: Mapping[str, Any],
    fact_ids: Optional[Mapping[str, str]] = None,
    parameters: Optional[Mapping[str, Any]] = None,
    result: Optional[float] = None,
    formula_source: str = "",
) -> CalculationWitness:
    """
    Build a witness for a Beneish result.

    ``result`` must be the unrounded value produced by the deterministic
    calculator. If any index is missing or non-numeric the caller should use
    :func:`unavailable_beneish_witness` instead of filling in a neutral ratio.
    """
    from src.utils.beneish import (
        BENEISH_FORMULA_SOURCE,
        BENEISH_SCREENING_THRESHOLD,
        CALCULATOR_ID,
        CALCULATOR_VERSION,
    )

    return CalculationWitness(
        formula_id="beneish_m_score",
        formula_version=CALCULATOR_VERSION,
        calculator_id=CALCULATOR_ID,
        result=result,
        inputs=dict(indices),
        parameters={
            "threshold_policy": "ecb-385-eq2-conventional",
            "screening_threshold": BENEISH_SCREENING_THRESHOLD,
            "formula_source": formula_source or BENEISH_FORMULA_SOURCE,
            **(dict(parameters) if parameters else {}),
        },
        input_fact_ids=dict(fact_ids) if fact_ids else {},
        units={
            "dsri": "ratio",
            "gmi": "ratio",
            "aqi": "ratio",
            "sgi": "ratio",
            "depi": "ratio",
            "sgai": "ratio",
            "lvgi": "ratio",
            "tata": "ratio",
        },
        provenance=COMPUTED_BY_DETERMINISTIC,
        notes="Unrounded score; display rounding is a separate presentation step.",
    )


def unavailable_beneish_witness(reason: str) -> CalculationWitness:
    """Witness for an explicitly unavailable Beneish result (no fabrication)."""
    from src.utils.beneish import CALCULATOR_ID, CALCULATOR_VERSION

    return CalculationWitness(
        formula_id="beneish_m_score",
        formula_version=CALCULATOR_VERSION,
        calculator_id=CALCULATOR_ID,
        result=None,
        inputs={},
        parameters={"threshold_policy": "ecb-385-eq2-conventional"},
        provenance="unavailable",
        notes=reason,
    )


def witness_to_witness_dict(witness: CalculationWitness) -> Dict[str, Any]:
    """Serialize a witness for embedding in artifacts."""
    return witness.to_dict()


def summarize_witnesses(witnesses: List[CalculationWitness]) -> Dict[str, Any]:
    """Small aggregate used in run manifests."""
    return {
        "count": len(witnesses),
        "deterministic": sum(
            1 for w in witnesses if w.provenance == COMPUTED_BY_DETERMINISTIC
        ),
        "unavailable": sum(1 for w in witnesses if w.provenance == "unavailable"),
        "model_reported": sum(
            1 for w in witnesses if w.provenance == PROVENANCE_MODEL_REPORTED
        ),
    }


def validate_witness(
    witness: Union[CalculationWitness, Dict[str, Any]],
    *,
    fact_ledger: Optional[Mapping[str, Any]] = None,
    cutoff: Optional[str] = None,
    expected_company: Optional[str] = None,
) -> List[str]:
    """
    Validate a calculation witness for typed structure, provenance, arithmetic,
    and source binding.

    Source binding is the load-bearing part: an input that cites a fact is
    verified against that fact's *actual* value, unit, period, accounting basis,
    filing identity and point-in-time availability under the documented
    numeric-comparison policy in :mod:`src.fact_binding`. An existing fact ID is
    not sufficient; the number must equal the fact's number.

    An input with no cited fact is reported as unverified rather than assumed
    correct.
    """
    errors: List[str] = []
    if isinstance(witness, CalculationWitness):
        data = witness.to_dict()
    elif isinstance(witness, dict):
        data = dict(witness)
    else:
        return ["calculation witness must be a dictionary or CalculationWitness instance"]

    formula_id = data.get("formula_id")
    formula_version = data.get("formula_version")
    calculator_id = data.get("calculator_id")
    provenance = data.get("provenance")

    if not formula_id or not isinstance(formula_id, str):
        errors.append("witness missing or non-string 'formula_id'")
    if not formula_version or not isinstance(formula_version, str):
        errors.append("witness missing or non-string 'formula_version'")
    if not calculator_id or not isinstance(calculator_id, str):
        errors.append("witness missing or non-string 'calculator_id'")
    if not provenance or not isinstance(provenance, str):
        errors.append("witness missing or non-string 'provenance'")

    if provenance == PROVENANCE_MODEL_REPORTED:
        errors.append(
            f"witness claims provenance '{PROVENANCE_MODEL_REPORTED}'; arithmetic is not independently verified"
        )
        return errors

    if provenance == "unavailable":
        if data.get("result") is not None:
            errors.append("witness with provenance 'unavailable' must have result=None")
        if not data.get("notes") or not str(data.get("notes")).strip():
            errors.append("witness with provenance 'unavailable' requires documented notes/reason")
        return errors

    if provenance != COMPUTED_BY_DETERMINISTIC:
        errors.append(
            f"unknown witness provenance '{provenance}'; expected '{COMPUTED_BY_DETERMINISTIC}', 'unavailable', or '{PROVENANCE_MODEL_REPORTED}'"
        )
        return errors

    # For deterministic calculations:
    result = data.get("result")
    if result is None or isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result):
        errors.append("deterministic witness requires a finite numeric result")

    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        errors.append("deterministic witness requires a dictionary of inputs")
        inputs = {}

    for k, v in inputs.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"witness input '{k}' must be a finite number, got {v!r}")

    # Source binding. Every input that cites a fact must match that fact's
    # actual recorded value, unit, period, basis, filing identity and
    # availability. This is delegated to the shared policy in
    # ``src.fact_binding`` so the witness validator and the independent run
    # audit can never drift apart.
    if isinstance(input_fact_ids, dict):
        bindings = verify_witness_bindings(
            data,
            fact_ledger=fact_ledger or {},
            expected_company=expected_company,
            cutoff=cutoff,
            expected_periods=data.get("input_periods"),
            expected_bases=data.get("input_bases"),
        )
        # Only genuine binding failures are errors. An input that cites no fact
        # is disclosed as unverified, not treated as a false claim.
        errors.extend(binding_failures(bindings))

    # Formula-specific replay if known
    if formula_id == "beneish_m_score":
        from src.utils.beneish import BENEISH_INPUT_KEYS, beneish_score
        missing = [k for k in BENEISH_INPUT_KEYS if k not in inputs]
        if missing:
            errors.append("Beneish witness missing index inputs: " + ", ".join(missing))
        else:
            try:
                calc_val = beneish_score(**{k: float(inputs[k]) for k in BENEISH_INPUT_KEYS})
                if result is not None and isinstance(result, (int, float)) and not isinstance(result, bool):
                    if abs(float(result) - calc_val) > 1e-9:
                        errors.append(
                            f"Beneish arithmetic mismatch: recorded result {result!r} != recomputed {calc_val!r}"
                        )
            except Exception as exc:
                errors.append(f"Beneish recomputation failed: {exc}")

    return errors
