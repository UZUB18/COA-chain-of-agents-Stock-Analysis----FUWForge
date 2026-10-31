"""End-to-end acceptance on the saved AMD live-run dataset, offline.

This is the acceptance test the repair order calls for: it exercises one real,
saved dataset through the whole repaired path and asserts the four conditions
that were previously false.

1. **Required facts reach the prompt.** The intake bound discarded whole
   concepts before prompt selection could see them, so revenue, net income,
   operating cash flow, capex and SG&A had zero ledger entries.
2. **The real statement is selected.** The cash-flow excerpt was truncated
   mid-table (and the selector could pick a note that merely *mentions* the
   statement), so the retrieved filing looked as though the statement were
   missing.
3. **All eight forensic inputs bind.** Every Beneish index is computed by the
   runner from named facts, with periods and accounting bases recorded, and
   replays under the same binding policy as any other sourced number.
4. **Recoverable responses avoid provider calls.** A response already received
   is re-processed locally instead of being dispatched again.

Nothing here touches the network or a provider: the fixtures contain the
CompanyFacts payload and filing bytes the 2026-10-11 run already downloaded.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.registry import model_registry
from src.calculation_witness import beneish_witness, validate_witness
from src.evidence import EvidencePack, _context_excerpt, _select_prompt_facts
from src.fact_binding import binding_failures, verify_witness_bindings
from src.facts import filter_facts_as_of
from src.forensic_inputs import BENEISH_INDEX_ORDER, compute_forensic_inputs
from src.sec_facts import parse_sec_company_facts_report
from src.utils.beneish import beneish_result

AMD_CUTOFF = "2026-10-11"
#: The 10-K/A the run read, whose cash-flow statement is the one that appeared missing.
AMD_ANNUAL = "D002"


@pytest.fixture(scope="module")
def amd_ledger(amd_evidence_dir):
    report = parse_sec_company_facts_report(amd_evidence_dir / "sec_companyfacts.json", ticker="AMD")
    return {fact.id: fact for fact in filter_facts_as_of(report.facts, AMD_CUTOFF)}


@pytest.fixture(scope="module")
def amd_pack(amd_evidence_dir):
    return EvidencePack(amd_evidence_dir / "evidence.json", ticker="AMD", analysis_date=AMD_CUTOFF)


class _NoNetwork:
    """Fail the test if anything opens a socket."""

    def __enter__(self):
        self._original = socket.socket.connect
        socket.socket.connect = self._blocked
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._original
        return False

    @staticmethod
    def _blocked(*args, **kwargs):  # pragma: no cover - only on violation
        raise AssertionError("acceptance must run entirely offline")


class _NeverCalledBackend:
    model = "never-called"

    def generate(self, prompt: str) -> str:  # pragma: no cover - only on violation
        raise AssertionError("acceptance must not call a provider")

    def generate_envelope(self, prompt: str, schema):  # pragma: no cover
        raise AssertionError("acceptance must not call a provider")


# ================================================== 1. required facts reach the prompt


def test_condition_1_required_facts_reach_the_prompt(amd_ledger):
    """
    Each required statement row is in the ledger *and* in the prompt projection.

    Both halves matter: the ledger is what the audit binds against, and the
    projection is what the model can actually see.
    """
    selected, active_count = _select_prompt_facts(list(amd_ledger.values()))
    # ``active_count`` is the eligible set after superseded observations are
    # removed, so it is legitimately smaller than the raw ledger.
    assert 0 < active_count <= len(amd_ledger)
    assert len(selected) <= active_count
    projected = {fact.concept for fact in selected}

    required = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
        "NetIncomeLoss": "net income",
        "NetCashProvidedByUsedInOperatingActivities": "operating cash flow",
        "PaymentsToAcquirePropertyPlantAndEquipment": "capital expenditure",
        "SellingGeneralAndAdministrativeExpense": "SG&A",
        "GrossProfit": "gross profit",
        "CostOfGoodsAndServicesSold": "cost of revenue",
        "AccountsReceivableNetCurrent": "receivables",
        "Assets": "total assets",
        "AssetsCurrent": "current assets",
        "LiabilitiesCurrent": "current liabilities",
        "LongTermDebtNoncurrent": "long-term debt",
        "PropertyPlantAndEquipmentNet": "net PP&E",
        "DepreciationDepletionAndAmortization": "depreciation and amortization",
    }
    for concept, label in required.items():
        assert concept in {fact.concept for fact in amd_ledger.values()}, (
            f"{label} ({concept}) never reached the ledger"
        )
        assert concept in projected, f"{label} ({concept}) is not in the prompt projection"

    # Each required concept must appear with at least two distinct periods, or a
    # change index cannot be built from what the model was shown.
    for concept in (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
    ):
        periods = {
            fact.period_key() for fact in selected if fact.concept == concept
        }
        assert len(periods) >= 2, f"{concept} was projected for only {len(periods)} period(s)"


def test_condition_1_intake_loses_no_concept_to_the_budget(amd_evidence_dir):
    """The bound must not discard whole concepts, and must say so if it does."""
    report = parse_sec_company_facts_report(amd_evidence_dir / "sec_companyfacts.json", ticker="AMD")
    coverage = report.coverage
    assert coverage.truncated is False
    assert coverage.concepts_dropped_by_limit == ()
    # Concepts the issuer genuinely did not tag remain visible as such, which is
    # a different statement from "our budget discarded it".
    assert "Liabilities" in coverage.priority_concepts_missing_from_payload


# ================================================== 2. the real statement is selected


def test_condition_2_the_cash_flow_statement_is_selected_complete(amd_pack):
    """
    The retrieved 10-K/A does contain the cash-flow statement, and the excerpt
    that reaches the prompt must contain the whole statement rather than its
    first section.
    """
    document = next(d for d in amd_pack.documents if d["id"] == AMD_ANNUAL)
    text = amd_pack._file(document["text_file"]).read_text(encoding="utf-8")
    assert "Consolidated Statements of Cash Flows" in text

    excerpt = _context_excerpt(text, 80000 // 8)
    offsets = [
        int(match.group(1))
        for match in __import__("re").finditer(r"\[STATEMENT EXCERPT AT TEXT OFFSET (\d+)\]", excerpt)
    ]
    assert offsets, "no statement excerpt was selected from the annual filing"

    cash_flow = [
        offset
        for offset in offsets
        if "Consolidated Statements of Cash Flows" in text[offset:offset + 60]
    ]
    assert cash_flow, (
        "the cash-flow statement heading was not selected; the selector chose "
        "some other occurrence of the phrase"
    )
    for offset in cash_flow:
        body = excerpt.split(f"[STATEMENT EXCERPT AT TEXT OFFSET {offset}]")[1]
        body = body.split("\n[DOCUMENT EXCERPT")[0].upper()
        # A statement cut after the operating section is what made the filing
        # look as though the statement had not been retrieved at all.
        for section in ("OPERATING ACTIVITIES", "INVESTING ACTIVITIES", "FINANCING ACTIVITIES"):
            assert section in body, f"cash-flow excerpt is missing {section}"


def test_condition_2_statement_selection_prefers_headings_over_references():
    """
    A note that says "…as the cash flows of the related items" is not the
    statement. Heading shape must beat raw nearby numeric density, and this note
    is placed *earlier* in the document and given *more* numbers so that density
    alone would choose it.
    """
    text = "\n".join(
        [
            "Item 8. Financial Statements",
            "",
            "Note 5. Leases",
            "The Company classifies the Consolidated Statement of Cash Flows as the cash "
            "flows of the related items. Amounts were 1,234 2,345 3,456 4,567 5,678 "
            "6,789 7,890 8,901 9,012 1,111 2,222 3,333 4,444 5,555 6,666 7,777 8,888 "
            "9,999 1,010 2,020 3,030 4,040 5,050 6,060 7,070 8,080 9,090 1,100 2,100 "
            "3,100 4,100 5,100 6,100 7,100 8,100 9,100",
            "",
            "Consolidated Statements of Cash Flows",
            "Year Ended December 27, 2025",
            "Operating activities",
            "Net income 1,000 900",
            "Investing activities (500 ) (400 )",
            "Financing activities 200 100",
        ]
    )
    statement_offset = text.index("Consolidated Statements of Cash Flows")
    excerpt = _context_excerpt(text, 400)
    assert f"[STATEMENT EXCERPT AT TEXT OFFSET {statement_offset}]" in excerpt
    assert "Year Ended December 27, 2025" in excerpt
    assert "Financing activities 200 100" in excerpt
    assert "as the cash flows of the related items" not in excerpt


# ================================================== 3. all eight forensic inputs bind


def test_condition_3_all_eight_forensic_inputs_bind(amd_ledger):
    result = compute_forensic_inputs(amd_ledger, as_of=AMD_CUTOFF)
    assert result.feasible, result.unavailable_reason
    assert set(result.indices) == set(BENEISH_INDEX_ORDER)

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
    bindings = verify_witness_bindings(
        witness,
        fact_ledger=amd_ledger,
        expected_company="AMD",
        cutoff=AMD_CUTOFF,
        expected_periods=result.periods,
        expected_bases=result.bases,
    )
    assert binding_failures(bindings) == []
    assert sum(1 for b in bindings if b.bound) == 8
    # The audit's core counters read exactly these two numbers.
    assert len(BENEISH_INDEX_ORDER) == 8


def test_condition_3_every_index_records_its_periods_and_basis(amd_ledger):
    result = compute_forensic_inputs(amd_ledger, as_of=AMD_CUTOFF)
    for index in BENEISH_INDEX_ORDER:
        assert result.periods[index], index
        assert result.bases[index] == "US-GAAP", index
        assert result.derivations[index]["operand_fact_ids"], index
        for fact_id in result.derivations[index]["operand_fact_ids"]:
            assert fact_id in amd_ledger, f"{index} cites a fact outside the ledger"


def test_condition_3_the_runner_result_reaches_a3_as_a_bound_artifact(amd_ledger, tmp_path):
    """The prompt states the indices; the artifact is deterministic and replayable."""
    from src.agents.model import _normalize_beneish

    result = compute_forensic_inputs(amd_ledger, as_of=AMD_CUTOFF)
    resolved = _normalize_beneish({"feasible": True}, [], result.to_dict())
    assert resolved["provenance"] == "deterministic"
    assert resolved["index_source"] == "runner_computed"
    witness = resolved["calculation_witness"]
    assert set(witness["inputs"]) == set(BENEISH_INDEX_ORDER)
    assert validate_witness(
        witness, fact_ledger=amd_ledger, cutoff=AMD_CUTOFF, expected_company="AMD"
    ) == []

    context = AgentContext(
        run_id="acceptance",
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
        forensic_inputs=result.to_dict(),
    )
    agent = model_registry(_NeverCalledBackend(), provider="recording", strict=True).resolve("A3")
    prompt = agent.build_prompt(context)
    assert "RUNNER-COMPUTED FORENSIC INPUTS" in prompt
    for key in BENEISH_INDEX_ORDER:
        assert f'"{key}"' in prompt


# ================================================== 4. the whole path is offline


def test_condition_4_acceptance_runs_offline_end_to_end(amd_ledger, amd_pack, amd_evidence_dir):
    """Every condition above, re-derived with network access blocked."""
    with _NoNetwork():
        report = parse_sec_company_facts_report(amd_evidence_dir / "sec_companyfacts.json", ticker="AMD")
        ledger = {
            fact.id: fact for fact in filter_facts_as_of(report.facts, AMD_CUTOFF)
        }
        selected, _active = _select_prompt_facts(list(ledger.values()))
        result = compute_forensic_inputs(ledger, as_of=AMD_CUTOFF)
        witness = beneish_witness(
            indices=result.indices,
            fact_ids=result.fact_ids,
            result=beneish_result(**result.indices)["raw_score"],
            derivations=result.derivations,
            input_periods=result.periods,
            input_bases=result.bases,
        ).to_dict()

        document = next(d for d in amd_pack.documents if d["id"] == AMD_ANNUAL)
        text = amd_pack._file(document["text_file"]).read_text(encoding="utf-8")
        excerpt = _context_excerpt(text, 80000 // 8)

    assert len(ledger) > 20000
    assert {fact.concept for fact in selected} >= {
        "Revenues",
        "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
    }
    assert result.feasible
    assert validate_witness(
        witness, fact_ledger=ledger, cutoff=AMD_CUTOFF, expected_company="AMD"
    ) == []
    assert "Consolidated Statements of Cash Flows" in excerpt
    assert "FINANCING ACTIVITIES" in excerpt.upper()


def test_amd_forensics_pass_source_backed_run_audit(amd_evidence_dir, tmp_path):
    from src.agents.mock import A3Mock
    from src.agents.model import _normalize_beneish
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
    from src.run_semantic_audit import semantic_audit

    class BoundA3(A3Mock):
        def run(self, context):
            output = super().run(context)
            assert context.forensic_inputs["feasible"]
            output.envelope.artifacts["BeneishM"] = _normalize_beneish(
                {"feasible": True}, [], context.forensic_inputs
            )
            return output

    orchestrator = FUWForgeOrchestrator(
        PROJECT_ROOT, provider="mock", strict=True,
        evidence_pack=amd_evidence_dir / "evidence.json",
    )
    orchestrator.registry.register_instance(BoundA3())
    params = OrchestratorParams(
        company="Advanced Micro Devices, Inc.", ticker="AMD", analysis_date=AMD_CUTOFF,
        depth="Quick", focus=[], weights="default",
    )
    with _NoNetwork():
        summary = orchestrator.run("amd-offline", params, output_root=tmp_path / "run")
        report = semantic_audit(tmp_path / "run", require_source_backed=True)
    assert summary["status"] == "completed"
    assert report.overall_state == "verified", report.errors
    assert report.core_witnesses_replayed == 1
    assert report.core_inputs_verified == 8


def test_condition_4_recoverable_responses_avoid_provider_calls(tmp_path):
    """
    The recovery half of the acceptance criterion, on the same saved payload: a
    response the run already received is re-processed locally, and the backend
    is never asked for a completion.
    """
    from src.execution_store import ExecutionStore
    from src.orchestrator import FUWForgeOrchestrator

    calls = {"n": 0}

    class _CountingBackend:
        model = "never-called"

        def generate(self, prompt: str) -> str:  # pragma: no cover
            calls["n"] += 1
            raise AssertionError("a received response must not be dispatched again")

    store = ExecutionStore(tmp_path / "ledger.db")
    key = store.begin(
        agent_id="A1",
        provider="mock",
        model="never-called",
        prompt_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "e" * 64,
        upstream=(),
    )
    payload = {
        "schema_version": "2.1",
        "agent": {"id": "A1", "name": "Harvester-&-Validator"},
        "gates": {"passed": True, "notes": []},
        "sections_md": {"RAW_DATA_PACK": "# Harvest"},
        "artifacts": {
            "price_timestamp_eet": "2026-10-09T16:00:00Z",
            "shares_basic": 1632475042.0,
            "shares_diluted": 1659000000.0,
            "market_cap_check": {"price": 608.10, "shares": 1632475042.0, "cap": 992688035728.0},
        },
        "sources": [],
        "warnings": [],
        "metrics": {"data_checksum": "sha256:abc"},
        "confidence": 0.9,
    }
    store.mark_returned(key, {"raw": json.dumps(payload)})

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="never-called")
    orchestrator.registry = model_registry(_CountingBackend(), provider="recording", strict=False)
    context = AgentContext(
        run_id="acceptance_recovery",
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
    )

    with _NoNetwork():
        execution = orchestrator._recover_saved_response(
            "A1", store.get(key), context, store, key, {}, (), {}
        )

    assert execution is not None
    assert calls["n"] == 0
    assert execution.raw_response["recovered_locally"] is True
    assert execution.envelope.artifacts["market_cap_check"]["price"] == 608.10
