# Comprehensive test suite verifying stock-analyst upgrades in FUWForge:
# 1. Deterministic financial calculation engine (Altman Z, Beneish M, ROIC, Accruals, CAGR, Return-Driver Stack)
# 2. Context DAG propagation across agents (A1 -> A2-A5 -> A6B -> A6R -> A6 -> A7)
# 3. Epistemic tagging rules injection
# 4. Standardized comparison_fields generation

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from src.agents.base import AgentContext
from src.agents.model import ModelAgent
from src.envelope import Envelope
from src.utils.finance import (
    accruals_ratio,
    altman_z_score,
    beneish_m_score,
    cagr,
    fcf_conversion,
    fcf_yield,
    generate_comparison_fields,
    net_debt_to_ebitda,
    return_driver_stack,
    roic,
)


class RecordingBackend:
    """Mock backend that captures prompts and returns a minimal valid envelope."""

    def __init__(self):
        self.captured_prompts = []

    SECTION_KEYS = [
        "RAW_DATA_PACK",
        "SECTION_0_EXEC_SUMMARY",
        "SECTION_1_COMPANY_PROFILE",
        "SECTION_2_BUSINESS_MODEL",
        "SECTION_3_MARKET_AND_PEERS",
        "SECTION_4_FINANCIAL_FORENSICS",
        "SECTION_5_VALUATION",
        "SECTION_6_LEADERSHIP_GOVERNANCE",
        "SECTION_7_CULTURE_ESG",
        "SECTION_8_NEWS_SENTIMENT",
        "SECTION_9_FLOW_LITIGATION",
        "SECTION_10_BULL_CASE",
        "SECTION_11_BEAR_CASE",
        "SECTION_12_TECHNICALS",
        "SECTION_13_IPO_HISTORY",
        "SECTION_14_SYNTHESIS",
        "SECTION_15_SWOT",
        "SECTION_16_SCORECARD",
        "SECTION_17_GLOSSARY",
        "SECTION_18_LAY_SUMMARY",
    ]

    def generate(self, prompt: str) -> str:
        self.captured_prompts.append(prompt)
        return json.dumps(
            {
                "schema_version": "2.1",
                "agent": {"id": "TEST", "name": "TestAgent"},
                "sections_md": {key: f"# {key}\nContent" for key in self.SECTION_KEYS},
                "artifacts": {},
                "confidence": 0.95,
                "metrics": {},
            }
        )


def test_financial_calculations():
    # 1. CAGR
    res_cagr = cagr(100.0, 200.0, 5)
    assert res_cagr == 14.87  # (2^(1/5) - 1) * 100 = 14.8698%
    assert cagr(0.0, 100.0, 5) is None
    assert cagr(100.0, -10.0, 5) is None

    # 2. FCF Conversion
    conv = fcf_conversion(85.0, 100.0)
    assert conv == 85.0
    assert fcf_conversion(85.0, 0.0) is None

    # 3. Accruals Ratio
    accruals = accruals_ratio(net_income=120.0, ocf=100.0, avg_total_assets=1000.0)
    assert accruals == 0.02  # (120 - 100) / 1000 = 0.02

    # 4. ROIC
    res_roic = roic(nopat=250.0, invested_capital=1000.0)
    assert res_roic == 25.0
    assert roic(250.0, 0.0) is None

    # 5. Altman Z-Score (Manufacturing / Standard)
    z_safe = altman_z_score(
        working_capital=240.0,
        total_assets=1000.0,
        retained_earnings=360.0,
        ebit=290.0,
        market_cap=1900.0,
        total_liabilities=1000.0,
        revenue=1120.0,
    )
    assert z_safe["score"] > 3.0
    assert z_safe["classification"] == "Safe Zone"

    z_distress = altman_z_score(
        working_capital=-100.0,
        total_assets=1000.0,
        retained_earnings=-200.0,
        ebit=-50.0,
        market_cap=200.0,
        total_liabilities=1200.0,
        revenue=500.0,
    )
    assert z_distress["score"] < 1.81
    assert z_distress["classification"] == "Distress Zone"

    # 6. Beneish M-Score
    m_clean = beneish_m_score(
        dsri=1.02,
        gmi=1.01,
        aqi=0.98,
        sgi=1.10,
        depi=1.00,
        sgai=1.01,
        lvgi=0.95,
        tata=-0.02,
    )
    assert m_clean["score"] < -1.78
    assert m_clean["classification"] == "Unlikely Manipulator"

    m_manip = beneish_m_score(
        dsri=1.8,
        gmi=1.6,
        aqi=1.5,
        sgi=1.8,
        depi=1.3,
        sgai=1.4,
        lvgi=1.6,
        tata=0.15,
    )
    assert m_manip["score"] > -1.78
    assert m_manip["classification"] == "High Manipulation Risk"

    # 7. 5-Year Return-Driver Stack
    stack = return_driver_stack(
        starting_yield=3.5,
        fundamental_growth=9.0,
        multiple_change=-1.5,
        capital_allocation=1.0,
    )
    assert stack["expected_5y_irr_base_pct"] == 12.0
    assert stack["starting_yield_pct"] == 3.5

    # 8. Comparison Fields (13-field vector)
    fields = generate_comparison_fields(
        quality_score_10=8.5,
        valuation_score_10=7.0,
        expected_5y_irr_base_pct=11.5,
        downside_case_5y_irr_pct=-2.0,
        fcf_yield_ttm_pct=4.1,
        roic_ttm_pct=24.5,
        net_debt_to_ebitda_val=0.4,
        moat_score_10=8.5,
        reinvestment_runway_score_10=7.5,
        balance_sheet_score_10=9.0,
        fragility_score_10=2.0,
    )
    assert len(fields) == 13
    assert fields["quality_score_10"] == 8.5
    assert fields["roic_ttm_pct"] == 24.5
    # Unmeasured scores stay null; they are never defaulted.
    assert fields["evidence_quality_score_10"] is None
    assert fields["data_completeness_score_10"] is None


