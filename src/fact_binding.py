"""Bind a calculation's numeric inputs to the typed facts they claim to come from.

Why this module exists
----------------------

A calculation witness used to be able to name a *real* fact ID and carry an
*arbitrary* number. The arithmetic replay then recomputed the score faithfully
from the fabricated inputs and declared success, because internal consistency
was the only thing being checked. A witness is a provenance claim: "this number
came from that fact". This module is what makes that claim checkable.

What a binding check can establish
----------------------------------

For one sourced input, :func:`verify_input_binding` establishes:

1. the referenced fact exists in the ledger;
2. the fact belongs to the run's company;
3. the witness's numeric value equals the fact's recorded value;
4. units share a compatible dimension (and currency where one applies);
5. the economic period and period type match the fact;
6. the accounting basis is compatible;
7. the source reference and filing identity are well-formed;
8. the fact was publicly available by the research cutoff;
9. a derived ratio resolves to an inspectable derivation over underlying facts.

What it cannot establish
------------------------

- That the fact is *true*. It proves the number matches the recorded fact, not
  that the issuer reported the fact correctly.
- That the fact is the *economically appropriate* input. Binding a real
  trailing revenue number to a DSRI input is semantically satisfied here; that
  the index is the right one to compute is an analytical judgement no code
  check can settle.
- That a derived ratio's formula is economically sound. Only that the
  derivation is stated, its operands are bound, and it recomputes.

Numeric comparison policy (explicit, not implicit)
--------------------------------------------------

There is no blanket ``abs(a - b) < epsilon`` escape hatch. The policy is:

- **Exact by default.** Two floats must be *exactly* equal to be considered
  bound. ``100`` vs ``150`` is a failure, and it must be an *unambiguous*
  failure, not one hidden under a tolerance.
- **Tolerance is opt-in and ratio-scaled.** When a caller explicitly asks for
  :func:`compare_numbers` with a relative tolerance, the tolerance is
  ``rel_tol * max(abs(a), abs(b))`` with an absolute floor. It is still a
  *declared* policy, recorded in the module and applied identically to every
  compared pair; it is never widened to make a mismatch disappear.
- **The default binding path uses no tolerance at all.** A value that survives
  JSON round-tripping and IEEE-754 parsing is bit-identical to the recorded
  fact. Any real difference is therefore a real difference. This is why the
  witness A/B test (100 vs 150) is deterministic rather than probabilistic.

Rationale: a tolerance large enough to excuse a 50% error (Witness B) would
also excuse the errors this module exists to catch. Only exactness keeps the
check meaningful for large-magnitude currency figures, where a relative
epsilon chosen for a ratio would silently swallow a fabricated absolute number.

Unit dimensions
---------------

Units are mapped to a *dimension* before comparison. Compatibility is decided
on dimension, not on string equality, because legitimate synonyms exist
(``shares``/``share``, ``USD``/``USD/shares`` is *not* compatible, etc.).
Incompatible dimensions are **rejected**, never implicitly converted:

===============  ============================================
Dimension        Members
===============  ============================================
``ratio``        ratio, pure, x, multiple, times, percent...
``currency``     USD, EUR, GBP, ... (a known currency code)
``count``        shares, count, shares_outstanding, employees
``per_share``    USD/share, EUR/share, $/share
``text``         a non-numeric label (never valid for a number)
===============  ============================================

Scale suffixes (``thousand``, ``million``, ``k``, ``m``) are recognised so that
a 1,000x scale error is *reported as a scale error* rather than as an opaque
dimension mismatch, and so that a scale mismatch is never auto-converted into
a pass.

The old guard was::

    expected_unit != "ratio" and fact_unit != expected_unit

which skipped the check entirely whenever the expected unit was ``"ratio"`` —
so a ratio input could silently cite a currency or share-count fact. This
module has no such short-circuit: a ``ratio`` expectation against a
``currency`` or ``count`` fact is a hard mismatch unless the input is
explicitly marked as derived from an inspectable derivation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

# --------------------------------------------------------------- policy const

#: Numeric-comparison policy identifier recorded in audit output so a reviewer
#: can tell *which* rule produced a verdict.
NUMERIC_COMPARISON_POLICY = "exact-float-equality-v1"

#: Relative tolerance used only by the explicitly-opt-in scaled comparison.
#: It is never applied on the default binding path.
DEFAULT_RELATIVE_TOLERANCE = 1e-9

#: Absolute floor for the scaled comparison, for values near zero.
DEFAULT_ABSOLUTE_TOLERANCE = 1e-12

#: Scale multipliers keyed by the suffix that introduces them.
_SCALE_SUFFIXES: Tuple[Tuple[str, float], ...] = (
    ("thousand", 1e3),
    ("thousands", 1e3),
    ("million", 1e6),
    ("millions", 1e6),
    ("billion", 1e9),
    ("billions", 1e9),
    ("k", 1e3),
    ("m", 1e6),
    ("mm", 1e6),
    ("bn", 1e9),
)

#: A closed set of recognised ISO-4217-style codes. Restricted on purpose: an
#: unrecognised label must not be silently treated as a compatible currency.
_CURRENCY_CODES = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "RMB", "HKD",
        "SEK", "NZD", "KRW", "SGD", "NOK", "MXN", "INR", "BRL", "TWD", "ILS",
        "ZAR", "PLN", "THB", "DKK",
    }
)

#: Dimension names.
DIM_RATIO = "ratio"
DIM_CURRENCY = "currency"
DIM_COUNT = "count"
DIM_PER_SHARE = "per_share"
DIM_UNKNOWN = "unknown"


class FactBindingError(Exception):
    """Raised when an attachment/ledger is structurally unusable."""


class BindingFailure(Exception):
    """Raised when a single witness input cannot be bound to its fact."""

    def __init__(self, reason: str, detail: Optional[str] = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# ------------------------------------------------------------------- units


def _clean(unit: Any) -> str:
    if unit is None:
        return ""
    return str(unit).strip()


def _split_scale(unit: str) -> Tuple[str, Optional[float]]:
    """Split a trailing scale qualifier off a unit label."""
    lowered = unit.strip().lower()
    for suffix, multiplier in _SCALE_SUFFIXES:
        if lowered == suffix:
            return "", multiplier
        for sep in (" ", "_", "-"):
            marker = f"{sep}{suffix}"
            if lowered.endswith(marker) and len(lowered) > len(marker):
                return lowered[: -len(marker)].strip(" _-"), multiplier
    return lowered, None


def unit_dimension(unit: Any) -> str:
    """Return the physical dimension of a unit label.

    ``ratio``/``pure``/``x``/``percent`` -> ``ratio``
    ISO currency codes                     -> ``currency``
    ``shares``/``count``/``employees``     -> ``count``
    ``USD/share``                          -> ``per_share``
    anything else                          -> ``unknown``
    """
    base, _ = _split_scale(_clean(unit))
    if not base:
        return DIM_UNKNOWN
    if base in ("ratio", "pure", "x", "multiple", "times", "index", "factor"):
        return DIM_RATIO
    if base in ("percent", "percentage", "pct", "%", "percent_change"):
        return DIM_RATIO
    upper = _clean(unit).strip().upper()
    if upper in _CURRENCY_CODES:
        return DIM_CURRENCY
    base_upper = base.upper()
    if base_upper in _CURRENCY_CODES:
        return DIM_CURRENCY
    if base_upper in (
        "SHARE",
        "SHARES",
        "COUNT",
        "EMPLOYEES",
        "SHARES_OUTSTANDING",
        "NUMBER_OF_SHARES",
        "UNITS",
        "EMPLOYEE_COUNT",
        "EMPLOYEE",
        "HEADCOUNT",
    ):
        return DIM_COUNT
    for code in _CURRENCY_CODES:
        if base_upper.startswith(code + "/") or base_upper.startswith(code + " PER "):
            return DIM_PER_SHARE
    if "per_share" in base or "/share" in base or "per share" in base:
        return DIM_PER_SHARE
    return DIM_UNKNOWN


def unit_currency(unit: Any) -> Optional[str]:
    """Return the ISO currency code implied by a unit label, if any."""
    upper = _clean(unit).strip().upper()
    if upper in _CURRENCY_CODES:
        return upper
    base, _ = _split_scale(_clean(unit))
    base_upper = base.upper()
    if base_upper in _CURRENCY_CODES:
        return base_upper
    for code in _CURRENCY_CODES:
        if base_upper.startswith(code + "/") or base_upper.startswith(code + " PER "):
            return code
    return None


def unit_scale(unit: Any) -> Optional[float]:
    """Return the numeric scale multiplier implied by a unit label."""
    _, multiplier = _split_scale(_clean(unit))
    return multiplier


def unit_currency_compatible(unit_a: Any, unit_b: Any) -> bool:
    """
    Currency compatibility for two labels of the same dimension.

    Two dimensionless ratios have no currency and are always compatible. For
    ``currency``/``per_share`` dimensions, the currency must match exactly; a
    missing currency is incompatible rather than assumed, because assuming one
    is how a EUR figure gets used as USD.
    """
    dim = unit_dimension(unit_a)
    if unit_dimension(unit_b) != dim:
        return False
    if dim not in (DIM_CURRENCY, DIM_PER_SHARE):
        return True
    cur_a = unit_currency(unit_a)
    cur_b = unit_currency(unit_b)
    if cur_a is None or cur_b is None:
        # One side is a bare currency with no code: cannot assert compatibility.
        return False
    return cur_a == cur_b


def units_compatible(unit_a: Any, unit_b: Any) -> bool:
    """
    Are two unit labels compatible for binding?

    Scale multipliers must agree: ``USD`` and ``USD thousand`` are the same
    dimension but *not* interchangeable values, so they are reported as a scale
    mismatch rather than silently accepted or silently converted.
    """
    if unit_dimension(unit_a) != unit_dimension(unit_b):
        return False
    scale_a = unit_scale(unit_a)
    scale_b = unit_scale(unit_b)
    if (scale_a or 1.0) != (scale_b or 1.0):
        return False
    return unit_currency_compatible(unit_a, unit_b)


def describe_unit_mismatch(expected: Any, actual: Any) -> str:
    """Human-readable, specific reason two unit labels are incompatible."""
    exp_dim = unit_dimension(expected)
    act_dim = unit_dimension(actual)
    if exp_dim != act_dim:
        return (
            f"dimension mismatch: witness expects a '{exp_dim}' unit "
            f"({_clean(expected)!r}) but fact records '{act_dim}' ({_clean(actual)!r})"
        )
    scale_exp = unit_scale(expected)
    scale_act = unit_scale(actual)
    if (scale_exp or 1.0) != (scale_act or 1.0):
        ratio = (scale_act or 1.0) / (scale_exp or 1.0)
        return (
            f"scale mismatch: witness unit {_clean(expected)!r} vs fact unit "
            f"{_clean(actual)!r} differ by a factor of {ratio:g}"
        )
    if not unit_currency_compatible(expected, actual):
        return (
            f"currency mismatch: witness unit {_clean(expected)!r} vs fact unit "
            f"{_clean(actual)!r}"
        )
    return f"unit mismatch: {_clean(expected)!r} vs {_clean(actual)!r}"


# ------------------------------------------------------------------ numbers


def compare_numbers(
    claimed: Any,
    actual: Any,
    *,
    rel_tol: Optional[float] = None,
    abs_tol: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Compare a claimed numeric input against a fact's recorded value.

    Default (``rel_tol=None``): **exact** equality. A value that has been
    JSON-round-tripped and parsed back to a float is bit-identical, so exact
    comparison is meaningful and any difference is a genuine difference.

    Opt-in scaled mode applies ``rel_tol * max(|a|,|b|) + abs_tol`` and returns
    the effective bound so a verdict can report it. This is the *only* place a
    tolerance exists, it must be requested explicitly, and it is reported
    alongside the verdict rather than hidden inside it.

    Returns ``(ok, detail)``.
    """
    if isinstance(claimed, bool) or isinstance(actual, bool):
        return False, f"booleans are not numeric values (claimed={claimed!r}, actual={actual!r})"
    try:
        c = float(claimed)
        a = float(actual)
    except (TypeError, ValueError) as exc:
        return False, f"non-numeric value (claimed={claimed!r}, actual={actual!r}): {exc}"
    if not math.isfinite(c) or not math.isfinite(a):
        return False, (
            f"non-finite value (claimed={c!r}, actual={a!r}); "
            "an unbounded number cannot be bound to a fact"
        )

    if rel_tol is None and abs_tol is None:
        if c == a:
            return True, "exact float equality"
        return False, (
            f"claimed value {c!r} does not equal the referenced fact's value {a!r} "
            f"(absolute difference {abs(c - a)!r}, relative {abs(c - a) / max(abs(a), 1e-300)!r}); "
            f"policy={NUMERIC_COMPARISON_POLICY} (no tolerance)"
        )

    rel = DEFAULT_RELATIVE_TOLERANCE if rel_tol is None else rel_tol
    ab = DEFAULT_ABSOLUTE_TOLERANCE if abs_tol is None else abs_tol
    bound = rel * max(abs(c), abs(a)) + ab
    difference = abs(c - a)
    if difference <= bound:
        return True, f"within declared tolerance {bound!r} (difference {difference!r})"
    return False, (
        f"claimed value {c!r} differs from the referenced fact's value {a!r} by "
        f"{difference!r}, exceeding the declared tolerance {bound!r} "
        f"(rel_tol={rel!r}, abs_tol={ab!r})"
    )


