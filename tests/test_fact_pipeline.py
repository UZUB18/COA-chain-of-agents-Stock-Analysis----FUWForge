"""
Comprehensive offline production-path tests for the fact pipeline:
- SEC companyfacts ingestion with accession preservation, exact units/context/periods,
  available-at precision limitations, and amended original history.
- Restatement / supersession tracking with point-in-time as-of resolution.
- Arelle optional deferral of unsupported custom/dimensional XBRL concepts.
- Evidence fact ledger exposure and anti-look-ahead as-of filtering.
- A1 / A3 prompt construction, epistemic discipline (no forecasts as reported).
- A3 deterministic calculation linking with calculation witness and fact IDs.
- _normalize_beneish invalid scalar handling (no 0.0 fallback for unparseable scalars).
- Public-source discovery for amendments and material events with caps and provenance.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import pytest

from src.facts import (
    AccountingBasis,
    Fact,
    FactKind,
    check_ledger_consistency,
    filter_facts_as_of,
    flag_concept_unit_mismatch,
    flag_unit_mismatch,
    load_fact_ledger,
    parse_iso_datetime,
    resolve_as_of,
    usable_as_of,
    validate_fact,
)
from src.sec_facts import (
    EXTRACTOR_VERSION_SEC,
    ingest_company_facts_file,
    is_arelle_available,
    parse_sec_company_facts,
)
from src.evidence import EvidencePack, download_public_document, fetch_evidence
from src.agents.model import (
    ModelAgent,
    _normalize_beneish,
    _parse_numeric,
    _parse_numeric_or_none,
)
from src.agents.mock import MockAgent
from src.agents.base import AgentContext
from src.calculation_witness import (
    COMPUTED_BY_DETERMINISTIC,
    PROVENANCE_MODEL_REPORTED,
    beneish_witness,
)
from src.public_data import _filing_rows, _select_filings, collect_public_sources
from src.utils.beneish import beneish_score, beneish_result


# ----------------------------------------------------------------------
# 1. Offline SEC CompanyFacts Ingestion Fixture Tests
# ----------------------------------------------------------------------

SAMPLE_SEC_COMPANYFACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "label": "Entity Common Stock, Shares Outstanding",
                "description": "Common shares outstanding",
                "units": {
                    "shares": [
                        {
                            "end": "2023-10-20",
                            "val": 15550061000,
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-03",
                            "frame": "CY2023Q3I",
                            "accn": "0000320193-23-000106",
                        }
                    ]
                },
            }
        },
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "description": "Revenue from operations",
                "units": {
                    "USD": [
                        {
                            "start": "2022-10-01",
                            "end": "2023-09-30",
                            "val": 383285000000,
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-03",
                            "frame": "CY2023",
                            "accn": "0000320193-23-000106",
                        },
                        {
                            "start": "2022-10-01",
                            "end": "2023-09-30",
                            "val": 383280000000,
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K/A",
                            "filed": "2024-11-01",
                            "frame": "CY2023",
                            "accn": "0000320193-24-000106",
                        },
                    ]
                },
            },
            "NetIncomeLoss": {
                "label": "Net Income (Loss)",
                "description": "Net income",
                "units": {
                    "USD": [
                        {
                            "start": "2022-10-01",
                            "end": "2023-09-30",
                            "val": 96995000000,
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-03",
                            "frame": "CY2023",
                            "accn": "0000320193-23-000106",
                        }
                    ]
                },
            },
        },
        "custom-taxonomy": {
            "CustomProductRevenue": {
                "label": "Custom Product Breakdown",
                "units": {
                    "USD": [
                        {
                            "start": "2022-10-01",
                            "end": "2023-09-30",
                            "val": 50000000,
                            "dimensions": {"SegmentAxis": "CustomSubUnit"},
                            "filed": "2023-11-03",
                            "accn": "0000320193-23-000106",
                        }
                    ]
                },
            }
        },
    },
}


def test_sec_companyfacts_ingestion_preserves_accession_units_and_precision():
    facts = parse_sec_company_facts(SAMPLE_SEC_COMPANYFACTS, ticker="AAPL")
    assert len(facts) >= 3

    # Check revenues original fact
    rev_original = next(
        f for f in facts if f.concept == "Revenues" and f.filing_accession == "0000320193-23-000106"
    )
    assert rev_original.company == "AAPL"
    assert rev_original.value == 383285000000.0
    assert rev_original.unit == "USD"
    assert rev_original.currency == "USD"
    assert rev_original.period_start == "2022-10-01"
    assert rev_original.period_end == "2023-09-30"
    assert rev_original.instant is None
    assert rev_original.accounting_basis == AccountingBasis.US_GAAP
    assert rev_original.extractor_version == EXTRACTOR_VERSION_SEC
    assert rev_original.kind == FactKind.REPORTED
    assert rev_original.supersedes is None

    # Preserves available-at precision limitation in metadata
    assert rev_original.availability_time == "2023-11-03T00:00:00Z"
    assert rev_original.metadata is not None
    assert rev_original.metadata.get("availability_precision") == "date_only_intraday_unspecified"
    assert rev_original.metadata.get("date_basis") == "SEC_filing_date"

    # Check instant fact (EntityCommonStockSharesOutstanding)
    shares_fact = next(f for f in facts if f.concept == "EntityCommonStockSharesOutstanding")
    assert shares_fact.instant == "2023-10-20"
    assert shares_fact.period_start is None
    assert shares_fact.unit == "shares"
    assert shares_fact.currency is None
    assert shares_fact.value == 15550061000.0


def test_amended_original_history_and_supersession():
    facts = parse_sec_company_facts(SAMPLE_SEC_COMPANYFACTS, ticker="AAPL")

    rev_original = next(
        f for f in facts if f.concept == "Revenues" and f.filing_accession == "0000320193-23-000106"
    )
    rev_amended = next(
        f for f in facts if f.concept == "Revenues" and f.filing_accession == "0000320193-24-000106"
    )

    assert rev_original.kind == FactKind.REPORTED
    assert rev_original.supersedes is None

    assert rev_amended.kind == FactKind.RESTATEMENT
    assert rev_amended.supersedes == rev_original.id
    assert rev_amended.value == 383280000000.0

    # Point-in-time as-of resolution
    # 1. As of 2023-12-31: returns original fact
    res_2023 = resolve_as_of(facts, "Revenues", "2023-12-31T00:00:00Z")
    assert res_2023 is not None
    assert res_2023.id == rev_original.id
    assert res_2023.value == 383285000000.0

    # 2. As of 2024-11-02: returns restated fact
    res_2024 = resolve_as_of(facts, "Revenues", "2024-11-02T00:00:00Z")
    assert res_2024 is not None
    assert res_2024.id == rev_amended.id
    assert res_2024.value == 383280000000.0


def test_custom_dimensional_deferral_without_fabrication():
    facts = parse_sec_company_facts(SAMPLE_SEC_COMPANYFACTS, ticker="AAPL", defer_dimensional=True)
    custom_facts = [f for f in facts if f.concept == "CustomProductRevenue"]
    assert len(custom_facts) == 1
    deferred = custom_facts[0]
    assert deferred.value is None
    assert deferred.is_unavailable()
    assert "Deferred: unsupported custom or dimensional" in str(deferred.missing_reason)


def test_load_fact_ledger_auto_ingests_sec_companyfacts(tmp_path):
    json_path = tmp_path / "companyfacts.json"
    json_path.write_text(json.dumps(SAMPLE_SEC_COMPANYFACTS), encoding="utf-8")

    facts = load_fact_ledger(json_path)
    assert len(facts) >= 3
    assert any(f.concept == "Revenues" for f in facts)
    assert any(f.concept == "NetIncomeLoss" for f in facts)


# ----------------------------------------------------------------------
# 2. As-Of Filtering and Epistemic Discipline (No Forecasts as Reported)
# ----------------------------------------------------------------------

def test_anti_look_ahead_and_forecast_separation():
    reported_fact = Fact(
        id="fact_hist_1",
        company="AAPL",
        concept="Revenues",
        unit="USD",
        currency="USD",
        value=100000.0,
        period_start="2024-01-01",
        period_end="2024-12-31",
        filing_accession="0000320193-25-000001",
        availability_time="2025-01-15T00:00:00Z",
        retrieval_time="2025-01-20T00:00:00Z",
        source_location="https://sec.gov",
        accounting_basis=AccountingBasis.US_GAAP,
        extractor_version="v1",
        kind=FactKind.REPORTED,
    )

    future_fact = Fact(
        id="fact_future_1",
        company="AAPL",
        concept="Revenues",
        unit="USD",
        currency="USD",
        value=110000.0,
        period_start="2025-01-01",
        period_end="2025-12-31",
        filing_accession="0000320193-26-000001",
        availability_time="2026-01-15T00:00:00Z",
        retrieval_time="2026-01-20T00:00:00Z",
        source_location="https://sec.gov",
        accounting_basis=AccountingBasis.US_GAAP,
        extractor_version="v1",
        kind=FactKind.REPORTED,
    )

    forecast_fact = Fact(
        id="fact_forecast_1",
        company="AAPL",
        concept="Revenues",
        unit="USD",
        currency="USD",
        value=105000.0,
        period_start="2024-01-01",
        period_end="2024-12-31",
        filing_accession="0000320193-24-000001",
        availability_time="2024-05-01T00:00:00Z",
        retrieval_time="2024-05-05T00:00:00Z",
        source_location="https://sec.gov",
        accounting_basis=AccountingBasis.NON_GAAP,
        extractor_version="v1",
        kind=FactKind.FORECAST,
    )

    all_facts = [reported_fact, future_fact, forecast_fact]

    # As of 2025-01-31:
    # 1. future_fact is excluded (available in 2026)
    # 2. forecast_fact is excluded by default from reported lookup
    res = resolve_as_of(all_facts, "Revenues", "2025-01-31T00:00:00Z")
    assert res is not None
    assert res.id == "fact_hist_1"

    filtered = filter_facts_as_of(all_facts, "2025-01-31T00:00:00Z", allow_forecasts=False)
    assert len(filtered) == 1
    assert filtered[0].id == "fact_hist_1"


# ----------------------------------------------------------------------
# 3. EvidencePack Fact Ledger Exposure
# ----------------------------------------------------------------------

def test_evidence_pack_exposes_fact_ledger_with_as_of_filtering(tmp_path):
    from src.utils.checksum import sha256_digest

    pack_dir = tmp_path / "evidence_pack"
    pack_dir.mkdir()

    raw_bytes = b"<p>Official financial report</p>"
    raw_file = pack_dir / "D001.bytes"
    text_file = pack_dir / "D001.txt"
    raw_file.write_bytes(raw_bytes)
    text_content = "Official financial report"
    text_file.write_text(text_content, encoding="utf-8")

    evidence_data = {
        "schema_version": "1",
        "ticker": "TEST",
        "documents": [
            {
                "id": "D001",
                "type": "SECFiling",
                "title": "10-K",
                "url": "https://www.sec.gov/Archives/edgar/data/1/000000000123000001/doc.htm",
                "doc_date": "2025-01-15",
                "date_basis": "SEC_filing_date",
                "accessed": "2025-01-20T00:00:00Z",
                "content_type": "text/html",
                "content_sha256": sha256_digest(raw_bytes),
                "text_sha256": sha256_digest(text_content.encode("utf-8")),
                "raw_file": "D001.bytes",
                "text_file": "D001.txt",
                "verification": "retrieved",
            }
        ],
        "facts": [
            {
                "schema_version": "financial_fact_v1",
                "id": "fact_test_rev_2024",
                "company": "TEST",
                "concept": "Revenues",
                "unit": "USD",
                "currency": "USD",
                "value": 50000000.0,
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "filing_accession": "0000000001-25-000001",
                "availability_time": "2025-01-15T00:00:00Z",
                "retrieval_time": "2025-01-20T00:00:00Z",
                "source_location": "https://sec.gov",
                "accounting_basis": "US-GAAP",
                "extractor_version": "v1",
                "kind": "reported",
            }
        ],
    }

    (pack_dir / "evidence.json").write_text(json.dumps(evidence_data, indent=2), encoding="utf-8")

    pack = EvidencePack(pack_dir / "evidence.json", ticker="TEST", analysis_date="2025-01-31")
    assert len(pack.facts) == 1
    assert pack.facts[0].id == "fact_test_rev_2024"

    prompt_text = pack.prompt_text()
    assert "Date-Bounded Typed Fact Ledger" in prompt_text
    assert "fact_test_rev_2024" in prompt_text


# ----------------------------------------------------------------------
# 4. _normalize_beneish Invalid Scalar Handling & Witness Linking
# ----------------------------------------------------------------------

def test_parse_numeric_or_none_handles_scalars_strictly():
    assert _parse_numeric_or_none(123.45) == 123.45
    assert _parse_numeric_or_none("123.45") == 123.45
    assert _parse_numeric_or_none("$1.5M") == 1500000.0
    assert _parse_numeric_or_none("25%") == 25.0

    # Non-numbers must return None, NOT 0.0
    assert _parse_numeric_or_none(None) is None
    assert _parse_numeric_or_none(True) is None
    assert _parse_numeric_or_none(False) is None
    assert _parse_numeric_or_none("invalid") is None
    assert _parse_numeric_or_none("N/A") is None
    assert _parse_numeric_or_none("unknown") is None
    assert _parse_numeric_or_none(float("nan")) is None
    assert _parse_numeric_or_none(float("inf")) is None


def test_normalize_beneish_invalid_scalar_does_not_create_zero():
    injected: list[str] = []

    # 1. Invalid string scalar "invalid" -> MUST be unavailable, not 0.0
    res_invalid_str = _normalize_beneish({"score": "invalid"}, injected)
    assert res_invalid_str["score"] is None
    assert res_invalid_str["feasible"] is False
    assert res_invalid_str["classification"] == "Unavailable"
    assert "BeneishM" in injected

    # 2. String "N/A" -> MUST be unavailable, not 0.0
    injected.clear()
    res_na = _normalize_beneish("N/A", injected)
    assert res_na["score"] is None
    assert res_na["feasible"] is False
    assert res_na["classification"] == "Unavailable"

    # 3. Boolean True -> MUST be unavailable
    injected.clear()
    res_bool = _normalize_beneish({"score": True}, injected)
    assert res_bool["score"] is None
    assert res_bool["feasible"] is False

    # 4. Valid numeric score string "-2.45" -> preserved as model_reported_unverified
    injected.clear()
    res_valid_num = _normalize_beneish({"score": "-2.45"}, injected)
    assert res_valid_num["score"] == -2.45
    assert res_valid_num["provenance"] == PROVENANCE_MODEL_REPORTED


def test_normalize_beneish_deterministic_linking_with_fact_ids():
    injected: list[str] = []
    indices = {
        "dsri": 1.05,
        "gmi": 1.02,
        "aqi": 0.98,
        "sgi": 1.10,
        "depi": 1.00,
        "sgai": 0.95,
        "lvgi": 1.01,
        "tata": -0.02,
        "fact_ids": {
            "dsri": "fact_aapl_ar_2024",
            "gmi": "fact_aapl_gp_2024",
            "aqi": "fact_aapl_asset_2024",
            "sgi": "fact_aapl_rev_2024",
            "depi": "fact_aapl_dep_2024",
            "sgai": "fact_aapl_sga_2024",
            "lvgi": "fact_aapl_liab_2024",
            "tata": "fact_aapl_tata_2024",
        },
    }

    res = _normalize_beneish({"indices": indices}, injected)
    assert res["provenance"] == "deterministic"
    assert res["score"] is not None
    assert math.isfinite(res["score"])

    # Witness binds input fact IDs
    witness = res["calculation_witness"]
    assert witness["provenance"] == COMPUTED_BY_DETERMINISTIC
    assert witness["input_fact_ids"]["dsri"] == "fact_aapl_ar_2024"
    assert witness["input_fact_ids"]["tata"] == "fact_aapl_tata_2024"


# ----------------------------------------------------------------------
# 5. Public-Source Discovery of Amendments & Material Events
# ----------------------------------------------------------------------

def test_public_discovery_selects_amendments_and_material_events_capped():
    rows = [
        {"form": "10-K", "filingDate": "2024-11-01", "accessionNumber": "0000000001-24-000001", "primaryDocument": "10k.htm"},
        {"form": "10-K/A", "filingDate": "2024-11-15", "accessionNumber": "0000000001-24-000002", "primaryDocument": "10ka.htm"},
        {"form": "10-Q", "filingDate": "2025-01-10", "accessionNumber": "0000000001-25-000001", "primaryDocument": "10q.htm"},
        {"form": "DEF 14A", "filingDate": "2025-01-12", "accessionNumber": "0000000001-25-000002", "primaryDocument": "def14a.htm"},
        {"form": "8-K", "filingDate": "2025-01-14", "accessionNumber": "0000000001-25-000003", "primaryDocument": "8k1.htm"},
        {"form": "8-K", "filingDate": "2025-01-18", "accessionNumber": "0000000001-25-000004", "primaryDocument": "8k2.htm"},
        {"form": "8-K", "filingDate": "2025-01-20", "accessionNumber": "0000000001-25-000005", "primaryDocument": "8k3.htm"},
    ]

    annuals, quarters, proxies, material_events = _select_filings(rows, years=3)

    # Annuals include 10-K and 10-K/A
    assert len(annuals) == 2
    assert any(r["form"] == "10-K/A" for r in annuals)

    # Material events (8-K) capped at 2
    assert len(material_events) == 2
    assert material_events[0]["accessionNumber"] == "0000000001-25-000005"
    assert material_events[1]["accessionNumber"] == "0000000001-25-000004"


# ----------------------------------------------------------------------
# 6. A1 and A3 Prompt Epistemic Discipline & Mock Synthetic Calculations
# ----------------------------------------------------------------------

def test_a1_and_a3_prompts_contain_as_of_and_forecast_discipline(tmp_path):
    a1_agent = ModelAgent("A1", "Harvester-&-Validator")
    a3_agent = ModelAgent("A3", "Quant-Valuation-Technicals")

    context = AgentContext(
        run_id="test_run",
        params={"ticker": "AAPL", "company": "Apple Inc.", "analysis_date": "2025-01-31"},
        depth_config={"financial_lookback_years": 3},
        focus_overrides={},
        frozen_checksum="sha256:testchecksum",
        workdir=tmp_path,
        provenance_path=tmp_path / "p.log",
    )

    a1_prompt = a1_agent._build_prompt(context)
    assert "2025-01-31" in a1_prompt
    assert "date-bounded on or before 2025-01-31" in a1_prompt
    assert "Never treat forward-looking guidance or forecasts as reported facts" in a1_prompt
    assert "[Fact]" in a1_prompt and "[Estimate]" in a1_prompt

    a3_prompt = a3_agent._build_prompt(context)
    assert "2025-01-31" in a3_prompt
    assert "strictly on factual evidence available as of 2025-01-31" in a3_prompt
    assert "NEVER fabricate neutral or default figures" in a3_prompt


def test_mock_agent_calculations_remain_explicitly_synthetic(tmp_path):
    mock_a3 = MockAgent("A3", "Quant-Valuation-Technicals")
    context = AgentContext(
        run_id="test_mock",
        params={"ticker": "AAPL", "analysis_date": "2025-01-31"},
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:mockchecksum",
        workdir=tmp_path,
        provenance_path=tmp_path / "p.log",
    )

    out = mock_a3.run(context)
    env = out.envelope
    beneish = env.artifacts["BeneishM"]
    altman = env.artifacts["AltmanZ"]

    assert beneish["provenance"] == "deterministic"
    assert "computed from explicit synthetic indices" in env.sections_md["SECTION_4_FINANCIAL_FORENSICS"]
    assert beneish["calculation_witness"]["formula_id"] == "beneish_m_score"
    assert altman["provenance"] == "synthetic"

