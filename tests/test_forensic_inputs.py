"""Runner-computed forensic inputs, bound to typed facts.

Two defects motivate these tests, and both are about *where a number comes from*
rather than whether the arithmetic is right:

1. **Intake dropped the concepts the indices need.** CompanyFacts iteration is
   alphabetical and the record budget was spent inside ``Deferred*`` concepts, so
   ``Revenues``, ``NetIncomeLoss``,
   ``NetCashProvidedByUsedInOperatingActivities`` and
   ``SellingGeneralAndAdministrativeExpense`` reached the ledger with zero
   entries. No downstream prompt selection can recover a concept that was never
   ingested, so the indices were unavailable no matter how the prompt was
   written. ``tests/test_sec_facts_intake.py`` pins the intake ordering;
   these tests pin the consequence for the eight indices.

2. **The indices were the model's own arithmetic.** The runner recomputed the
   *score* from model-supplied ratios but could not check the ratios, so a
   plausible-looking index with no source was indistinguishable from a real one.

The acceptance evidence is the saved AMD CompanyFacts payload from the
2026-10-11 live run. It is reused offline: no network, no provider call, no
credential or local run directory is needed.
"""

from __future__ import annotations

from dataclasses import replace
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.model import _normalize_beneish
from src.calculation_witness import beneish_witness, validate_witness
from src.fact_binding import (
    Derivation,
    binding_failures,
    expected_period_key,
    verify_witness_bindings,
)
from src.facts import filter_facts_as_of
from src.forensic_inputs import (
    BENEISH_INDEX_ORDER,
    compute_forensic_inputs,
    forensic_inputs_for,
)
from src.sec_facts import parse_sec_company_facts_report
from src.utils.beneish import beneish_result

AMD_CUTOFF = "2026-10-11"

@pytest.fixture(scope="module")
def amd_ledger(amd_evidence_dir):
    report = parse_sec_company_facts_report(amd_evidence_dir / "sec_companyfacts.json", ticker="AMD")
    return {fact.id: fact for fact in filter_facts_as_of(report.facts, AMD_CUTOFF)}


@pytest.fixture(scope="module")
def amd_inputs(amd_ledger):
    return compute_forensic_inputs(amd_ledger, as_of=AMD_CUTOFF)


# ================================================== 1. intake no longer starves the indices


def test_core_statement_concepts_reach_the_ledger(amd_ledger):
    """
    The four concepts the live run reported as having zero entries.

    This is the acceptance condition for the intake repair: the concepts must
    exist as facts, not merely exist in the downloaded payload.
    """
    concepts = {fact.concept for fact in amd_ledger.values()}
    for concept in (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "SellingGeneralAndAdministrativeExpense",
    ):
        assert concept in concepts, f"{concept} did not reach the typed fact ledger"


def test_prompt_projection_carries_every_required_index_input(amd_ledger):
    """
    The 100-fact prompt projection must include the inputs the indices need.

    A ledger that contains a concept is not enough: the model has to see it.
    """
    from src.evidence import _select_prompt_facts

    selected, _active = _select_prompt_facts(list(amd_ledger.values()))
    concepts = {fact.concept for fact in selected}
    for concept in (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "GrossProfit",
        "CostOfGoodsAndServicesSold",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "SellingGeneralAndAdministrativeExpense",
        "AccountsReceivableNetCurrent",
        "Assets",
        "AssetsCurrent",
        "LiabilitiesCurrent",
        "LongTermDebtNoncurrent",
        "PropertyPlantAndEquipmentNet",
        "StockholdersEquity",
    ):
        assert concept in concepts, f"{concept} is absent from the prompt projection"


def test_intake_reports_no_concept_dropped_by_the_default_budget(amd_evidence_dir):
    """The default budget must not silently discard whole concepts."""
    report = parse_sec_company_facts_report(amd_evidence_dir / "sec_companyfacts.json", ticker="AMD")
    coverage = report.coverage
    assert coverage.truncated is False
    assert coverage.concepts_dropped_by_limit == ()
    assert coverage.records_retained == coverage.records_seen
    # What the issuer genuinely did not tag stays visible as such.
    assert "Liabilities" in coverage.priority_concepts_missing_from_payload