# --------------------------------------------------------------------- facts


def _fact_get(fact: Any, key: str, default: Any = None) -> Any:
    if isinstance(fact, Mapping):
        return fact.get(key, default)
    return getattr(fact, key, default)


def fact_period_kind(fact: Any) -> str:
    """``instant`` or ``duration`` for a fact, or ``unknown``."""
    if _fact_get(fact, "instant"):
        return "instant"
    if _fact_get(fact, "period_start") and _fact_get(fact, "period_end"):
        return "duration"
    return "unknown"


_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def fact_filing_identity_valid(fact: Any) -> Tuple[bool, str]:
    """
    Validate filing identity: the accession number must be well-formed.

    A fact whose accession is an empty string or a placeholder is not evidence
    of a filing, and must not be allowed to satisfy a citation.
    """
    accession = _fact_get(fact, "filing_accession")
    if not accession or not str(accession).strip():
        return False, "fact carries no filing accession"
    text = str(accession).strip()
    if not _ACCESSION_RE.match(text):
        return False, (
            f"filing accession {text!r} is not a well-formed SEC accession "
            "(expected NNNNNNNN-NN-NNNNNN); a placeholder is not a filing identity"
        )
    return True, f"accession {text}"


def fact_source_reference_valid(fact: Any) -> Tuple[bool, str]:
    """Validate that a fact points at a real, public source location."""
    location = _fact_get(fact, "source_location")
    if not location:
        return False, "fact carries no source_location"
    if isinstance(location, Mapping):
        url = str(location.get("url") or "").strip()
        if not url:
            return False, "source_location object carries no url"
        if not url.lower().startswith(("http://", "https://")):
            return False, f"source_location url {url!r} is not an absolute http(s) reference"
        return True, f"source url {url}"
    text = str(location).strip()
    if not text:
        return False, "fact carries an empty source_location"
    if not text.lower().startswith(("http://", "https://")):
        return False, f"source_location {text!r} is not an absolute http(s) reference"
    return True, f"source reference {text}"