def test_epistemic_tagging_and_focus_routing_in_prompts():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_test_epistemic_"))
    try:
        backend = RecordingBackend()
        agent_a3 = ModelAgent(
            agent_id="A3", name="Quant-Valuation-Technicals", backend=backend
        )

        context = AgentContext(
            run_id="test_run",
            params={"ticker": "AAPL", "company": "Apple Inc."},
            depth_config={},
            envelopes={},
            focus_overrides={
                "A3": "Scrutinize Services gross margin trajectory and R&D capitalization."
            },
            frozen_checksum="sha256:dummy",
            workdir=temp_dir,
            provenance_path=temp_dir / "prov.log",
        )

        agent_a3.run(context)
        prompt = backend.captured_prompts[0]

        # Verify epistemic tagging instructions are present
        assert "Epistemic Discipline:" in prompt
        assert "[Fact]" in prompt
        assert "[Estimate]" in prompt
        assert "[Derived]" in prompt
        assert "[Interpretation]" in prompt
        assert "[Judgment]" in prompt

        # Verify focus routing directive was dynamically injected
        assert "Specific Focus Directives:" in prompt
        assert (
            "Scrutinize Services gross margin trajectory and R&D capitalization"
            in prompt
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_context_dag_propagation():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_test_dag_"))
    try:
        # 1. Create upstream envelopes for A1, A6B, A6R
        a1_env = Envelope(
            schema_version="2.1",
            agent={"id": "A1", "name": "SEC-Intake"},
            timestamp="2025-01-31T00:00:00Z",
            params={"ticker": "MSFT"},
            gates={"passed": True, "notes": []},
            sections_md={
                "SECTION_1_SEC_INTAKE": "# Section 1: Frozen Filing Data\nRevenue: $245B"
            },
            artifacts={
                "price_timestamp_eet": "17:00",
                "shares_basic": 7400,
                "shares_diluted": 7450,
            },
            sources=[],
            warnings=[],
            metrics={"data_checksum": "sha256:abc1234"},
            confidence=0.99,
        )

        a6b_env = Envelope(
            schema_version="2.1",
            agent={"id": "A6B", "name": "Bull-Case"},
            timestamp="2025-01-31T00:00:00Z",
            params={"ticker": "MSFT"},
            gates={"passed": True, "notes": []},
            sections_md={
                "SECTION_10_BULL_CASE": "# Section 10: Azure AI expansion accelerates FCF."
            },
            artifacts={
                "BullCase": [
                    {"id": "B1", "thesis": "Azure AI expansion accelerates FCF"}
                ]
            },
            sources=[],
            warnings=[],
            metrics={"data_checksum": "sha256:abc1234"},
            confidence=0.95,
        )

        a6r_env = Envelope(
            schema_version="2.1",
            agent={"id": "A6R", "name": "Bear-Case"},
            timestamp="2025-01-31T00:00:00Z",
            params={"ticker": "MSFT"},
            gates={"passed": True, "notes": []},
            sections_md={
                "SECTION_11_BEAR_CASE": "# Section 11: Capex intensity depresses near-term ROIC."
            },
            artifacts={
                "BearRebuttals": [
                    {
                        "bull_id": "B1",
                        "rebuttal": "Capex intensity depresses near-term ROIC",
                    }
                ]
            },
            sources=[],
            warnings=[],
            metrics={"data_checksum": "sha256:abc1234"},
            confidence=0.95,
        )

        # 2. Test A2 receiving A1 intake
        backend_a2 = RecordingBackend()
        agent_a2 = ModelAgent(
            agent_id="A2", name="Business-Quality-Moat", backend=backend_a2
        )
        ctx_a2 = AgentContext(
            run_id="test_run",
            params={"ticker": "MSFT"},
            depth_config={},
            envelopes={"A1": a1_env},
            focus_overrides={},
            frozen_checksum="sha256:abc1234",
            workdir=temp_dir,
            provenance_path=temp_dir / "prov.log",
        )
        agent_a2.run(ctx_a2)
        prompt_a2 = backend_a2.captured_prompts[0]
        assert "Primary Filing Intake (A1)" in prompt_a2
        assert "Revenue: $245B" in prompt_a2

        # 3. Test A6R receiving A6B bull case
        backend_a6r = RecordingBackend()
        agent_a6r = ModelAgent(agent_id="A6R", name="Bear-Case", backend=backend_a6r)
        ctx_a6r = AgentContext(
            run_id="test_run",
            params={"ticker": "MSFT"},
            depth_config={},
            envelopes={"A1": a1_env, "A6B": a6b_env},
            focus_overrides={},
            frozen_checksum="sha256:abc1234",
            workdir=temp_dir,
            provenance_path=temp_dir / "prov.log",
        )
        agent_a6r.run(ctx_a6r)
        prompt_a6r = backend_a6r.captured_prompts[0]
        assert "Bull Theses to Rebut:" in prompt_a6r
        assert "Azure AI expansion accelerates FCF" in prompt_a6r

        # 4. Test A6 (CIO Synthesis) receiving BOTH A6B and A6R
        backend_a6 = RecordingBackend()
        agent_a6 = ModelAgent(agent_id="A6", name="CIO-Synthesis", backend=backend_a6)
        ctx_a6 = AgentContext(
            run_id="test_run",
            params={"ticker": "MSFT"},
            depth_config={},
            envelopes={"A1": a1_env, "A6B": a6b_env, "A6R": a6r_env},
            focus_overrides={},
            frozen_checksum="sha256:abc1234",
            workdir=temp_dir,
            provenance_path=temp_dir / "prov.log",
        )
        agent_a6.run(ctx_a6)
        prompt_a6 = backend_a6.captured_prompts[0]
        assert "[BULL THESES from A6B]" in prompt_a6
        assert "[BEAR COUNTER-REBUTTALS from A6R]" in prompt_a6
        assert "Azure AI expansion accelerates FCF" in prompt_a6
        assert "Capex intensity depresses near-term ROIC" in prompt_a6

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_a7_comparison_fields_normalization():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_test_a7_fields_"))
    try:
        backend = RecordingBackend()
        agent_a7 = ModelAgent(agent_id="A7", name="Final-Assembly", backend=backend)

        raw_a7_output = {
            "schema_version": "2.1",
            "agent": {"id": "A7", "name": "Final-Assembly"},
            "sections_md": {
                "SECTION_0_EXEC_SUMMARY": "# Executive Summary",
                "SECTION_13_IPO_HISTORY": "# Capital History",
                "SECTION_15_SWOT": "# SWOT",
                "SECTION_16_SCORECARD": "# Scorecard",
                "SECTION_17_GLOSSARY": "# Glossary",
                "SECTION_18_LAY_SUMMARY": "# Lay Summary",
            },
            "artifacts": {
                "Scorecard": {
                    "Financials": 9.0,
                    "Moat": 8.5,
                    "Growth": 8.0,
                    "Valuation": 7.0,
                    "MgmtGov": 8.0,
                    "RiskInverse": 8.0,
                    "ESG": 7.5,
                    "Market": 7.5,
                }
            },
            "confidence": 0.98,
        }

        class SpecificBackend:
            def generate(self, prompt: str) -> str:
                return json.dumps(raw_a7_output)

        agent_a7.backend = SpecificBackend()
        ctx = AgentContext(
            run_id="test_run",
            params={"ticker": "NVDA", "company": "NVIDIA Corp"},
            depth_config={},
            envelopes={},
            focus_overrides={},
            frozen_checksum="sha256:dummy",
            workdir=temp_dir,
            provenance_path=temp_dir / "prov.log",
        )

        output = agent_a7.run(ctx)
        env = output.envelope

        # Verify comparison_fields was injected into artifacts
        assert "comparison_fields" in env.artifacts
        fields = env.artifacts["comparison_fields"]
        assert len(fields) == 13
        assert "quality_score_10" in fields
        assert "valuation_score_10" in fields
        assert "roic_ttm_pct" in fields
        assert "fcf_yield_ttm_pct" in fields
        assert "expected_5y_irr_base_pct" in fields
        assert "evidence_quality_score_10" in fields
        assert fields["quality_score_10"] > 0
        # Values the model never measured are null and documented, not invented.
        gaps = env.metrics.get("missing_data")
        gap_paths = {g["field"] for g in gaps} if isinstance(gaps, list) else set(gaps or {})
        for key in ("roic_ttm_pct", "fcf_yield_ttm_pct", "net_debt_to_ebitda",
                    "expected_5y_irr_base_pct", "downside_case_5y_irr_pct",
                    "evidence_quality_score_10", "data_completeness_score_10"):
            assert fields[key] is None, key
            assert f"comparison_fields.{key}" in gap_paths, key
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