# ================================================== 2. the eight indices are runner-computed


def test_all_eight_indices_are_computed_from_bound_facts(amd_inputs, amd_ledger):
    assert amd_inputs.feasible, amd_inputs.unavailable_reason
    assert set(amd_inputs.indices) == set(BENEISH_INDEX_ORDER)
    for value in amd_inputs.indices.values():
        assert isinstance(value, float)

    witness = beneish_witness(
        indices=amd_inputs.indices,
        fact_ids=amd_inputs.fact_ids,
        result=beneish_result(**amd_inputs.indices)["raw_score"],
        derivations=amd_inputs.derivations,
        input_periods=amd_inputs.periods,
        input_bases=amd_inputs.bases,
    ).to_dict()
    assert validate_witness(
        witness, fact_ledger=amd_ledger, cutoff=AMD_CUTOFF, expected_company="AMD"
    ) == []

    bindings = verify_witness_bindings(
        witness,
        fact_ledger=amd_ledger,
        expected_company="AMD",
        cutoff=AMD_CUTOFF,
        expected_periods=amd_inputs.periods,
        expected_bases=amd_inputs.bases,
    )
    assert binding_failures(bindings) == []
    assert [b.status for b in bindings] == ["bound"] * 8
    assert {b.input_key for b in bindings if b.bound} == set(BENEISH_INDEX_ORDER)


def test_every_operand_is_a_real_ledger_fact(amd_inputs, amd_ledger):
    for index, spec in amd_inputs.derivations.items():
        assert spec["operand_fact_ids"], index
        for fact_id in spec["operand_fact_ids"]:
            assert fact_id in amd_ledger, f"{index} cites {fact_id} which is not in the ledger"
        # Operand names are declared, so the audit can map expression variables
        # back to facts without guessing positional aliases.
        assert len(spec["operand_names"]) == len(spec["operand_fact_ids"])


def test_balance_sheet_dates_match_fiscal_year_ends(amd_inputs):
    """
    A balance-sheet instant divided by a duration is only comparable when both
    describe the same reporting date. The newest interim balance sheet over the
    newest annual duration computes a real ratio over a mismatched period.
    """
    assert "fiscal-year-end" in amd_inputs.conventions["period_alignment"]
    annual_ends = {
        amd_inputs.period_ends["t1"]["duration"],
        amd_inputs.period_ends["t0"]["duration"],
    }
    for period, ends in amd_inputs.period_ends.items():
        # Every balance-sheet date must be one of the fiscal-year ends the index
        # also uses as a flow period end. Without this, a Q2 balance divided by
        # a fiscal-year flow would bind and still be economically meaningless.
        assert ends["instant"] in annual_ends, (
            f"{period} pairs balance-sheet date {ends['instant']} with flow periods "
            f"{sorted(annual_ends)}"
        )
    for index, spec in amd_inputs.periods.items():
        instant_dates = {
            key.split(":", 1)[1] for key in spec if key.startswith("instant:")
        }
        duration_ends = {
            key.split("/", 1)[1] for key in spec if key.startswith("duration:")
        }
        if not instant_dates or not duration_ends:
            # An index of instants only (AQI) or flows only (GMI) has nothing to
            # align; the pairing check above covers the mixed case.
            continue
        assert instant_dates <= duration_ends, (
            f"{index} pairs balance-sheet dates {sorted(instant_dates)} with "
            f"period ends {sorted(duration_ends)}"
        )


def test_indices_match_independent_recomputation(amd_inputs, amd_ledger):
    """Each index recomputes from the cited facts, not merely from its own claim."""
    for index, spec in amd_inputs.derivations.items():
        values = {}
        for name, fact_id in zip(spec["operand_names"], spec["operand_fact_ids"]):
            values[name] = float(amd_ledger[fact_id].value)
        for name, constant in (spec.get("constants") or {}).items():
            values[name] = float(constant)
        from src.forensic_inputs import eval_expression

        recomputed = eval_expression(spec["expression"], values)
        assert recomputed == pytest.approx(amd_inputs.indices[index], rel=1e-12), index