_BASIS_EQUIVALENTS = {
    "US-GAAP": "US-GAAP",
    "US_GAAP": "US-GAAP",
    "GAAP": "US-GAAP",
    "IFRS": "IFRS",
    "NON-GAAP": "NON-GAAP",
    "NON_GAAP": "NON-GAAP",
    "NONGAAP": "NON-GAAP",
    "OTHER": "OTHER",
    "TAX": "TAX",
    "CASH": "CASH",
}


def normalize_accounting_basis(basis: Any) -> str:
    """Normalise an accounting-basis label for comparison."""
    if basis is None:
        return ""
    raw = getattr(basis, "value", basis)
    text = str(raw).strip().upper().replace("_", "-").replace(" ", "-")
    return _BASIS_EQUIVALENTS.get(text, text)


def accounting_bases_compatible(expected: Any, actual: Any) -> bool:
    """Accounting bases are compatible only when they are the same basis."""
    norm_expected = normalize_accounting_basis(expected)
    norm_actual = normalize_accounting_basis(actual)
    if not norm_expected or not norm_actual:
        return False
    return norm_expected == norm_actual


# ------------------------------------------------------------------ periods


def _period_fields(fact: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    return (
        _fact_get(fact, "period_start"),
        _fact_get(fact, "period_end"),
        _fact_get(fact, "instant"),
    )


def expected_period_key(spec: Mapping[str, Any]) -> Optional[str]:
    """
    Build a canonical period key from a witness's declared expectation.

    Accepts ``{"period_start", "period_end"}`` or ``{"instant"}``. Returns None
    when the witness declares nothing, in which case period checking is skipped
    (an undeclared period is not a *wrong* period, it is an *unverified* one —
    and the caller is told so separately).
    """
    start = spec.get("period_start")
    end = spec.get("period_end")
    instant = spec.get("instant")
    if start and end:
        return f"duration:{start}/{end}"
    if instant:
        return f"instant:{instant}"
    return None


def fact_period_key(fact: Any) -> Optional[str]:
    start, end, instant = _period_fields(fact)
    if instant:
        return f"instant:{instant}"
    if start and end:
        return f"duration:{start}/{end}"
    return None


# -------------------------------------------------------------- derivations


@dataclass(frozen=True)
class Derivation:
    """
    An inspectable derivation of one derived input (e.g. a Beneish index).

    ``operand_fact_ids`` names the facts the operands came from; each operand
    names a field on the fact (``value`` by default) and optionally a unit.
    ``expression`` is the arithmetic in textual form, recorded verbatim so a
    reviewer can read the formula without running code.
    """

    expression: str
    operand_fact_ids: Tuple[str, ...]
    operand_fields: Tuple[str, ...] = ()
    result_value: Optional[float] = None

    @staticmethod
    def from_spec(spec: Any) -> Optional["Derivation"]:
        """Build a :class:`Derivation` from a witness-supplied mapping."""
        if not isinstance(spec, Mapping):
            return None
        expression = str(spec.get("expression") or "").strip()
        operands = spec.get("operand_fact_ids")
        if not isinstance(operands, (list, tuple)) or not operands:
            return None
        operand_ids = tuple(str(x) for x in operands)
        fields_spec = spec.get("operand_fields") or ()
        if isinstance(fields_spec, (list, tuple)) and fields_spec:
            operand_fields = tuple(str(x) for x in fields_spec)
        else:
            operand_fields = tuple("value" for _ in operand_ids)
        if len(operand_fields) != len(operand_ids):
            # A mismatched operand-field list is unusable as stated.
            operand_fields = operand_fields[: len(operand_ids)]
            operand_fields += tuple(
                "value" for _ in range(len(operand_ids) - len(operand_fields))
            )
        result_value = spec.get("result_value")
        try:
            result_value = float(result_value) if result_value is not None else None
        except (TypeError, ValueError):
            result_value = None
        if not expression:
            return None
        return Derivation(
            expression=expression,
            operand_fact_ids=operand_ids,
            operand_fields=operand_fields,
            result_value=result_value,
        )


# ------------------------------------------------------------------ binding


@dataclass
class InputBinding:
    """Outcome of verifying one witness input against its cited fact."""

    input_key: str
    fact_id: Optional[str] = None
    bound: bool = False
    #: ``True`` when the input is fully bound; ``False`` with a reason when it is
    #: unavailable (no fact cited) or failed.
    status: str = "unavailable"
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    derivation: Optional[Derivation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_key": self.input_key,
            "fact_id": self.fact_id,
            "bound": self.bound,
            "status": self.status,
            "problems": list(self.problems),
            "notes": list(self.notes),
            "derivation": (
                {
                    "expression": self.derivation.expression,
                    "operand_fact_ids": list(self.derivation.operand_fact_ids),
                    "operand_fields": list(self.derivation.operand_fields),
                    "result_value": self.derivation.result_value,
                }
                if self.derivation
                else None
            ),
        }


def verify_input_binding(
    *,
    input_key: str,
    claimed_value: Any,
    fact: Optional[Any],
    fact_id: Optional[str],
    expected_unit: Optional[str],
    fact_ledger: Mapping[str, Any],
    expected_company: Optional[str] = None,
    expected_period: Optional[Mapping[str, Any]] = None,
    expected_basis: Optional[str] = None,
    cutoff: Optional[Union[str, date, datetime]] = None,
    derivation_spec: Optional[Any] = None,
    rel_tol: Optional[float] = None,
) -> InputBinding:
    """
    Verify one sourced calculation input against the fact it cites.

    Returns an :class:`InputBinding`. ``bound=True`` means every applicable
    check passed. Problems are collected rather than raised so a reviewer sees
    the full picture for one input instead of only the first failure.

    The checks, in order:

    - fact exists and is non-``None``
    - company matches the run's company (case-insensitive)
    - value equality against the recorded fact value
    - unit dimension/currency/scale compatibility
    - period and period type match
    - accounting basis compatible
    - filing identity and source reference well-formed
    - availability at or before the cutoff
    """
    result = InputBinding(input_key=input_key, fact_id=fact_id)

    # --- 0. the fact must exist -------------------------------------------
    if fact is None:
        result.status = "failed"
        result.problems.append(
            f"witness input '{input_key}' cites fact '{fact_id}' which does not exist "
            "in the fact ledger; a citation to a non-existent fact cannot bind a number"
        )
        return result

    # --- 1. company ownership ----------------------------------------------
    fact_company = _fact_get(fact, "company")
    if expected_company is not None:
        if not fact_company or str(fact_company).strip().upper() != str(expected_company).strip().upper():
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' cites fact '{fact_id}' belonging to company "
                f"{fact_company!r}, not the run's company {expected_company!r}"
            )
            return result

    # --- 2. the fact must carry an actual value ----------------------------
    fact_value = _fact_get(fact, "value")
    if fact_value is None:
        # Citing a fact that records no value is a genuine defect: the witness
        # claims a numeric source that does not carry one.
        reason = _fact_get(fact, "missing_reason") or "no documented reason"
        result.status = "failed"
        result.problems.append(
            f"witness input '{input_key}' cites fact '{fact_id}' which records no value "
            f"(missing_reason={reason!r}); an unavailable fact cannot back a numeric input"
        )
        return result

    # --- 3. the core binding: numeric equality -----------------------------
    ok, detail = compare_numbers(claimed_value, fact_value, rel_tol=rel_tol)
    if not ok:
        result.status = "failed"
        result.problems.append(
            f"witness input '{input_key}' {detail} (fact '{fact_id}', policy={NUMERIC_COMPARISON_POLICY})"
        )

    # --- 4. unit compatibility ---------------------------------------------
    fact_unit = _fact_get(fact, "unit")
    if expected_unit:
        if not fact_unit:
            result.problems.append(
                f"witness input '{input_key}' expects unit {expected_unit!r} but fact "
                f"'{fact_id}' records no unit"
            )
            result.status = "failed"
        else:
            exp_dim = unit_dimension(expected_unit)
            fact_dim = unit_dimension(fact_unit)
            is_derived = derivation_spec is not None
            if not is_derived and exp_dim == DIM_RATIO and fact_dim in (DIM_CURRENCY, DIM_COUNT):
                # The historical bypass, now explicit and rejected.
                result.problems.append(
                    f"witness input '{input_key}' expects a 'ratio' but fact '{fact_id}' is a "
                    f"{fact_dim} fact ({fact_unit!r}); "
                    + describe_unit_mismatch(expected_unit, fact_unit)
                    + ". A ratio input must cite a ratio-valued fact or be marked as derived."
                )
                result.status = "failed"
            elif not units_compatible(expected_unit, fact_unit):
                result.problems.append(
                    f"witness input '{input_key}' unit incompatible with fact '{fact_id}': "
                    + describe_unit_mismatch(expected_unit, fact_unit)
                )
                result.status = "failed"
            else:
                result.notes.append(
                    f"unit bound: {expected_unit!r} compatible with fact unit {fact_unit!r} "
                    f"(dimension {exp_dim})"
                )

    # --- 5. period and period type -----------------------------------------
    if expected_period:
        want_key = expected_period_key(expected_period)
        have_key = fact_period_key(fact)
        if want_key is not None:
            want_kind = "instant" if want_key.startswith("instant:") else "duration"
            have_kind = fact_period_kind(fact)
            if have_key is None:
                result.problems.append(
                    f"witness input '{input_key}' declares period {want_key!r} but fact "
                    f"'{fact_id}' records no economic period"
                )
                result.status = "failed"
            elif want_kind != have_kind:
                result.problems.append(
                    f"witness input '{input_key}' period type mismatch: witness expects a "
                    f"{want_kind} period, fact '{fact_id}' is {have_kind}"
                )
                result.status = "failed"
            elif want_key != have_key:
                result.problems.append(
                    f"witness input '{input_key}' period mismatch: witness expects "
                    f"{want_key!r}, fact '{fact_id}' records {have_key!r}"
                )
                result.status = "failed"
            else:
                result.notes.append(f"period bound: {have_key}")

    # --- 6. accounting basis ------------------------------------------------
    if expected_basis is not None:
        if not accounting_bases_compatible(expected_basis, _fact_get(fact, "accounting_basis")):
            result.problems.append(
                f"witness input '{input_key}' accounting basis mismatch: witness expects "
                f"{expected_basis!r}, fact '{fact_id}' records "
                f"{_fact_get(fact, 'accounting_basis')!r}"
            )
            result.status = "failed"
        else:
            result.notes.append(
                f"accounting basis bound: {normalize_accounting_basis(expected_basis)}"
            )

    # --- 7. filing identity and source reference ----------------------------
    ok_id, id_detail = fact_filing_identity_valid(fact)
    if not ok_id:
        result.problems.append(f"witness input '{input_key}' {id_detail}")
        result.status = "failed"
    else:
        result.notes.append(f"filing identity bound: {id_detail}")

    ok_src, src_detail = fact_source_reference_valid(fact)
    if not ok_src:
        result.problems.append(f"witness input '{input_key}' {src_detail}")
        result.status = "failed"
    else:
        result.notes.append(f"source reference bound: {src_detail}")

    # --- 8. point-in-time availability --------------------------------------
    if cutoff is not None:
        from src.facts import usable_as_of

        if not usable_as_of(fact, cutoff):
            avail = _fact_get(fact, "availability_time")
            result.problems.append(
                f"witness input '{input_key}' cites fact '{fact_id}' whose availability_time "
                f"{avail!r} is after the research cutoff {cutoff!r} (look-ahead violation)"
            )
            result.status = "failed"
        else:
            result.notes.append(
                f"available by cutoff {cutoff!r}: availability_time="
                f"{_fact_get(fact, 'availability_time')!r}"
            )

    # --- 9. derived inputs need an inspectable derivation -------------------
    if derivation_spec is not None:
        derivation = Derivation.from_spec(derivation_spec)
        if derivation is None:
            result.problems.append(
                f"witness input '{input_key}' is marked derived but supplies no inspectable "
                "derivation (an expression plus at least one operand fact ID is required)"
            )
            result.status = "failed"
        else:
            result.derivation = derivation
            missing_ops = [oid for oid in derivation.operand_fact_ids if oid not in fact_ledger]
            if missing_ops:
                result.problems.append(
                    f"witness input '{input_key}' derivation cites operand fact(s) "
                    f"{missing_ops} that do not exist in the fact ledger"
                )
                result.status = "failed"
            else:
                result.notes.append(
                    f"derivation inspectable: {derivation.expression} over "
                    f"{list(derivation.operand_fact_ids)}"
                )
            if derivation.result_value is not None:
                ok_d, detail_d = compare_numbers(claimed_value, derivation.result_value, rel_tol=rel_tol)
                if not ok_d:
                    result.problems.append(
                        f"witness input '{input_key}' derived value {detail_d}"
                    )
                    result.status = "failed"

    if result.status != "failed":
        result.bound = True
        result.status = "bound"
    return result


