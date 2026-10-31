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

import ast
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
    if base in _PERCENT_UNITS:
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


_PERCENT_UNITS = ("percent", "percentage", "pct", "%", "percent_change")


def unit_scale(unit: Any) -> Optional[float]:
    """Return the numeric scale multiplier implied by a unit label.

    A percent is a ratio scaled by 0.01, so 12.5 (percent) and 0.125 (ratio)
    are the same dimension but not interchangeable values: they are reported
    as a scale mismatch, never silently accepted as equal units.
    """
    base, multiplier = _split_scale(_clean(unit))
    if base in _PERCENT_UNITS:
        return 0.01 * (multiplier or 1.0)
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
    except (TypeError, ValueError, OverflowError) as exc:
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

#: The SEC placeholder used when an entry carries no real accession. It is
#: well-formed by shape but identifies no filing, so it must not satisfy a
#: citation.
_PLACEHOLDER_ACCESSIONS = frozenset({"0000000000-00-000000", "00000000000000"})


def fact_filing_identity_valid(fact: Any) -> Tuple[bool, str]:
    """
    Validate filing identity: the accession number must be well-formed.

    A fact whose accession is an empty string, malformed, or the SEC's
    all-zero placeholder is not evidence of a filing, and must not be allowed
    to satisfy a citation.
    """
    accession = _fact_get(fact, "filing_accession")
    if not accession or not str(accession).strip():
        return False, "fact carries no filing accession"
    text = str(accession).strip()
    if text in _PLACEHOLDER_ACCESSIONS:
        return False, (
            f"filing accession {text!r} is the SEC placeholder, not a real filing identity"
        )
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


def _is_period_spec(spec: Any) -> bool:
    """True when a mapping is itself a single period declaration."""
    if not isinstance(spec, Mapping):
        return False
    return any(key in spec for key in ("period_start", "period_end", "instant"))


def anchor_period_spec(spec: Any) -> Optional[Mapping[str, Any]]:
    """Reduce a witness period declaration to its single anchor period.

    A witness input may legitimately span two periods — a change index compares a
    current and a prior period — so ``input_periods`` can carry a mapping of
    period specs. The binder checks one *anchor* period per input: the most recent
    one declared. That keeps the check meaningful (the current period must be
    among the operands, and nothing may reach past it) without pretending a
    two-period index has a single period.
    """
    if _is_period_spec(spec):
        return spec
    if not isinstance(spec, Mapping):
        return None
    nested = [value for value in spec.values() if _is_period_spec(value)]
    if not nested:
        return None

    def _end(value: Mapping[str, Any]) -> str:
        return str(value.get("instant") or value.get("period_end") or value.get("period_start") or "")

    return max(nested, key=_end)


def expected_period_key(spec: Mapping[str, Any]) -> Optional[str]:
    """
    Build a canonical period key from a witness's declared expectation.

    Accepts ``{"period_start", "period_end"}``, ``{"instant"}``, or a mapping of
    such specs (in which case the most recent period is the anchor). Returns None
    when the witness declares nothing, in which case period checking is skipped
    (an undeclared period is not a *wrong* period, it is an *unverified* one —
    and the caller is told so separately).
    """
    anchor = anchor_period_spec(spec)
    if anchor is None:
        return None
    return _canonical_period_key(anchor.get("period_start"), anchor.get("period_end"), anchor.get("instant"))


def _canonical_period_key(start: Any, end: Any, instant: Any) -> Optional[str]:
    def valid_day(value: Any) -> bool:
        if not isinstance(value, str) or len(value) != 10:
            return False
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False

    if instant:
        return f"instant:{instant}" if not start and not end and valid_day(instant) else None
    if valid_day(start) and valid_day(end) and start <= end:
        return f"duration:{start}/{end}"
    return None


def fact_period_key(fact: Any) -> Optional[str]:
    return _canonical_period_key(*_period_fields(fact))


# -------------------------------------------------------------- derivations

#: Maximum character length for derivation expression string.
_AST_MAX_CHARS: int = 1000

#: Maximum AST nodes allowed in an expression (prevents complex/DoS trees).
_AST_MAX_NODES: int = 50

#: Maximum AST recursion/nesting depth.
_AST_MAX_DEPTH: int = 10


class DerivationError(Exception):
    """Base exception for derivation expression errors."""


class DerivationSyntaxError(DerivationError):
    """Raised when an expression contains invalid syntax, arbitrary constants, or unresolvable operands."""


class DerivationEvaluationError(DerivationError):
    """Raised when arithmetic evaluation fails (e.g. division by zero, non-finite values, overflow)."""


class DerivationUnitError(DerivationError):
    """Raised when derivation operand units, currencies, or scales are incoherent."""