# ================================================== 3. failure is disclosed, never filled


def _fact(**overrides):
    from src.facts import AccountingBasis, Fact, FactKind

    base = dict(
        id="fact_test_revenues_duration_2024_01_01_2024_12_31_000000000000000000",
        company="TEST",
        concept="Revenues",
        unit="USD",
        currency="USD",
        value=1000.0,
        period_start="2024-01-01",
        period_end="2024-12-31",
        instant=None,
        filing_accession="0000000000-25-000001",
        availability_time="2025-02-01T23:59:59Z",
        retrieval_time="2026-01-01T00:00:00Z",
        source_location={"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000000.json"},
        accounting_basis=AccountingBasis.US_GAAP,
        extractor_version="sec_companyfacts_v1",
        kind=FactKind.REPORTED,
    )
    base.update(overrides)
    return Fact(**base)


@pytest.fixture
def complete_ledger():
    ledger = {}
    for year, revenue in ((2022, 800), (2023, 1000), (2024, 1200)):
        for concept, value in {
            "Revenues": revenue,
            "GrossProfit": revenue * 0.4,
            "CostOfGoodsAndServicesSold": revenue * 0.6,
            "SellingGeneralAndAdministrativeExpense": revenue * 0.1,
            "DepreciationDepletionAndAmortization": revenue * 0.05,
            "NetIncomeLoss": revenue * 0.1,
            "NetCashProvidedByUsedInOperatingActivities": revenue * 0.08,
        }.items():
            fact = _fact(id=f"{concept}_{year}", concept=concept, value=value,
                         period_start=f"{year}-01-01", period_end=f"{year}-12-31")
            ledger[fact.id] = fact
        for concept, value in {
            "AccountsReceivableNetCurrent": revenue * 0.1,
            "AssetsCurrent": 500,
            "PropertyPlantAndEquipmentNet": 400,
            "Assets": 2000,
            "LiabilitiesCurrent": 200,
            "LongTermDebtNoncurrent": 200,
        }.items():
            fact = _fact(id=f"{concept}_{year}", concept=concept, value=value,
                         period_start=None, period_end=None, instant=f"{year}-12-31")
            ledger[fact.id] = fact
    return ledger


def test_missing_middle_fiscal_year_does_not_produce_a_change_index(complete_ledger):
    ledger = {key: fact for key, fact in complete_ledger.items() if not key.endswith("_2023")}
    result = compute_forensic_inputs(ledger)
    assert not result.feasible
    assert "consecutive" in result.unavailable_reason.lower()
    assert not result.indices


def test_incomparable_expense_concepts_do_not_produce_sgai(complete_ledger):
    ledger = dict(complete_ledger)
    previous = ledger.pop("SellingGeneralAndAdministrativeExpense_2023")
    partial = replace(previous, id="GeneralAndAdministrativeExpense_2023",
                      concept="GeneralAndAdministrativeExpense", value=50.0)
    ledger[partial.id] = partial
    result = compute_forensic_inputs(ledger)
    assert not result.feasible
    assert "sga" in result.unavailable_reason.lower()
    assert not result.indices


def test_general_and_administrative_alone_is_not_total_sga(complete_ledger):
    ledger = dict(complete_ledger)
    for year in (2023, 2024):
        old = ledger.pop(f"SellingGeneralAndAdministrativeExpense_{year}")
        partial = replace(old, id=f"GeneralAndAdministrativeExpense_{year}",
                          concept="GeneralAndAdministrativeExpense")
        ledger[partial.id] = partial
    assert not compute_forensic_inputs(ledger).feasible


def test_revenue_tag_with_one_year_does_not_hide_complete_common_tag(complete_ledger):
    ledger = dict(complete_ledger)
    current = ledger["Revenues_2024"]
    extra = replace(current, id="RevenueFromContractWithCustomerExcludingAssessedTax_2024",
                    concept="RevenueFromContractWithCustomerExcludingAssessedTax")
    ledger[extra.id] = extra
    result = compute_forensic_inputs(ledger)
    assert result.feasible, result.unavailable_reason
    assert result.indices["sgi"] == pytest.approx(1.2)
    assert result.roles["sgi"]["t1"]["revenue"] == "Revenues_2024"