def verify_witness_bindings(
    witness: Mapping[str, Any],
    *,
    fact_ledger: Mapping[str, Any],
    expected_company: Optional[str] = None,
    cutoff: Optional[Union[str, date, datetime]] = None,
    expected_periods: Optional[Mapping[str, Mapping[str, Any]]] = None,
    expected_bases: Optional[Mapping[str, str]] = None,
    rel_tol: Optional[float] = None,
) -> List[InputBinding]:
    """
    Verify every sourced input of a witness.

    An input with no cited fact is *unavailable*, not failed: a witness may
    legitimately record a computed index that has no single underlying fact, as
    long as it says so. What must never happen is a **sourced** input whose
    number disagrees with the fact it cites.
    """
    inputs = witness.get("inputs") if isinstance(witness, Mapping) else None
    inputs = inputs if isinstance(inputs, Mapping) else {}
    input_fact_ids = witness.get("input_fact_ids")
    input_fact_ids = input_fact_ids if isinstance(input_fact_ids, Mapping) else {}
    units = witness.get("units")
    units = units if isinstance(units, Mapping) else {}
    derivations = witness.get("derivations")
    derivations = derivations if isinstance(derivations, Mapping) else {}
    expected_periods = expected_periods or {}
    expected_bases = expected_bases or {}

    bindings: List[InputBinding] = []
    for input_key, claimed in inputs.items():
        fact_id = input_fact_ids.get(input_key)
        if not fact_id or not isinstance(fact_id, str):
            # No cited fact is *unverified*, not *wrong*. It is reported so a
            # reviewer sees the gap, but it is not a verification failure: a
            # legitimately computed index may have no single underlying fact.
            bindings.append(
                InputBinding(
                    input_key=input_key,
                    status="unavailable",
                    notes=[
                        f"witness input '{input_key}' ({claimed!r}) cites no source fact; "
                        "its value is unverified against any typed fact"
                    ],
                )
            )
            continue
        fact = fact_ledger.get(fact_id)
        bindings.append(
            verify_input_binding(
                input_key=input_key,
                claimed_value=claimed,
                fact=fact,
                fact_id=fact_id,
                expected_unit=units.get(input_key),
                fact_ledger=fact_ledger,
                expected_company=expected_company,
                expected_period=expected_periods.get(input_key),
                expected_basis=expected_bases.get(input_key),
                cutoff=cutoff,
                derivation_spec=derivations.get(input_key),
                rel_tol=rel_tol,
            )
        )
    return bindings


def binding_failures(bindings: Sequence[InputBinding]) -> List[str]:
    """
    Flatten bindings into problem strings for an audit error list.

    Only bindings that actually *failed* are returned. Unavailable bindings
    (no cited fact) carry their explanation in ``notes`` and are not errors:
    they are a disclosed gap, not a false claim.
    """
    problems: List[str] = []
    for binding in bindings:
        if binding.status == "failed":
            problems.extend(binding.problems)
    return problems


def binding_notes(bindings: Sequence[InputBinding]) -> List[str]:
    """
    Disclosures that are not failures: unverified inputs and the evidence that
    backed the successful checks.

    These are surfaced as audit warnings so that "the arithmetic replayed" and
    "every input was source-backed" stay visibly different statements.
    """
    notes: List[str] = []
    for binding in bindings:
        if binding.status == "unavailable":
            notes.extend(binding.notes or binding.problems)
    return notes