@dataclass(frozen=True)
class InferredUnit:
    """Inferred physical dimension, currency, and scale of a formula AST node."""

    dimension: str
    currency: Optional[str] = None
    scale: float = 1.0


@dataclass(frozen=True)
class Derivation:
    """
    An inspectable derivation of one derived input (e.g. a Beneish index).

    ``operand_fact_ids`` names the facts the operands came from; each operand
    names a field on the fact (``value`` by default).
    ``expression`` is the arithmetic in textual form, evaluated through safe AST replay.
    ``operand_names`` optional variable names used in ``expression`` corresponding
    to ``operand_fact_ids``.
    ``result_value`` optional declared result of the formula.

    Expected Expression Syntax:
    ---------------------------
    Derivation expressions must be safe, bounded arithmetic expressions over
    fact-bound variables.
      - Binary arithmetic operators: `+`, `-`, `*`, `/`
      - Unary arithmetic operators: `+`, `-`
      - Parenthesized sub-expressions: `(...)`
      - Fact-bound variable names: e.g. `receivables_t1`, `receivables_t0`, `a`, `b`

    Invariants:
    -----------
    1. No arbitrary constants / literals:
       Every numeric value in a source-backed computation must resolve to a
       fact value in the ledger. Expressions composed of constants (e.g. '100 / 2')
       or containing arbitrary numeric literals are rejected. A formula that
       genuinely needs a baseline or scale declares it as a **named constant**
       (``constants={'one': 1.0}``), which is recorded in the witness and
       reviewable; it is never an anonymous digit inside the expression string.
    2. Complete operand binding:
       Every variable in the expression must resolve to a declared operand fact.
       Every declared operand fact must be used in the expression (no unused operands).
    3. Safe evaluation:
       No `eval()` or `exec()`. Traversed purely via explicit AST nodes.
       Zero division, non-finite values, booleans, and overflow (>1e100) are rejected.
    4. Unit coherence:
       Addition/subtraction requires matching dimensions, currencies, and scales.
       Division of like dimensions produces a dimensionless `ratio`.
       Scale mismatches (e.g. USD / USD thousand) are rejected.
    """

    expression: str
    operand_fact_ids: Tuple[str, ...]
    operand_fields: Tuple[str, ...] = ()
    result_value: Optional[float] = None
    operand_names: Tuple[str, ...] = ()
    #: Named numeric constants used by the expression.
    #:
    #: Most indices are pure ratios of fact values. A few are not: an index
    #: that measures a *change* against a baseline needs an explicit ``1``
    #: (``(margin_t0 / margin_t1) - 1``), and an index expressed as a share of
    #: total assets needs that scale. Writing those as literals inside the
    #: expression would break the rule that every number in a source-backed
    #: derivation resolves to a fact. Declaring them here keeps the rule: the
    #: constant is named, recorded in the witness, and reviewable, instead of
    #: being an anonymous digit buried in a formula string.
    constants: Mapping[str, float] = field(default_factory=dict)

    @staticmethod
    def from_spec(spec: Any) -> Optional["Derivation"]:
        """Build a :class:`Derivation` from a witness-supplied mapping."""
        if not isinstance(spec, Mapping):
            return None
        expression = str(spec.get("expression") or "").strip()
        operands = spec.get("operand_fact_ids")
        if not isinstance(operands, (list, tuple)):
            # Support {"operands": {"rec_t1": "fact_id_1", "rec_t0": "fact_id_2"}}
            operands_dict = spec.get("operands")
            if isinstance(operands_dict, Mapping) and operands_dict:
                operand_names = tuple(str(k) for k in operands_dict.keys())
                operand_ids = tuple(str(v) for v in operands_dict.values())
            else:
                return None
        else:
            if not operands:
                return None
            operand_ids = tuple(str(x) for x in operands)
            names_spec = spec.get("operand_names") or ()
            if isinstance(names_spec, (list, tuple)) and names_spec:
                operand_names = tuple(str(x) for x in names_spec)
            else:
                operand_names = ()

        fields_spec = spec.get("operand_fields")
        if fields_spec is not None:
            if not isinstance(fields_spec, (list, tuple)) or len(fields_spec) != len(operand_ids):
                return None
            # A typed financial fact has exactly one economic numeric value.
            # Metadata/confidence/schema fields cannot masquerade as money.
            if any(field != "value" for field in fields_spec):
                return None
            operand_fields = tuple(fields_spec)
        else:
            operand_fields = tuple("value" for _ in operand_ids)
        result_value = spec.get("result_value")
        if result_value is not None:
            if isinstance(result_value, bool) or not isinstance(result_value, (int, float)):
                return None
            try:
                result_value = float(result_value)
            except (ValueError, OverflowError):
                return None
            if not math.isfinite(result_value):
                return None
        if not expression:
            return None
        constants_spec = spec.get("constants")
        constants: Dict[str, float] = {}
        if constants_spec is not None:
            if not isinstance(constants_spec, Mapping):
                return None
            for name, raw in constants_spec.items():
                if not isinstance(name, str) or not name.isidentifier():
                    return None
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    return None
                try:
                    value = float(raw)
                except (ValueError, OverflowError):
                    return None
                if not math.isfinite(value):
                    return None
                constants[name] = value
        return Derivation(
            expression=expression,
            operand_fact_ids=operand_ids,
            operand_fields=operand_fields,
            result_value=result_value,
            operand_names=operand_names,
            constants=constants,
        )