@pytest.mark.parametrize("defect", ["year_gap", "mixed_sga"])
def test_audit_rejects_previously_recorded_incomparable_runner_inputs(complete_ledger, defect):
    import copy
    from src.envelope import Envelope
    from src.forensic_inputs import _period_key, _period_spec, eval_expression
    from src.run_semantic_audit import replay_beneish

    original = compute_forensic_inputs(complete_ledger)
    derivations = copy.deepcopy(original.derivations)
    ledger = dict(complete_ledger)
    if defect == "year_gap":
        ledger = {key: fact for key, fact in ledger.items() if not key.endswith("_2023")}
        for spec in derivations.values():
            spec["operand_fact_ids"] = [value.replace("_2023", "_2022") for value in spec["operand_fact_ids"]]
    else:
        old = ledger.pop("SellingGeneralAndAdministrativeExpense_2023")
        partial = replace(old, id="GeneralAndAdministrativeExpense_2023",
                          concept="GeneralAndAdministrativeExpense", value=50.0)
        ledger[partial.id] = partial
        spec = derivations["sgai"]
        spec["operand_fact_ids"] = [partial.id if value == old.id else value for value in spec["operand_fact_ids"]]

    values, periods, fact_ids = {}, {}, {}
    for key, spec in derivations.items():
        operands = [ledger[fid] for fid in spec["operand_fact_ids"]]
        env = {name: float(fact.value) for name, fact in zip(spec["operand_names"], operands)}
        env.update(spec.get("constants", {}))
        values[key] = eval_expression(spec["expression"], env)
        periods[key] = {_period_key(fact): _period_spec(fact) for fact in operands}
        fact_ids[key] = operands[0].id
    assert values["sgi" if defect == "year_gap" else "sgai"] == pytest.approx(1.5 if defect == "year_gap" else 2.0)
    artifact = beneish_result(**values)
    artifact["provenance"] = "deterministic"
    artifact["calculation_witness"] = beneish_witness(
        indices=values, fact_ids=fact_ids, result=artifact["raw_score"], derivations=derivations,
        input_periods=periods, input_bases=original.bases, parameters={"index_source": "runner_computed"},
    ).to_dict()
    errors = replay_beneish(Envelope.from_dict({"artifacts": {"BeneishM": artifact}}),
                            ledger, "2026-10-11", "TEST")
    assert any(("consecutive" if defect == "year_gap" else "sga") in error.lower() for error in errors)


def test_malformed_forensic_witness_is_reported_without_crashing(complete_ledger):
    from src.forensic_inputs import validate_forensic_witness

    witness = {"parameters": "invalid", "derivations": {"sgi": {"operand_names": 7, "operand_fact_ids": []}}}
    assert validate_forensic_witness(witness, complete_ledger)


@pytest.mark.parametrize("use_cost", [False, True])
def test_gross_margin_index_is_ratio_not_change(complete_ledger, use_cost):
    if use_cost:
        complete_ledger = {k: v for k, v in complete_ledger.items() if v.concept != "GrossProfit"}
    result = compute_forensic_inputs(complete_ledger)
    assert result.feasible, result.unavailable_reason
    assert result.indices["gmi"] == pytest.approx(1.0)


@pytest.mark.parametrize("missing_year", [2023, 2024])
def test_gross_margin_can_use_different_fact_types_in_each_year(complete_ledger, missing_year):
    del complete_ledger[f"GrossProfit_{missing_year}"]
    result = compute_forensic_inputs(complete_ledger)
    assert result.feasible, result.unavailable_reason
    assert result.indices["gmi"] == pytest.approx(1.0)
    witness = beneish_witness(
        indices=result.indices,
        fact_ids=result.fact_ids,
        result=beneish_result(**result.indices)["raw_score"],
        derivations=result.derivations,
        input_periods=result.periods,
        input_bases=result.bases,
    ).to_dict()
    assert validate_witness(witness, fact_ledger=complete_ledger, cutoff="2026-10-11", expected_company="TEST") == []


