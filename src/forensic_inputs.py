"""Runner-computed forensic index inputs, bound to typed facts.

Why this module exists
----------------------

A3 is asked for eight Beneish indices. Recomputing only the score from
model-supplied ratios does not establish the ratios' source bindings. A
deterministic score over unbound inputs is a much weaker claim than it looks.

This module computes the eight indices from the typed fact ledger instead. Every
index is produced as an inspectable derivation over named facts, so the same
binding policy that guards any other sourced number applies to it: the operands
must exist, belong to the company, carry a finite value with a coherent unit and
accounting basis, and be available at the cutoff. A ratio that cannot be built
from real facts is reported **unavailable with a reason** — never filled with a
neutral value.

Formula conventions (all stated because they are choices, not facts)
-------------------------------------------------------------------

Standard eight-variable Beneish definitions, computed on consecutive annual
periods ``t1`` (most recent) and ``t0`` (the one before):

- ``DSRI = (receivables_t1 / revenue_t1) / (receivables_t0 / revenue_t0)``
- ``GMI  = gross_margin_t0 / gross_margin_t1``
- ``AQI  = (1 - (current_assets_t1 + ppe_t1) / assets_t1)
           / (1 - (current_assets_t0 + ppe_t0) / assets_t0)``
- ``SGI  = revenue_t1 / revenue_t0``
- ``DEPI = (depreciation_t0 / (depreciation_t0 + ppe_t0))
           / (depreciation_t1 / (depreciation_t1 + ppe_t1))``
- ``SGAI = (sga_t1 / revenue_t1) / (sga_t0 / revenue_t0)``
- ``LVGI = ((current_liabilities_t1 + long_term_debt_t1) / assets_t1)
           / ((current_liabilities_t0 + long_term_debt_t0) / assets_t0)``
- ``TATA = (net_income_t1 - operating_cash_flow_t1) / assets_t1``

Two conventions are deliberately narrower than a textbook definition, and both
are reported in ``conventions`` so a reviewer can disagree with them explicitly:

1. **Leverage** is the debt-bearing measure (current liabilities plus long-term
   debt over total assets), not ``total liabilities / total assets``. Total
   liabilities is frequently not tagged as a single concept — AMD does not tag
   ``us-gaap:Liabilities`` at all — and deriving it as ``assets - equity`` would
   silently absorb noncontrolling interests and mezzanine items. The narrower
   measure is chosen because it is directly tagged, not because it is preferred.
2. **Accruals** use total assets as the scale, matching the published index.

The module does not decide whether these indices are the right economic inputs,
and it does not interpret the resulting score. Crossing the published threshold
is a screening signal, never a finding that manipulation occurred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Concept alternatives per role, in preference order. The first concept that
#: has usable facts for both periods wins, and the choice is recorded, so a
#: reader can see which tag actually backed each index.
CONCEPT_ALTERNATIVES: Dict[str, Tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "receivables": (
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
        "ReceivablesNetCurrent",
        "AccountsReceivableGrossCurrent",
    ),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": (
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsAndServicesSoldAmortization",
    ),
    "current_assets": ("AssetsCurrent",),
    "ppe_net": ("PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipment"),
    "assets": ("Assets",),
    "depreciation": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
    "sga": ("SellingGeneralAndAdministrativeExpense",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "long_term_debt": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
}

#: Every concept this module can consume, for coverage reporting.
REQUIRED_CONCEPTS: Tuple[str, ...] = tuple(
    concept for concepts in CONCEPT_ALTERNATIVES.values() for concept in concepts
)

#: Index name -> the roles it needs, split by period.
_INDEX_ROLES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "dsri": (("receivables", "revenue"), ("receivables", "revenue")),
    "gmi": (("gross_profit", "cost_of_revenue", "revenue"), ("gross_profit", "cost_of_revenue", "revenue")),
    "aqi": (("current_assets", "ppe_net", "assets"), ("current_assets", "ppe_net", "assets")),
    "sgi": (("revenue",), ("revenue",)),
    "depi": (("depreciation", "ppe_net"), ("depreciation", "ppe_net")),
    "sgai": (("sga", "revenue"), ("sga", "revenue")),
    "lvgi": (("current_liabilities", "long_term_debt", "assets"), ("current_liabilities", "long_term_debt", "assets")),
    "tata": (("net_income", "operating_cash_flow", "assets"), ()),
}

BENEISH_INDEX_ORDER: Tuple[str, ...] = ("dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata")


@dataclass(frozen=True)
class ForensicInputsResult:
    """Computed indices plus the binding metadata a witness needs."""

    indices: Mapping[str, float] = field(default_factory=dict)
    fact_ids: Mapping[str, str] = field(default_factory=dict)
    derivations: Mapping[str, Any] = field(default_factory=dict)
    periods: Mapping[str, Any] = field(default_factory=dict)
    bases: Mapping[str, str] = field(default_factory=dict)
    roles: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    unavailable_reason: Optional[str] = None
    conventions: Mapping[str, str] = field(default_factory=dict)
    #: The reporting dates the computation actually paired, as
    #: ``{"t1": {...}, "t0": {...}}``. Recorded because "which two periods did
    #: this index compare" is the question a reviewer asks first, and it cannot
    #: be recovered from the index value.
    period_ends: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        return (
            self.unavailable_reason is None
            and len(self.indices) == len(BENEISH_INDEX_ORDER)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feasible": self.feasible,
            "indices": dict(self.indices),
            "fact_ids": dict(self.fact_ids),
            "derivations": dict(self.derivations),
            "periods": dict(self.periods),
            "bases": dict(self.bases),
            "roles": {key: dict(value) for key, value in self.roles.items()},
            "conventions": dict(self.conventions),
            "period_ends": {key: dict(value) for key, value in self.period_ends.items()},
            "unavailable_reason": self.unavailable_reason,
        }


def _get(fact: Any, name: str) -> Any:
    if isinstance(fact, Mapping):
        return fact.get(name)
    return getattr(fact, name, None)


def _period_fields(fact: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    return (
        _get(fact, "period_start"),
        _get(fact, "period_end"),
        _get(fact, "instant"),
    )


def _duration_days(fact: Any) -> Optional[int]:
    start, end, _ = _period_fields(fact)
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except ValueError:
        return None


def _is_annual(fact: Any) -> bool:
    days = _duration_days(fact)
    return days is not None and 330 <= days <= 400


def _is_instant(fact: Any) -> bool:
    start, end, instant = _period_fields(fact)
    return bool(instant) and not (start and end)


def _basis_of(fact: Any) -> str:
    basis = _get(fact, "accounting_basis")
    value = getattr(basis, "value", basis)
    return str(value) if value is not None else ""


def _period_key(fact: Any) -> str:
    start, end, instant = _period_fields(fact)
    if start and end:
        return f"duration:{start}/{end}"
    return f"instant:{instant or end or start}"


def _period_spec(fact: Any) -> Dict[str, str]:
    start, end, instant = _period_fields(fact)
    if start and end:
        return {"period_start": str(start), "period_end": str(end)}
    return {"instant": str(instant or end or start)}


def _rank(fact: Any) -> Tuple:
    """Preference order for competing observations of the same period.

    A restated figure from a later filing is preferred over the original, and an
    annual report is preferred over an interim one that happens to restate the
    same annual period. Ties break on fact id so the choice is deterministic.
    """
    start, end, instant = _period_fields(fact)
    return (
        str(end or instant or start or ""),
        str(_get(fact, "availability_time") or ""),
        str(_get(fact, "filing_accession") or ""),
        str(_get(fact, "id") or ""),
    )


def _select(
    ledger: Mapping[str, Any],
    concept: str,
    *,
    annual: bool,
    period_end: Optional[str] = None,
) -> Optional[Any]:
    """Return the preferred usable fact for ``concept``, or None.

    ``period_end`` pins the selection to one reporting date. Roles that feed the
    same index must describe the *same* period: taking the newest available
    receivables instant and the newest available revenue duration independently
    would silently compute a ratio across mismatched periods.
    """
    candidates: List[Any] = []
    for fact in ledger.values():
        if str(_get(fact, "concept") or "") != concept:
            continue
        if _get(fact, "value") is None:
            continue
        if _basis_of(fact).endswith("OTHER"):
            continue
        if annual:
            if not _is_annual(fact):
                continue
            if period_end is not None and str(_get(fact, "period_end") or "") != period_end:
                continue
        else:
            if not _is_instant(fact):
                continue
            if period_end is not None and str(_get(fact, "instant") or "") != period_end:
                continue
        candidates.append(fact)
    if not candidates:
        return None
    return max(candidates, key=_rank)


def _period_ends(ledger: Mapping[str, Any], concept: str, *, annual: bool) -> List[str]:
    """Distinct reporting dates for ``concept``, newest first."""
    ends = set()
    for fact in ledger.values():
        if str(_get(fact, "concept") or "") != concept:
            continue
        if _get(fact, "value") is None:
            continue
        if annual:
            if not _is_annual(fact):
                continue
            ends.add(str(_get(fact, "period_end") or ""))
        else:
            if not _is_instant(fact):
                continue
            ends.add(str(_get(fact, "instant") or ""))
    return sorted((end for end in ends if end), reverse=True)


def _reference_periods(
    ledger: Mapping[str, Any],
    *,
    annual: bool,
) -> List[str]:
    """Candidate reporting dates, newest first, from the roles that anchor a period.

    The anchor concept is the one every index shares: revenue for durations,
    total assets for instants. Anchoring on it keeps the two periods aligned
    across all eight indices instead of each index picking its own.
    """
    role = "revenue" if annual else "assets"
    return sorted({end for concept in CONCEPT_ALTERNATIVES[role]
                   for end in _period_ends(ledger, concept, annual=annual)}, reverse=True)


def validate_forensic_witness(witness: Mapping[str, Any], ledger: Mapping[str, Any]) -> List[str]:
    """Check the fiscal years and comparable concepts used by recorded derivations."""
    derivations = witness.get("derivations")
    if not isinstance(derivations, Mapping) or not ledger:
        return []
    parameters = witness.get("parameters")
    runner_computed = isinstance(parameters, Mapping) and parameters.get("index_source") == "runner_computed"
    pairs = {
        "dsri": ("recv", "rev"), "gmi": ("rev",), "aqi": ("ca", "ppe", "assets"),
        "sgi": ("rev",), "depi": ("dep", "ppe"), "sgai": ("sga", "rev"),
        "lvgi": ("cl", "ltd", "assets"),
    }
    errors: List[str] = []
    for index, roles in pairs.items():
        spec = derivations.get(index)
        if not isinstance(spec, Mapping):
            if runner_computed:
                errors.append(f"{index} is missing its runner derivation")
            continue
        names = spec.get("operand_names") or []
        ids = spec.get("operand_fact_ids") or []
        if (not isinstance(names, (list, tuple)) or not isinstance(ids, (list, tuple))
                or not all(isinstance(value, str) for value in (*names, *ids))):
            errors.append(f"{index} has malformed derivation operands")
            continue
        operands = dict(zip(names, ids))
        for role in roles:
            current_id, prior_id = operands.get(f"{role}_t1"), operands.get(f"{role}_t0")
            if not current_id or not prior_id:
                if runner_computed:
                    errors.append(f"{index} is missing comparable {role} operands")
                continue
            current, prior = ledger.get(current_id), ledger.get(prior_id)
            if current is None or prior is None:
                continue  # Binding validation reports missing facts.
            current_concept, prior_concept = _get(current, "concept"), _get(prior, "concept")
            if current_concept != prior_concept:
                errors.append(f"{index} uses incomparable {role} concepts across years")
            if role == "sga" and (current_concept != "SellingGeneralAndAdministrativeExpense"
                                  or prior_concept != "SellingGeneralAndAdministrativeExpense"):
                errors.append("sgai requires selling, general and administrative expenses in both years")
            if index == "sgi" and role == "rev":
                try:
                    gap = (date.fromisoformat(str(_get(current, "period_end")))
                           - date.fromisoformat(str(_get(prior, "period_end")))).days
                    if not 330 <= gap <= 400:
                        errors.append("sgi requires consecutive annual periods")
                except ValueError:
                    errors.append("sgi requires valid consecutive annual periods")
    return errors


def _aligned_periods(
    instant_ends: Sequence[str],
    annual_ends: Sequence[str],
) -> Tuple[List[str], bool]:
    """Pair balance-sheet dates with fiscal-year ends.

    An index that divides a balance-sheet instant by a duration is only
    comparable when both describe the same reporting date. Taking the newest
    interim balance sheet (say a Q2 instant) over the newest annual duration
    (the last fiscal year) computes a real ratio over a mismatched period — the
    arithmetic replays and the answer is still meaningless.

    Returns the instant dates matched to the two newest annual periods and
    whether both could be matched within seven days.
    """
    # 52/53-week fiscal year drift tolerance:
    # Some filers date the balance sheet on Saturday or month-end (e.g. 2024-12-31
    # vs 2024-12-28). Pair each annual duration end with the closest instant within 7 days.
    matched: List[str] = []
    for a_end in annual_ends[:2]:
        try:
            a_date = date.fromisoformat(str(a_end))
        except ValueError:
            continue
        candidates: List[Tuple[int, str]] = []
        for i_end in instant_ends:
            try:
                i_date = date.fromisoformat(str(i_end))
                diff = abs((i_date - a_date).days)
                if diff <= 7:
                    candidates.append((diff, str(i_end)))
            except ValueError:
                continue
        if candidates:
            candidates.sort()
            matched.append(candidates[0][1])
    if len(matched) == 2 and len(set(matched)) == 2:
        return matched, True

    return list(instant_ends), False


def _revenue_and_margin_facts(
    ledger: Mapping[str, Any],
    period_end: str,
) -> Tuple[Optional[Any], Optional[Any]]:
    """Return (gross_profit, cost_of_revenue) for the revenue period ``period_end``.

    Gross margin needs a numerator and a denominator for the *same* period as the
    revenue used by DSRI/SGI, otherwise the indices would silently mix periods. A
    missing pair is unavailable, not approximated.
    """
    gross = _select(ledger, "GrossProfit", annual=True, period_end=period_end)
    if gross is not None:
        return gross, None
    cost = None
    for concept in CONCEPT_ALTERNATIVES["cost_of_revenue"]:
        candidate = _select(ledger, concept, annual=True, period_end=period_end)
        if candidate is not None:
            cost = candidate
            break
    return None, cost


def compute_forensic_inputs(
    ledger: Mapping[str, Any],
    *,
    as_of: Optional[Any] = None,
) -> ForensicInputsResult:
    """Compute the eight Beneish indices from typed facts.

    ``ledger`` maps fact id -> fact (a mapping or an object with attributes).
    ``as_of`` is accepted for API symmetry with the rest of the pipeline; the
    caller is expected to have already filtered the ledger to the cutoff, and
    this module never reaches past what it is given.
    """
    from src.fact_binding import fact_period_key
    from src.utils.beneish import beneish_result

    conventions = {
        "leverage": (
            "LVGI uses current liabilities plus long-term debt over total assets. "
            "Total liabilities is not tagged by every issuer, so the narrower "
            "debt-bearing measure is used rather than deriving it as assets minus equity."
        ),
        "accruals": "TATA scales total accruals by total assets for the most recent annual period.",
        "periods": "Indices compare consecutive annual periods; the most recent annual period is t1.",
        "preference": (
            "Where several filings report the same period, the latest restatement "
            "at or before the cutoff is used and the chosen concept is recorded per role."
        ),
    }

    # --- resolve one fact per role per period ------------------------------
    # Both periods are anchored on shared concepts (total assets for instants,
    # revenue for durations) so every index compares the same two reporting
    # dates rather than each index choosing its own.
    instant_ends = _reference_periods(ledger, annual=False)
    annual_ends = _reference_periods(ledger, annual=True)
    if len(instant_ends) < 2 or len(annual_ends) < 2:
        return ForensicInputsResult(
            unavailable_reason=(
                "Fewer than two eligible reporting periods are available for "
                f"{'total assets' if len(instant_ends) < 2 else 'revenue'}; a "
                "change index requires a current and a prior period."
            ),
            conventions=conventions,
        )
    if not 330 <= (date.fromisoformat(annual_ends[0]) - date.fromisoformat(annual_ends[1])).days <= 400:
        return ForensicInputsResult(
            unavailable_reason="The two latest annual periods are not consecutive fiscal years.",
            conventions=conventions,
        )
    instant_ends, fiscal_year_aligned = _aligned_periods(instant_ends, annual_ends)
    if not fiscal_year_aligned:
        return ForensicInputsResult(
            unavailable_reason=(
                "Balance-sheet instants cannot be aligned within seven days of "
                "both latest annual period ends."
            ),
            conventions=conventions,
        )
    conventions["period_alignment"] = (
        "Balance-sheet instants align within seven days of their respective "
        "fiscal-year-end dates."
    )
    period_ends = {
        "t1": {"instant": instant_ends[0], "duration": annual_ends[0]},
        "t0": {"instant": instant_ends[1], "duration": annual_ends[1]},
    }

    facts: Dict[str, Dict[str, Any]] = {"t1": {}, "t0": {}}
    concepts: Dict[str, Dict[str, str]] = {"t1": {}, "t0": {}}
    missing: List[str] = []

    def need(role: str, period: str, annual: bool) -> Optional[Any]:
        if role in facts[period]:
            return facts[period][role]
        period_type = "duration" if annual else "instant"
        for concept in CONCEPT_ALTERNATIVES[role]:
            pair = {p: _select(ledger, concept, annual=annual, period_end=period_ends[p][period_type])
                    for p in ("t1", "t0")}
            if all(pair.values()):
                for p, fact in pair.items():
                    facts[p][role] = fact
                    concepts[p][role] = concept
                return facts[period][role]
        missing.append(f"{role} (no comparable facts for both periods)")
        return None

    for period in ("t1", "t0"):
        for role in ("receivables", "current_assets", "ppe_net", "assets", "current_liabilities", "long_term_debt"):
            need(role, period, False)
        for role in ("revenue", "sga", "depreciation", "net_income", "operating_cash_flow"):
            need(role, period, True)

    # Gross margin: prefer a tagged gross profit, else derive from revenue and cost.
    for period in ("t1", "t0"):
        gross, cost = _revenue_and_margin_facts(ledger, period_ends[period]["duration"])
        if gross is not None:
            facts[period]["gross_profit"] = gross
            concepts[period]["gross_profit"] = "GrossProfit"
        elif cost is not None:
            facts[period]["cost_of_revenue"] = cost
            concepts[period]["cost_of_revenue"] = str(_get(cost, "concept"))
        else:
            missing.append(f"gross_profit or cost_of_revenue ({period} @ {period_ends[period]['duration']})")

    if missing:
        return ForensicInputsResult(
            unavailable_reason=(
                "These inputs are not available as tagged facts at the analysis cutoff: "
                + ", ".join(sorted(set(missing)))
                + ". A missing index is reported unavailable rather than filled with a "
                "neutral ratio."
            ),
            conventions=conventions,
        )

    # --- build derivations -------------------------------------------------
    derivations: Dict[str, Any] = {}
    indices: Dict[str, float] = {}
    fact_ids: Dict[str, str] = {}
    periods: Dict[str, Any] = {}
    bases: Dict[str, str] = {}
    roles_out: Dict[str, Dict[str, str]] = {}

    def bind(index: str, expression: str, operands: Sequence[Tuple[str, str]], constants: Optional[Mapping[str, float]] = None) -> None:
        """Evaluate one index over named operands and record its derivation."""
        env: Dict[str, float] = {}
        spec: Dict[str, Any] = {
            "expression": expression,
            "operand_fact_ids": [fact_id for _, fact_id in operands],
            "operand_names": [name for name, _ in operands],
        }
        if constants:
            spec["constants"] = dict(constants)
        for name, fact_id in operands:
            env[name] = float(_get(ledger[fact_id], "value"))
        for name, value in (constants or {}).items():
            env[name] = float(value)
        value = eval_expression(expression, env)
        indices[index] = value
        derivations[index] = spec
        fact_ids[index] = operands[0][1]

    def role_id(period: str, role: str) -> str:
        return str(_get(facts[period][role], "id"))

    # DSRI
    bind(
        "dsri",
        "(recv_t1 / rev_t1) / (recv_t0 / rev_t0)",
        [
            ("recv_t1", role_id("t1", "receivables")),
            ("rev_t1", role_id("t1", "revenue")),
            ("recv_t0", role_id("t0", "receivables")),
            ("rev_t0", role_id("t0", "revenue")),
        ],
    )
    # GMI — each period can use either gross profit or revenue less cost.
    margins: Dict[str, str] = {}
    margin_operands: List[Tuple[str, str]] = []
    for period in ("t0", "t1"):
        revenue = (f"rev_{period}", role_id(period, "revenue"))
        if "gross_profit" in facts[period]:
            margins[period] = f"gp_{period} / rev_{period}"
            margin_operands.extend([(f"gp_{period}", role_id(period, "gross_profit")), revenue])
        else:
            margins[period] = f"(rev_{period} - cost_{period}) / rev_{period}"
            margin_operands.extend([revenue, (f"cost_{period}", role_id(period, "cost_of_revenue"))])
    bind("gmi", f"({margins['t0']}) / ({margins['t1']})", margin_operands)
    # AQI
    bind(
        "aqi",
        "(one - (ca_t1 + ppe_t1) / assets_t1) / (one - (ca_t0 + ppe_t0) / assets_t0)",
        [
            ("ca_t1", role_id("t1", "current_assets")),
            ("ppe_t1", role_id("t1", "ppe_net")),
            ("assets_t1", role_id("t1", "assets")),
            ("ca_t0", role_id("t0", "current_assets")),
            ("ppe_t0", role_id("t0", "ppe_net")),
            ("assets_t0", role_id("t0", "assets")),
        ],
        {"one": 1.0},
    )
    # SGI
    bind(
        "sgi",
        "rev_t1 / rev_t0",
        [
            ("rev_t1", role_id("t1", "revenue")),
            ("rev_t0", role_id("t0", "revenue")),
        ],
    )
    # DEPI
    bind(
        "depi",
        "(dep_t0 / (dep_t0 + ppe_t0)) / (dep_t1 / (dep_t1 + ppe_t1))",
        [
            ("dep_t0", role_id("t0", "depreciation")),
            ("ppe_t0", role_id("t0", "ppe_net")),
            ("dep_t1", role_id("t1", "depreciation")),
            ("ppe_t1", role_id("t1", "ppe_net")),
        ],
    )
    # SGAI
    bind(
        "sgai",
        "(sga_t1 / rev_t1) / (sga_t0 / rev_t0)",
        [
            ("sga_t1", role_id("t1", "sga")),
            ("rev_t1", role_id("t1", "revenue")),
            ("sga_t0", role_id("t0", "sga")),
            ("rev_t0", role_id("t0", "revenue")),
        ],
    )
    # LVGI — debt-bearing leverage, see module docstring.
    bind(
        "lvgi",
        "((cl_t1 + ltd_t1) / assets_t1) / ((cl_t0 + ltd_t0) / assets_t0)",
        [
            ("cl_t1", role_id("t1", "current_liabilities")),
            ("ltd_t1", role_id("t1", "long_term_debt")),
            ("assets_t1", role_id("t1", "assets")),
            ("cl_t0", role_id("t0", "current_liabilities")),
            ("ltd_t0", role_id("t0", "long_term_debt")),
            ("assets_t0", role_id("t0", "assets")),
        ],
    )
    # TATA
    bind(
        "tata",
        "(ni_t1 - ocf_t1) / assets_t1",
        [
            ("ni_t1", role_id("t1", "net_income")),
            ("ocf_t1", role_id("t1", "operating_cash_flow")),
            ("assets_t1", role_id("t1", "assets")),
        ],
    )

    # --- periods and bases per index ---------------------------------------
    for index, spec in derivations.items():
        used_periods: Dict[str, Any] = {}
        used_bases: List[str] = []
        for fact_id in spec["operand_fact_ids"]:
            fact = ledger[fact_id]
            used_periods[_period_key(fact)] = _period_spec(fact)
            used_bases.append(_basis_of(fact))
        periods[index] = used_periods
        distinct = sorted(set(used_bases))
        if len(distinct) != 1:
            return ForensicInputsResult(
                unavailable_reason=(
                    f"Index '{index}' would mix accounting bases {distinct}; a ratio over "
                    "inconsistent bases is not a comparable measure."
                ),
                conventions=conventions,
            )
        bases[index] = distinct[0]

    # --- arithmetic self-check before anything is reported ------------------
    result = beneish_result(**{key: indices[key] for key in BENEISH_INDEX_ORDER})
    if result.get("raw_score") is None:
        return ForensicInputsResult(
            unavailable_reason="The computed indices did not produce a finite Beneish score.",
            conventions=conventions,
        )

    for index in BENEISH_INDEX_ORDER:
        roles_out[index] = {
            "t1": {
                role: role_id("t1", role)
                for role in _INDEX_ROLES[index][0]
                if role in facts["t1"]
            },
            "t0": {
                role: role_id("t0", role)
                for role in _INDEX_ROLES[index][1]
                if role in facts["t0"]
            },
        }
        # Keyed per period: a role can resolve to a different concept in t1
        # than in t0, and a flat map kept only one of them.
        roles_out[index]["concepts"] = {
            "t1": {role: concepts["t1"].get(role, "") for role in _INDEX_ROLES[index][0]},
            "t0": {role: concepts["t0"].get(role, "") for role in _INDEX_ROLES[index][1]},
        }

    return ForensicInputsResult(
        indices=indices,
        fact_ids=fact_ids,
        derivations=derivations,
        periods=periods,
        bases=bases,
        roles=roles_out,
        conventions=conventions,
        period_ends=period_ends,
    )


def eval_expression(expression: str, env: Mapping[str, float]) -> float:
    """Evaluate a bound arithmetic expression through the shared safe evaluator.

    The derivation engine in :mod:`src.fact_binding` is the single implementation
    of bounded arithmetic in this codebase. Re-implementing it here would let the
    value the runner computes drift from the value the audit replays.
    """
    import ast as _ast

    from src.fact_binding import _evaluate_ast_node, _parse_and_validate_ast

    tree = _parse_and_validate_ast(expression)
    if not isinstance(tree, _ast.Expression):  # pragma: no cover - defensive
        raise ValueError("derivation expression did not parse to an expression")
    return _evaluate_ast_node(tree.body, env)


def forensic_inputs_for(
    ledger: Mapping[str, Any],
    *,
    as_of: Optional[Any] = None,
) -> Dict[str, Any]:
    """Compute forensic inputs for prompt/context use, never raising.

    An exception here would abort a run over an *optional enrichment*. A
    computation that cannot be completed is returned as an explicit
    unavailable result carrying its reason, which the prompt then states and the
    model must not paper over.
    """
    try:
        return compute_forensic_inputs(ledger, as_of=as_of).to_dict()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed silently
        return {
            "feasible": False,
            "indices": {},
            "unavailable_reason": (
                f"Forensic index computation failed locally ({type(exc).__name__}: {exc}). "
                "No index is reported; they must not be guessed."
            ),
        }


def coverage_of(ledger: Mapping[str, Any]) -> Dict[str, Any]:
    """Report which required concepts the ledger actually carries."""
    present = {str(_get(fact, "concept") or "") for fact in ledger.values()}
    return {
        "required_concepts_present": sorted(set(REQUIRED_CONCEPTS) & present),
        "required_concepts_absent": sorted(set(REQUIRED_CONCEPTS) - present),
    }