def _parse_and_validate_ast(expression: str) -> ast.Expression:
    """Parse expression and validate bounded complexity and allowed AST node types.

    Allowed AST nodes:
      - ast.Expression (root in mode='eval')
      - ast.BinOp with op in (Add, Sub, Mult, Div)
      - ast.UnaryOp with op in (UAdd, USub)
      - ast.Name (variable identifier)

    Disallowed:
      - ast.Constant (arbitrary constants/literals are forbidden)
      - ast.Call, ast.Attribute, ast.Subscript, ast.List, etc.
    """
    if len(expression) > _AST_MAX_CHARS:
        raise DerivationSyntaxError(
            f"expression length ({len(expression)}) exceeds maximum limit of {_AST_MAX_CHARS} characters"
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise DerivationSyntaxError(f"invalid expression syntax: {exc}") from exc

    node_count = 0

    def _walk_and_check(node: ast.AST, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > _AST_MAX_NODES:
            raise DerivationSyntaxError(
                f"expression exceeds complexity limit of {_AST_MAX_NODES} AST nodes"
            )
        if depth > _AST_MAX_DEPTH:
            raise DerivationSyntaxError(
                f"expression exceeds maximum nesting depth of {_AST_MAX_DEPTH}"
            )

        if isinstance(node, ast.Expression):
            _walk_and_check(node.body, depth + 1)
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                raise DerivationSyntaxError(
                    f"unsupported binary operator '{type(node.op).__name__}'; only +, -, *, / are allowed"
                )
            _walk_and_check(node.left, depth + 1)
            _walk_and_check(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                raise DerivationSyntaxError(
                    f"unsupported unary operator '{type(node.op).__name__}'; only unary + and - are allowed"
                )
            _walk_and_check(node.operand, depth + 1)
        elif isinstance(node, ast.Name):
            if not node.id or node.id.startswith("__"):
                raise DerivationSyntaxError(f"invalid variable identifier {node.id!r}")
        elif isinstance(node, ast.Constant):
            raise DerivationSyntaxError(
                f"arbitrary numeric constants ({node.value!r}) are not permitted in source-backed derivations; "
                "all operands must be fact-bound variables"
            )
        else:
            raise DerivationSyntaxError(
                f"unsupported syntax node '{type(node).__name__}'; derivations only permit explicit "
                "arithmetic operations over fact-bound variables"
            )

    _walk_and_check(tree, 0)
    return tree


def _resolve_derivation_variables(
    tree: ast.Expression,
    derivation: Derivation,
) -> Dict[str, str]:
    """Map variable names in the AST to operand fact IDs in derivation.operand_fact_ids.

    Enforces that:
    1. The expression references at least one variable.
    2. Every referenced variable resolves to a declared operand fact ID.
    3. Every declared operand fact ID is used in the expression (no unused operands).
    """
    names_used = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)]
    if not names_used:
        raise DerivationSyntaxError(
            "derivation expression contains no variable references; at least one operand is required"
        )
    # Declared constants are named numbers, not operands: they resolve to a
    # recorded value rather than to a fact, so they are removed before operand
    # matching and re-attached by the caller.
    constants = getattr(derivation, "constants", None) or {}
    unique_names = sorted({name for name in names_used if name not in constants})
    if not unique_names:
        raise DerivationSyntaxError(
            "derivation expression references only declared constants; at least one "
            "fact-bound operand is required"
        )
    num_operands = len(derivation.operand_fact_ids)

    # 1. Explicit operand_names supplied
    if derivation.operand_names:
        if len(derivation.operand_names) != num_operands:
            raise DerivationSyntaxError(
                f"operand_names count ({len(derivation.operand_names)}) does not match "
                f"operand_fact_ids count ({num_operands})"
            )
        name_to_id = dict(zip(derivation.operand_names, derivation.operand_fact_ids))
        for name in unique_names:
            if name not in name_to_id:
                raise DerivationSyntaxError(
                    f"expression references unknown variable {name!r}; "
                    f"declared operand names are {list(derivation.operand_names)}"
                )
        unused = [name for name in derivation.operand_names if name not in unique_names]
        if unused:
            unused_ids = [name_to_id[name] for name in unused]
            raise DerivationSyntaxError(
                f"derivation declares unused operand(s): {unused_ids} (named {unused})"
            )
        return name_to_id

    # 2. Direct match against fact IDs
    if all(name in derivation.operand_fact_ids for name in unique_names):
        unused_ids = [oid for oid in derivation.operand_fact_ids if oid not in unique_names]
        if unused_ids:
            raise DerivationSyntaxError(
                f"derivation declares unused operand fact ID(s): {unused_ids}"
            )
        return {name: name for name in unique_names}

    # 3. Positional aliases
    # 3a. Alphabetical letters: a, b, c, ...
    if num_operands <= 26:
        letters = [chr(ord("a") + i) for i in range(num_operands)]
        if all(name in letters for name in unique_names):
            name_to_id = dict(zip(letters, derivation.operand_fact_ids))
            unused_letters = [letters[i] for i in range(num_operands) if letters[i] not in unique_names]
            if unused_letters:
                unused_ids = [derivation.operand_fact_ids[i] for i in range(num_operands) if letters[i] not in unique_names]
                raise DerivationSyntaxError(
                    f"derivation declares unused operand(s): {unused_ids} (positional {unused_letters})"
                )
            return name_to_id

    # 3b. op0, op1, ... (0-indexed) or op1, op2, ... (1-indexed)
    op0_names = [f"op{i}" for i in range(num_operands)]
    if all(name in op0_names for name in unique_names):
        name_to_id = dict(zip(op0_names, derivation.operand_fact_ids))
        unused = [op0_names[i] for i in range(num_operands) if op0_names[i] not in unique_names]
        if unused:
            unused_ids = [derivation.operand_fact_ids[i] for i in range(num_operands) if op0_names[i] not in unique_names]
            raise DerivationSyntaxError(
                f"derivation declares unused operand(s): {unused_ids} (positional {unused})"
            )
        return name_to_id

    op1_names = [f"op{i + 1}" for i in range(num_operands)]
    if all(name in op1_names for name in unique_names):
        name_to_id = dict(zip(op1_names, derivation.operand_fact_ids))
        unused = [op1_names[i] for i in range(num_operands) if op1_names[i] not in unique_names]
        if unused:
            unused_ids = [derivation.operand_fact_ids[i] for i in range(num_operands) if op1_names[i] not in unique_names]
            raise DerivationSyntaxError(
                f"derivation declares unused operand(s): {unused_ids} (positional {unused})"
            )
        return name_to_id

    # 3c. x, y, z for up to 3 operands, or x0, x1, ...
    if num_operands <= 3:
        xyz_names = ["x", "y", "z"][:num_operands]
        if all(name in xyz_names for name in unique_names):
            name_to_id = dict(zip(xyz_names, derivation.operand_fact_ids))
            unused = [xyz_names[i] for i in range(num_operands) if xyz_names[i] not in unique_names]
            if unused:
                unused_ids = [derivation.operand_fact_ids[i] for i in range(num_operands) if xyz_names[i] not in unique_names]
                raise DerivationSyntaxError(
                    f"derivation declares unused operand(s): {unused_ids} (positional {unused})"
                )
            return name_to_id

    x0_names = [f"x{i}" for i in range(num_operands)]
    if all(name in x0_names for name in unique_names):
        name_to_id = dict(zip(x0_names, derivation.operand_fact_ids))
        unused = [x0_names[i] for i in range(num_operands) if x0_names[i] not in unique_names]
        if unused:
            unused_ids = [derivation.operand_fact_ids[i] for i in range(num_operands) if x0_names[i] not in unique_names]
            raise DerivationSyntaxError(
                f"derivation declares unused operand(s): {unused_ids} (positional {unused})"
            )
        return name_to_id

    raise DerivationSyntaxError(
        f"cannot resolve expression variable(s) {unique_names} to declared operands "
        f"{list(derivation.operand_fact_ids)}; declare matching fact IDs, explicit operand_names, "
        "or positional aliases (a, b, ... / op0, op1, ...)"
    )


def _evaluate_ast_node(node: ast.AST, env_values: Mapping[str, float]) -> float:
    """Recursively evaluate an AST expression with bound variable values."""
    if isinstance(node, ast.Expression):
        return _evaluate_ast_node(node.body, env_values)
    elif isinstance(node, ast.Name):
        if node.id not in env_values:
            raise DerivationEvaluationError(f"variable {node.id!r} has no bound numeric value")
        val = env_values[node.id]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise DerivationEvaluationError(
                f"variable {node.id!r} has non-numeric or non-finite value: {val!r}"
            )
        return float(val)
    elif isinstance(node, ast.UnaryOp):
        operand_val = _evaluate_ast_node(node.operand, env_values)
        if isinstance(node.op, ast.UAdd):
            res = +operand_val
        elif isinstance(node.op, ast.USub):
            res = -operand_val
        else:
            raise DerivationSyntaxError(f"unsupported unary operator: {type(node.op).__name__}")
        if not math.isfinite(res):
            raise DerivationEvaluationError("unary operation produced non-finite value")
        return res
    elif isinstance(node, ast.BinOp):
        left_val = _evaluate_ast_node(node.left, env_values)
        right_val = _evaluate_ast_node(node.right, env_values)
        if isinstance(node.op, ast.Add):
            res = left_val + right_val
        elif isinstance(node.op, ast.Sub):
            res = left_val - right_val
        elif isinstance(node.op, ast.Mult):
            res = left_val * right_val
        elif isinstance(node.op, ast.Div):
            if right_val == 0.0 or abs(right_val) < 1e-300:
                raise DerivationEvaluationError("division by zero in derivation expression")
            res = left_val / right_val
        else:
            raise DerivationSyntaxError(f"unsupported binary operator: {type(node.op).__name__}")
        if not math.isfinite(res):
            raise DerivationEvaluationError("arithmetic operation produced non-finite or overflow value")
        if abs(res) > 1e100:
            raise DerivationEvaluationError(
                f"arithmetic operation produced overlarge value ({res:g} > 1e100)"
            )
        return res
    else:
        raise DerivationSyntaxError(f"unsupported AST node: {type(node).__name__}")


def _infer_unit_ast_node(node: ast.AST, env_units: Mapping[str, InferredUnit]) -> InferredUnit:
    """Infer derived physical dimension, currency, and scale from AST expression."""
    if isinstance(node, ast.Expression):
        return _infer_unit_ast_node(node.body, env_units)
    elif isinstance(node, ast.Name):
        return env_units[node.id]
    elif isinstance(node, ast.UnaryOp):
        return _infer_unit_ast_node(node.operand, env_units)
    elif isinstance(node, ast.BinOp):
        left = _infer_unit_ast_node(node.left, env_units)
        right = _infer_unit_ast_node(node.right, env_units)

        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left.dimension != right.dimension:
                raise DerivationUnitError(
                    f"cannot add or subtract operands of different dimensions: "
                    f"'{left.dimension}' vs '{right.dimension}'"
                )
            if left.dimension in (DIM_CURRENCY, DIM_PER_SHARE):
                if left.currency != right.currency:
                    raise DerivationUnitError(
                        f"currency mismatch in addition/subtraction: {left.currency!r} vs {right.currency!r}"
                    )
            if abs(left.scale - right.scale) > 1e-9:
                ratio = right.scale / left.scale if left.scale else 1.0
                raise DerivationUnitError(
                    f"scale mismatch in addition/subtraction: operands differ by factor of {ratio:g}"
                )
            return left

        elif isinstance(node.op, ast.Mult):
            if left.dimension == DIM_RATIO and right.dimension == DIM_RATIO:
                return InferredUnit(DIM_RATIO, None, left.scale * right.scale)
            elif left.dimension == DIM_RATIO:
                return InferredUnit(right.dimension, right.currency, left.scale * right.scale)
            elif right.dimension == DIM_RATIO:
                return InferredUnit(left.dimension, left.currency, left.scale * right.scale)
            else:
                raise DerivationUnitError(
                    f"cannot multiply operands of dimension '{left.dimension}' and '{right.dimension}'; "
                    "at least one operand must be a dimensionless ratio"
                )

        elif isinstance(node.op, ast.Div):
            if left.dimension == right.dimension:
                if left.dimension in (DIM_CURRENCY, DIM_PER_SHARE):
                    if left.currency != right.currency:
                        raise DerivationUnitError(
                            f"currency mismatch in ratio division: {left.currency!r} vs {right.currency!r}"
                        )
                if abs(left.scale - right.scale) > 1e-9:
                    ratio = left.scale / right.scale if right.scale else 1.0
                    raise DerivationUnitError(
                        f"scale mismatch between numerator ({left.scale:g}) and denominator ({right.scale:g}): "
                        f"factor of {ratio:g}"
                    )
                return InferredUnit(DIM_RATIO, None, 1.0)
            elif left.dimension == DIM_CURRENCY and right.dimension == DIM_COUNT:
                return InferredUnit(DIM_PER_SHARE, left.currency, left.scale / right.scale)
            elif left.dimension == DIM_RATIO and right.dimension == DIM_RATIO:
                return InferredUnit(DIM_RATIO, None, left.scale / right.scale)
            else:
                raise DerivationUnitError(
                    f"incoherent division: numerator dimension '{left.dimension}' divided by "
                    f"denominator dimension '{right.dimension}' does not produce a valid dimension"
                )
        else:
            raise DerivationSyntaxError(f"unsupported binary operator: {type(node.op).__name__}")
    else:
        raise DerivationSyntaxError(f"unsupported AST node: {type(node).__name__}")


def check_derived_unit_coherence(
    expected_unit: Optional[str],
    inferred: InferredUnit,
) -> Tuple[bool, str]:
    """Check that the inferred expression unit matches the expected unit of the derived input."""
    if not expected_unit:
        return True, f"inferred dimension '{inferred.dimension}'"
    exp_dim = unit_dimension(expected_unit)
    if exp_dim != inferred.dimension:
        return False, (
            f"dimension mismatch: witness expects '{exp_dim}' ({expected_unit!r}) "
            f"but derivation yields dimension '{inferred.dimension}'"
        )
    if inferred.dimension in (DIM_CURRENCY, DIM_PER_SHARE):
        exp_cur = unit_currency(expected_unit)
        if exp_cur and inferred.currency and exp_cur != inferred.currency:
            return False, (
                f"currency mismatch: witness expects currency {exp_cur!r} "
                f"but derivation yields {inferred.currency!r}"
            )
    exp_scale = unit_scale(expected_unit) or 1.0
    if abs(exp_scale - inferred.scale) > 1e-9:
        return False, (
            f"scale mismatch: witness expects scale {exp_scale:g} "
            f"but derivation yields scale {inferred.scale:g}"
        )
    return True, f"unit bound: dimension '{inferred.dimension}' compatible with expected {expected_unit!r}"


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
                    "operand_names": list(self.derivation.operand_names),
                    "constants": dict(self.derivation.constants),
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

    For direct inputs (``derivation_spec is None``):
      - fact exists and is non-``None``
      - company matches expected_company
      - value equality against recorded fact value
      - unit dimension, currency, and scale compatibility
      - period and period type match
      - accounting basis compatible
      - filing identity and source reference well-formed
      - point-in-time availability at or before cutoff

    For derived inputs (``derivation_spec is not None``):
      - an inspectable Derivation is present
      - every operand fact exists in the ledger
      - every operand fact is verified for company ownership, non-null finite value,
        filing identity, source reference, point-in-time cutoff, accounting basis,
        and valid economic period
      - multi-period formula periods are anchored to expected_period and prohibit
        look-ahead/future periods
      - safe bounded AST evaluation (no eval/exec, only +, -, *, / over fact variables,
        no arbitrary constants, bounds on length/nodes/depth)
      - every variable in the expression resolves to an operand; every declared operand
        is used (no unused/missing/counterfeit operands)
      - unit coherence: addition/subtraction like dimensions, ratio division like dimensions,
        matching currency and scales, agreement with expected_unit
      - deterministic recomputation: zero division and overflow rejected
      - claimed_value and optional declared result_value must match recomputed value
        under numeric comparison policy
    """
    result = InputBinding(input_key=input_key, fact_id=fact_id)
    if expected_period is not None and (
        not isinstance(expected_period, Mapping) or expected_period_key(expected_period) is None
    ):
        result.status = "failed"
        result.problems.append(f"witness input '{input_key}' declares an invalid economic period {expected_period!r}")
        return result

    # =========================================================================
    # DERIVED BINDINGS: deterministic AST evaluation & full operand validation
    # =========================================================================
    if derivation_spec is not None:
        derivation = Derivation.from_spec(derivation_spec)
        if derivation is None:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' is marked derived but supplies no inspectable "
                "derivation (an expression plus at least one operand fact ID is required)"
            )
            return result
        result.derivation = derivation

        if not derivation.operand_fact_ids:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' derivation declares no operand fact IDs"
            )
            return result

        # If a fact_id was cited directly on the witness input, it must be an operand of the derivation
        if fact_id is not None and fact_id not in derivation.operand_fact_ids:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' cites fact '{fact_id}' which is not among "
                f"the derivation's declared operand facts {list(derivation.operand_fact_ids)}"
            )
            return result

        # Every operand fact ID must exist in the fact ledger
        missing_ops = [oid for oid in derivation.operand_fact_ids if oid not in fact_ledger]
        if missing_ops:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' derivation cites operand fact(s) {missing_ops} "
                "that do not exist in the fact ledger"
            )
            return result

        # Full provenance, availability, and accounting validation of EVERY operand
        from src.facts import usable_as_of

        operand_bases: List[str] = []
        for idx, op_id in enumerate(derivation.operand_fact_ids):
            op_fact = fact_ledger[op_id]

            # 1. Company ownership
            op_company = _fact_get(op_fact, "company")
            if expected_company is not None:
                if not op_company or str(op_company).strip().upper() != str(expected_company).strip().upper():
                    result.status = "failed"
                    result.problems.append(
                        f"witness input '{input_key}' derivation operand '{op_id}' belongs to company "
                        f"{op_company!r}, not the run's company {expected_company!r}"
                    )

            # 2. Value presence and finite numeric type
            field_name = (
                derivation.operand_fields[idx]
                if idx < len(derivation.operand_fields)
                else "value"
            )
            op_val = _fact_get(op_fact, field_name)
            if op_val is None:
                reason = _fact_get(op_fact, "missing_reason") or "no documented reason"
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' derivation operand '{op_id}' records no value "
                    f"for field {field_name!r} (missing_reason={reason!r})"
                )
            elif isinstance(op_val, bool) or not isinstance(op_val, (int, float)) or not compare_numbers(op_val, op_val)[0]:
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' derivation operand '{op_id}' carries "
                    f"invalid numeric value {op_val!r}"
                )

            # 3. SEC filing identity
            ok_id, id_detail = fact_filing_identity_valid(op_fact)
            if not ok_id:
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' derivation operand '{op_id}' {id_detail}"
                )

            # 4. Source location reference
            ok_src, src_detail = fact_source_reference_valid(op_fact)
            if not ok_src:
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' derivation operand '{op_id}' {src_detail}"
                )

            # 5. Research cutoff look-ahead check
            if cutoff is not None:
                if not usable_as_of(op_fact, cutoff):
                    avail = _fact_get(op_fact, "availability_time")
                    result.status = "failed"
                    result.problems.append(
                        f"witness input '{input_key}' derivation operand '{op_id}' availability_time "
                        f"{avail!r} is after the research cutoff {cutoff!r} (look-ahead violation)"
                    )

            # 6. Accounting basis
            op_basis = _fact_get(op_fact, "accounting_basis")
            if op_basis:
                operand_bases.append(str(op_basis))
            if expected_basis is not None:
                if not accounting_bases_compatible(expected_basis, op_basis):
                    result.status = "failed"
                    result.problems.append(
                        f"witness input '{input_key}' derivation operand '{op_id}' accounting basis "
                        f"mismatch: witness expects {expected_basis!r}, operand records {op_basis!r}"
                    )

            # 7. Economic period existence
            op_pkey = fact_period_key(op_fact)
            if op_pkey is None:
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' derivation operand '{op_id}' records no economic period"
                )

        # Cross-operand basis consistency
        if operand_bases and len(set(normalize_accounting_basis(b) for b in operand_bases)) > 1:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' derivation operands carry conflicting accounting bases: "
                f"{operand_bases}"
            )

        # Multi-period and input-period requirements
        if expected_period:
            want_key = expected_period_key(expected_period)
            if want_key is not None:
                op_keys = [fact_period_key(fact_ledger[oid]) for oid in derivation.operand_fact_ids]
                if want_key not in op_keys:
                    result.status = "failed"
                    result.problems.append(
                        f"witness input '{input_key}' period mismatch: witness expects {want_key!r}, "
                        f"but no derivation operand matches this period"
                    )
            # Operands must not belong to future periods past expected_period
            anchor = anchor_period_spec(expected_period) or {}
            exp_end = anchor.get("instant") or anchor.get("period_end")
            if exp_end:
                for oid in derivation.operand_fact_ids:
                    op_f = fact_ledger[oid]
                    op_end = _fact_get(op_f, "instant") or _fact_get(op_f, "period_end")
                    if op_end and str(op_end) > str(exp_end):
                        result.status = "failed"
                        result.problems.append(
                            f"witness input '{input_key}' derivation operand '{oid}' period {op_end!r} "
                            f"is after the expected period {exp_end!r} (future operand violation)"
                        )

        # Do not evaluate operands whose source/value/period checks failed.
        if result.status == "failed":
            return result

        # Safe AST parsing (no eval/exec, only explicit arithmetic AST nodes, bounded complexity)
        try:
            tree = _parse_and_validate_ast(derivation.expression)
        except DerivationSyntaxError as exc:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' invalid derivation expression: {exc}"
            )
            return result

        # Fact-bound variable resolution (all operands used, no counterfeit/missing operands)
        try:
            name_to_id = _resolve_derivation_variables(tree, derivation)
        except DerivationSyntaxError as exc:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' variable binding failure: {exc}"
            )
            return result

        # Coherent derived units / currencies / scales
        try:
            env_units = {}
            for name, op_id in name_to_id.items():
                op_fact = fact_ledger[op_id]
                u = _fact_get(op_fact, "unit")
                dim = unit_dimension(u)
                cur = unit_currency(u)
                scl = unit_scale(u) or 1.0
                if dim == DIM_UNKNOWN or (dim in (DIM_CURRENCY, DIM_PER_SHARE) and cur is None):
                    raise DerivationUnitError(f"operand {op_id!r} has unrecognized or ambiguous unit {u!r}")
                env_units[name] = InferredUnit(dimension=dim, currency=cur, scale=scl)
            for name, value in (getattr(derivation, "constants", None) or {}).items():
                env_units[name] = InferredUnit(dimension=DIM_RATIO, currency=None, scale=1.0)
            inferred = _infer_unit_ast_node(tree, env_units)
            ok_u, u_detail = check_derived_unit_coherence(expected_unit, inferred)
            if not ok_u:
                result.status = "failed"
                result.problems.append(f"witness input '{input_key}' {u_detail}")
            else:
                result.notes.append(f"derived unit bound: {u_detail}")
        except (DerivationUnitError, DerivationSyntaxError) as exc:
            result.status = "failed"
            result.problems.append(f"witness input '{input_key}' unit error: {exc}")

        # Deterministic arithmetic replay
        try:
            env_values = {}
            for name, op_id in name_to_id.items():
                op_idx = derivation.operand_fact_ids.index(op_id)
                field_name = (
                    derivation.operand_fields[op_idx]
                    if op_idx < len(derivation.operand_fields)
                    else "value"
                )
                env_values[name] = _fact_get(fact_ledger[op_id], field_name)
            for name, value in (getattr(derivation, "constants", None) or {}).items():
                env_values[name] = value
            recomputed = _evaluate_ast_node(tree, env_values)
        except (DerivationEvaluationError, DerivationSyntaxError) as exc:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' derivation evaluation failed: {exc}"
            )
            return result

        # Numeric comparison policy: claimed value MUST match recomputed derivation value
        ok_c, detail_c = compare_numbers(claimed_value, recomputed, rel_tol=rel_tol)
        if not ok_c:
            result.status = "failed"
            result.problems.append(
                f"witness input '{input_key}' claimed value {claimed_value!r} does not match "
                f"recomputed derived value {recomputed!r} ({detail_c}; expression={derivation.expression!r}, "
                f"policy={NUMERIC_COMPARISON_POLICY})"
            )

        # Optional declared result_value MUST match recomputed derivation value and claimed value
        if derivation.result_value is not None:
            ok_r, detail_r = compare_numbers(derivation.result_value, recomputed, rel_tol=rel_tol)
            if not ok_r:
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' declared derivation result {derivation.result_value!r} "
                    f"does not match recomputed derived value {recomputed!r} ({detail_r}; "
                    f"expression={derivation.expression!r}, policy={NUMERIC_COMPARISON_POLICY})"
                )
            ok_cr, detail_cr = compare_numbers(claimed_value, derivation.result_value, rel_tol=rel_tol)
            if not ok_cr:
                result.status = "failed"
                result.problems.append(
                    f"witness input '{input_key}' claimed value {claimed_value!r} does not match "
                    f"declared derivation result {derivation.result_value!r} ({detail_cr}; "
                    f"policy={NUMERIC_COMPARISON_POLICY})"
                )
        else:
            result.notes.append(
                f"derivation recomputed: {derivation.expression} = {recomputed} "
                f"(no declared result_value supplied)"
            )

        if result.status != "failed":
            result.bound = True
            result.status = "bound"
            result.notes.append(
                f"derived value bound: {derivation.expression} = {recomputed} over "
                f"{list(derivation.operand_fact_ids)}"
            )
        return result

    # =========================================================================
    # DIRECT BINDINGS: unchanged single-fact verification
    # =========================================================================

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
            if exp_dim == DIM_RATIO and fact_dim in (DIM_CURRENCY, DIM_COUNT):
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

    An input with no cited fact and no derivation is *unavailable*, not failed:
    a witness may legitimately record a computed index that has no single underlying
    fact, as long as it says so. What must never happen is a **sourced** or
    **derived** input whose number disagrees with the fact or formula it cites.
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
        derivation_spec = derivations.get(input_key)
        if (not fact_id or not isinstance(fact_id, str)) and derivation_spec is None:
            # No cited fact and no derivation: unverified against any typed fact.
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
        fact = fact_ledger.get(fact_id) if fact_id else None
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
                derivation_spec=derivation_spec,
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