def test_missing_latest_balance_sheet_does_not_mix_years(complete_ledger):
    del complete_ledger["Assets_2024"]
    result = compute_forensic_inputs(complete_ledger)
    assert not result.feasible
    assert "align" in result.unavailable_reason.lower()
    assert not result.indices


def test_most_recent_periods_take_precedence_over_older_exact_matches():
    from src.forensic_inputs import _aligned_periods

    aligned, is_aligned = _aligned_periods(
        ["2025-12-31", "2024-12-28", "2023-12-30"],
        ["2025-12-27", "2024-12-28", "2023-12-30"],
    )
    assert is_aligned
    assert aligned == ["2025-12-31", "2024-12-28"]


def test_missing_inputs_yield_unavailable_not_neutral_ratios():
    """Two eligible periods exist, but the roles inside them do not: unavailable."""
    ledger = {
        "rev24": _fact(),
        "rev23": _fact(
            id="fact_test_revenues_duration_2023_01_01_2023_12_31_000000000000000000",
            period_start="2023-01-01",
            period_end="2023-12-31",
            value=900.0,
        ),
        "assets24": _fact(
            id="fact_test_assets_instant_2024_12_31_000000000000000000",
            concept="Assets",
            period_start=None,
            period_end=None,
            instant="2024-12-31",
        ),
        "assets23": _fact(
            id="fact_test_assets_instant_2023_12_31_000000000000000000",
            concept="Assets",
            period_start=None,
            period_end=None,
            instant="2023-12-31",
        ),
    }
    result = compute_forensic_inputs(ledger)
    assert result.feasible is False
    assert result.indices == {}
    assert "not available as tagged facts" in result.unavailable_reason
    # The disclosure names what is missing rather than only that something is.
    assert "receivables" in result.unavailable_reason


def test_a_single_period_is_not_enough_for_a_change_index():
    ledger = {
        "a": _fact(),
        "b": _fact(
            id="fact_test_assets_instant_2024_12_31_000000000000000000",
            concept="Assets",
            period_start=None,
            period_end=None,
            instant="2024-12-31",
        ),
    }
    result = compute_forensic_inputs(ledger)
    assert result.feasible is False
    assert "two eligible reporting periods" in result.unavailable_reason


def test_forensic_inputs_for_reports_local_failure_without_raising():
    class Exploding(dict):
        def values(self):  # pragma: no cover - deliberately hostile input
            raise RuntimeError("boom")

    payload = forensic_inputs_for(Exploding())
    assert payload["feasible"] is False
    assert "boom" in payload["unavailable_reason"]


# ================================================== 4. named constants stay reviewable


def test_named_constants_are_recorded_and_replay():
    derivation = Derivation.from_spec(
        {
            "expression": "a / b - one",
            "operand_fact_ids": ["a", "b"],
            "operand_names": ["a", "b"],
            "constants": {"one": 1.0},
        }
    )
    assert derivation is not None
    assert derivation.constants == {"one": 1.0}
    from src.forensic_inputs import eval_expression

    assert eval_expression(derivation.expression, {"a": 3.0, "b": 2.0, "one": 1.0}) == 0.5


def test_a_non_finite_constant_is_rejected():
    assert Derivation.from_spec(
        {
            "expression": "a * k",
            "operand_fact_ids": ["a"],
            "operand_names": ["a"],
            "constants": {"k": float("nan")},
        }
    ) is None


def test_an_expression_of_only_constants_is_rejected():
    assert Derivation.from_spec(
        {
            "expression": "one + one",
            "operand_fact_ids": ["a"],
            "constants": {"one": 1.0},
        }
    ) is not None  # parses; the variable binder rejects it, not the parser
    from src.fact_binding import DerivationSyntaxError, _resolve_derivation_variables

    derivation = Derivation.from_spec(
        {"expression": "one + one", "operand_fact_ids": ["a"], "constants": {"one": 1.0}}
    )
    import ast

    tree = ast.parse("one + one", mode="eval")
    with pytest.raises(DerivationSyntaxError):
        _resolve_derivation_variables(tree, derivation)


def test_multi_period_input_periods_anchor_on_the_current_period():
    """
    A change index spans two periods. The binder checks one anchor period — the
    most recent — so the declaration stays meaningful without pretending a
    two-period index has a single period.
    """
    spec = {
        "instant:2024-12-31": {"instant": "2024-12-31"},
        "duration:2024-01-01/2024-12-31": {
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
        },
        "instant:2023-12-31": {"instant": "2023-12-31"},
        "duration:2023-01-01/2023-12-31": {
            "period_start": "2023-01-01",
            "period_end": "2023-12-31",
        },
    }
    # Both the instant and the duration end on 2024-12-31; either is a valid
    # anchor. What matters is that the anchor is the *most recent* period and
    # that a reversed declaration is not silently accepted.
    assert expected_period_key(spec) in (
        "duration:2024-01-01/2024-12-31",
        "instant:2024-12-31",
    )
    reversed_spec = {
        "duration:2023-01-01/2023-12-31": {
            "period_start": "2023-01-01",
            "period_end": "2023-12-31",
        },
        "duration:2024-01-01/2024-12-31": {
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
        },
    }
    assert expected_period_key(reversed_spec) == "duration:2024-01-01/2024-12-31"
    # A single flat spec still behaves exactly as before.
    assert expected_period_key({"instant": "2024-12-31"}) == "instant:2024-12-31"


# ================================================== 5. the runner's result reaches the artifact


def test_runner_inputs_resolve_a3_beneish_without_a_provider_number(amd_inputs):
    """
    A3 that supplies no indices at all must still yield a bound, replayable
    artifact — and it must be labelled as runner-computed, not as model-reported.
    """
    resolved = _normalize_beneish({"feasible": True}, [], amd_inputs)
    assert resolved["provenance"] == "deterministic"
    assert resolved["index_source"] == "runner_computed"
    witness = resolved["calculation_witness"]
    assert witness["provenance"] == "deterministic"
    assert set(witness["inputs"]) == set(BENEISH_INDEX_ORDER)
    assert set(witness["derivations"]) == set(BENEISH_INDEX_ORDER)
    assert witness["input_fact_ids"] == dict(amd_inputs.fact_ids)


def test_rounded_model_indices_use_source_bound_runner_values(amd_inputs, amd_ledger):
    supplied = {key: round(value, 6) for key, value in amd_inputs.indices.items()}
    resolved = _normalize_beneish({"indices": supplied}, [], amd_inputs)
    witness = resolved["calculation_witness"]
    assert resolved["index_source"] == "runner_computed"
    assert witness["inputs"] == amd_inputs.indices
    assert validate_witness(witness, fact_ledger=amd_ledger, cutoff=AMD_CUTOFF, expected_company="AMD") == []
    from src.envelope import Envelope
    from src.run_semantic_audit import replay_beneish

    counters = {"witnesses_attempted": 0, "witnesses_replayed": 0, "inputs_verified": 0, "inputs_unverified": 0}
    envelope = Envelope.from_dict({"artifacts": {"BeneishM": resolved}})
    assert replay_beneish(envelope, amd_ledger, AMD_CUTOFF, "AMD", counters=counters) == []
    assert counters["inputs_verified"] == 8
    assert counters["inputs_unverified"] == 0


def test_material_model_index_disagreement_is_rejected(amd_inputs):
    supplied = dict(amd_inputs.indices)
    supplied["dsri"] += 0.1
    with pytest.raises(ValueError, match="dsri.*disagree"):
        _normalize_beneish({"indices": supplied}, [], amd_inputs)


def test_a_partial_runner_result_does_not_produce_a_score(amd_inputs):
    partial = dict(amd_inputs.to_dict())
    partial["indices"] = {k: v for k, v in amd_inputs.indices.items() if k != "tata"}
    resolved = _normalize_beneish({"feasible": True}, [], partial)
    assert resolved["feasible"] is False
    assert resolved["score"] is None


def test_a3_prompt_states_the_runner_computed_indices(amd_inputs, tmp_path):
    """The model must be told which numbers are the runner's, and not to re-derive them."""
    from src.agents.registry import model_registry

    class _NeverCalledBackend:
        model = "never"

        def generate(self, prompt: str) -> str:  # pragma: no cover
            raise AssertionError("prompt construction must not call the provider")

    context = AgentContext(
        run_id="prompt_only",
        params={
            "ticker": "AMD",
            "company": "Advanced Micro Devices, Inc.",
            "analysis_date": AMD_CUTOFF,
            "weights": "default",
        },
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:frozen",
        workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        source_documents=[],
        source_text="",
        evidence_checksum="sha256:pack",
        forensic_inputs=amd_inputs.to_dict(),
    )
    agent = model_registry(_NeverCalledBackend(), provider="recording", strict=True).resolve("A3")
    prompt = agent.build_prompt(context)
    assert "RUNNER-COMPUTED FORENSIC INPUTS" in prompt
    assert "Do NOT recompute or adjust them" in prompt
    for key in BENEISH_INDEX_ORDER:
        assert f'"{key}"' in prompt


def test_unavailable_runner_inputs_are_disclosed_in_the_prompt(tmp_path):
    from src.agents.registry import model_registry

    class _NeverCalledBackend:
        model = "never"

        def generate(self, prompt: str) -> str:  # pragma: no cover
            raise AssertionError("prompt construction must not call the provider")

    context = AgentContext(
        run_id="prompt_unavailable",
        params={"ticker": "AMD", "analysis_date": AMD_CUTOFF, "weights": "default"},
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:frozen",
        workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        forensic_inputs={
            "feasible": False,
            "indices": {},
            "unavailable_reason": "no eligible periods",
        },
    )
    agent = model_registry(_NeverCalledBackend(), provider="recording", strict=True).resolve("A3")
    prompt = agent.build_prompt(context)
    assert "RUNNER-COMPUTED FORENSIC INPUTS: unavailable" in prompt
    assert "no eligible periods" in prompt
    assert "Do not supply index ratios of your own" in prompt


def test_acceptance_replay_is_offline_and_calls_no_provider(amd_ledger, amd_inputs):
    """
    The whole acceptance path is local: parse saved bytes, compute, bind, replay.

    This test fails loudly if any step reaches for the network or a provider.
    """
    import socket

    def _blocked(*args, **kwargs):  # pragma: no cover - only on violation
        raise AssertionError("acceptance replay must not open a network connection")

    original = socket.socket.connect
    socket.socket.connect = _blocked
    try:
        result = compute_forensic_inputs(amd_ledger, as_of=AMD_CUTOFF)
        witness = beneish_witness(
            indices=result.indices,
            fact_ids=result.fact_ids,
            result=beneish_result(**result.indices)["raw_score"],
            derivations=result.derivations,
            input_periods=result.periods,
            input_bases=result.bases,
        ).to_dict()
        assert validate_witness(
            witness, fact_ledger=amd_ledger, cutoff=AMD_CUTOFF, expected_company="AMD"
        ) == []
    finally:
        socket.socket.connect = original
    assert result.feasible


def test_aligned_periods_52_53_week_drift():
    """Balance-sheet instants within 7 days of annual ends must align and skip interim quarters."""
    from src.forensic_inputs import _aligned_periods

    # Interim Q2 instant (2025-06-30) plus month-end annual instants (2024-12-31, 2023-12-31),
    # where the fiscal year ends on the preceding Saturday (2024-12-28, 2023-12-30).
    instant_ends = ["2025-06-30", "2024-12-31", "2023-12-31"]
    annual_ends = ["2024-12-28", "2023-12-30"]
    aligned, is_aligned = _aligned_periods(instant_ends, annual_ends)
    assert is_aligned is True
    assert aligned == ["2024-12-31", "2023-12-31"]

