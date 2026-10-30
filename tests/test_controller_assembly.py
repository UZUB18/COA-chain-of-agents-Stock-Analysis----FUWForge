"""
Self-check and integration test for FUWForge controller mode assembly.
Validates:
1. Canonical numerical section ordering (0 through 18)
2. Depth mode section filtering (Quick skips 13, 17, 18)
3. End-to-end envelope validation and artifact checks
4. Provenance log parsing with pipe delimiters
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.registry import AgentRegistry
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.provenance import ProvenanceLog


def make_mock_envelope(
    agent_id: str,
    name: str,
    checksum: str,
    artifacts: dict,
    sections: dict,
    gates: dict = None,
):
    return {
        "schema_version": "2.1",
        "agent": {"id": agent_id, "name": name},
        "timestamp": "2025-01-31T12:00:00Z",
        "params": {"ticker": "TEST", "company": "Test Co"},
        "gates": gates or {"passed": True, "notes": []},
        "sections_md": sections,
        "artifacts": artifacts,
        "sources": [
            {
                "type": "10-K",
                "url": "https://sec.gov/test",
                "doc_date": "2024-12-31",
                "accessed": "2025-01-31",
            }
        ],
        "warnings": [],
        "metrics": {"data_checksum": checksum, "latency_ms": 100, "tokens": 500},
        "confidence": 0.95,
    }


def test_controller_assembly():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_test_"))
    try:
        checksum = "sha256:abc123mockchecksumforauditrails"

        envelopes = {
            "a1": make_mock_envelope(
                "A1",
                "Harvester",
                checksum,
                artifacts={
                    "price_timestamp_eet": "2025-01-31T16:00:00",
                    "shares_basic": "1000",
                    "shares_diluted": "1050",
                    "market_cap_check": {
                        "price": 100.0,
                        "shares": 1000,
                        "cap": 100000.0,
                        "delta_pct": 0.0,
                    },
                },
                sections={"RAW_DATA_PACK": "### Raw Data Intake\nFinancial tables."},
            ),
            "a2": make_mock_envelope(
                "A2",
                "Business-Market-Peers",
                checksum,
                artifacts={
                    "PEER_LIST": ["COMP1", "COMP2", "COMP3"],
                    "MoatTrend": "Expanding",
                    "TopCustomerPct": "Not disclosed",
                    "TopSupplierPct": "Not disclosed",
                },
                sections={
                    "SECTION_1_COMPANY_PROFILE": "# Section 1: Company Profile",
                    "SECTION_2_BUSINESS_MODEL": "# Section 2: Business Model",
                    "SECTION_3_MARKET_AND_PEERS": "# Section 3: Market and Peers",
                    "AI_STRATEGY": "### AI Infrastructure & Monetization",
                },
            ),
            "a3": make_mock_envelope(
                "A3",
                "Quant-Valuation-Technicals",
                checksum,
                artifacts={
                    "AltmanZ": {"score": 4.5, "classification": "Safe Zone", "inputs": {}},
                    "BeneishM": {
                        "feasible": True,
                        "score": -2.5,
                        "classification": "Unlikely Manipulator",
                    },
                    "OCF_vs_NI_Flag": "Aligned",
                    "PEG_TTM": 1.2,
                    "PEG_Fwd": 1.1,
                    "HypeFlag": "Reality",
                    "PeerTable": [],
                    "TechnicalSet": {"SMA_50": 100.0, "SMA_200": 95.0, "RSI_14": 55.0},
                },
                sections={
                    "SECTION_4_FINANCIAL_FORENSICS": "# Section 4: Financial Forensics",
                    "SECTION_5_VALUATION": "# Section 5: Valuation",
                    "SECTION_12_TECHNICALS": "# Section 12: Technical Diagnostics",
                },
            ),
            "a4": make_mock_envelope(
                "A4",
                "Leadership-Governance-ESG",
                checksum,
                artifacts={
                    "CEO_Pay_Ratio": 120.5,
                    "Auditor": "PwC",
                    "AuditorTenure": "10 years",
                    "GlassdoorScore": 4.2,
                    "AttritionNote": "Normal",
                    "ESG_Momentum": "Improving",
                    "LowSampleSizeCultureData": False,
                },
                sections={
                    "SECTION_6_LEADERSHIP_GOVERNANCE": "# Section 6: Leadership & Governance",
                    "SECTION_7_CULTURE_ESG": "# Section 7: Culture & ESG",
                },
            ),
            "a5": make_mock_envelope(
                "A5",
                "News-Sentiment-Flows",
                checksum,
                artifacts={
                    "SentimentSplit": {
                        "pos": 0.6,
                        "neu": 0.3,
                        "neg": 0.1,
                        "lookback_days": 90,
                    },
                    "SocialVsFundDivergence": "Aligned",
                    "MajorLitigation": [],
                    "AuditorChangeYN": "No",
                    "InsiderNet12m": {"direction": "NetBuy"},
                    "InstitutionalOwnershipTrend": {
                        "direction": "Up",
                        "delta_pct": 1.2,
                    },
                    "RegulatoryWatchlist": [],
                },
                sections={
                    "SECTION_8_NEWS_SENTIMENT": "# Section 8: News Sentiment",
                    "SECTION_9_FLOW_LITIGATION": "# Section 9: Flows & Litigation",
                },
            ),
            "a6b": make_mock_envelope(
                "A6B",
                "Bull-Case",
                checksum,
                artifacts={
                    "BullCase": [
                        {
                            "id": "B1",
                            "thesis": "High margin expansion",
                            "evidence": ["A3.AltmanZ"],
                            "impact": "EPS +5%",
                        }
                    ]
                },
                sections={"SECTION_10_BULL_CASE": "# Section 10: Bull Case"},
            ),
            "a6r": make_mock_envelope(
                "A6R",
                "Bear-Case",
                checksum,
                artifacts={
                    "BearRebuttals": [
                        {
                            "bull_id": "B1",
                            "probability": "Med",
                            "severity": "High",
                            "counter": "Valuation compression risk",
                        }
                    ]
                },
                sections={"SECTION_11_BEAR_CASE": "# Section 11: Bear Rebuttals"},
            ),
            "a6": make_mock_envelope(
                "A6",
                "CIO-Synthesis",
                checksum,
                artifacts={
                    "DangerZoneDashboard": [
                        {
                            "category": "Valuation",
                            "flag": "\U0001F7E1",
                            "sources": ["A3.PEG_TTM"],
                            "note": "Premium multiple",
                        }
                    ],
                    "ScenarioTable": {
                        "Bull": {
                            "Revenue": "+16%",
                            "EPS": "$8.20",
                            "FCFPerShare": "$9.10",
                            "Drivers": "Expansion",
                        },
                        "Base": {
                            "Revenue": "+11%",
                            "EPS": "$7.30",
                            "FCFPerShare": "$8.00",
                            "Drivers": "Guidance",
                        },
                        "Bear": {
                            "Revenue": "+2%",
                            "EPS": "$5.90",
                            "FCFPerShare": "$6.40",
                            "Drivers": "Macro",
                        },
                    },
                    "Catalysts": ["Product cycle"],
                    "Headwinds": ["FX risk"],
                },
                sections={
                    "SECTION_14_SYNTHESIS": "# Section 14: Synthesis & Scenario Lab"
                },
            ),
            "a7": make_mock_envelope(
                "A7",
                "Final-Assembly",
                checksum,
                artifacts={
                    "Scorecard": {
                        "Financials": {"weight": 0.25, "score": 8.5, "weighted": 2.125},
                        "Moat": {"weight": 0.2, "score": 8.0, "weighted": 1.6},
                        "Growth": {"weight": 0.15, "score": 7.5, "weighted": 1.125},
                        "Valuation": {"weight": 0.15, "score": 6.8, "weighted": 1.02},
                        "MgmtGov": {"weight": 0.1, "score": 8.4, "weighted": 0.84},
                        "RiskInverse": {"weight": 0.1, "score": 8.0, "weighted": 0.8},
                        "ESG": {"weight": 0.025, "score": 7.2, "weighted": 0.18},
                        "Market": {"weight": 0.025, "score": 7.6, "weighted": 0.19},
                        "Overall": {"score": 7.88, "rating_band": "Buy"},
                    },
                    "SWOT": {
                        "Strengths": ["Recurring revenue"],
                        "Weaknesses": ["Premium multiple"],
                        "Opportunities": ["Cloud expansion"],
                        "Threats": ["Macro"],
                        "Watchlist": ["Margins"],
                    },
                    "Glossary": [
                        {"term": "FCF", "definition": "Free cash flow"}
                    ],
                    "comparison_fields": {
                        "quality_score_10": 7.9,
                        "valuation_score_10": 6.8,
                        "expected_5y_irr_base_pct": 11.8,
                        "downside_case_5y_irr_pct": -2.1,
                        "fcf_yield_ttm_pct": 3.6,
                        "roic_ttm_pct": 28.4,
                        "net_debt_to_ebitda": -0.45,
                        "moat_score_10": 8.0,
                        "reinvestment_runway_score_10": 7.5,
                        "balance_sheet_score_10": 8.5,
                        "fragility_score_10": 2.0,
                        "evidence_quality_score_10": 8.0,
                        "data_completeness_score_10": 9.0,
                    },
                },
                sections={
                    "SECTION_0_EXEC_SUMMARY": "# Section 0: Executive Summary",
                    "SECTION_13_IPO_HISTORY": "# Section 13: Capital History",
                    "SECTION_15_SWOT": "# Section 15: SWOT Matrix",
                    "SECTION_16_SCORECARD": "# Section 16: Institutional Scorecard",
                    "SECTION_17_GLOSSARY": "# Section 17: Glossary",
                    "SECTION_18_LAY_SUMMARY": "# Section 18: Lay Summary",
                },
            ),
        }

        for filename, data in envelopes.items():
            path = temp_dir / f"{filename}_envelope.json"
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # 1. Test Standard Depth Assembly
        registry_model = "orchestrator-registry-model"
        orchestrator = FUWForgeOrchestrator(
            PROJECT_ROOT,
            registry=AgentRegistry(provider="mock", model=registry_model),
        )
        params_standard = OrchestratorParams(
            company="Test Company Inc.",
            ticker="TEST",
            analysis_date="2025-01-31",
            depth="Standard",
            focus=["ai_strategy"],
            weights="default",
        )

        summary = orchestrator.run(
            run_id="test_run_01",
            params=params_standard,
            mode="controller",
            output_root=temp_dir,
        )

        report_file = Path(summary["outputs"]["report"])
        assert report_file.exists(), "Report file was not generated"
        content = report_file.read_text(encoding="utf-8")

        # Verify numerical ordering: Section 0 must precede 1, 1 precedes 2, ..., 18
        section_markers = [
            "# Section 0:",
            "# Section 1:",
            "# Section 2:",
            "# Section 3:",
            "### AI Infrastructure",
            "# Section 4:",
            "# Section 5:",
            "# Section 6:",
            "# Section 7:",
            "# Section 8:",
            "# Section 9:",
            "# Section 10:",
            "# Section 11:",
            "# Section 12:",
            "# Section 13:",
            "# Section 14:",
            "# Section 15:",
            "# Section 16:",
            "# Section 17:",
            "# Section 18:",
            "### Raw Data Intake",
        ]

        last_pos = -1
        for marker in section_markers:
            pos = content.find(marker)
            assert pos != -1, f"Marker '{marker}' missing from assembled report"
            assert pos > last_pos, (
                f"Ordering violation: '{marker}' (pos {pos}) appeared before prior marker (pos {last_pos})"
            )
            last_pos = pos

        assert "\nprovider: controller\n" in content
        assert "\nmodel: not-applicable\n" in content
        assert registry_model not in content
        controller_banner = "**CONTROLLER-ASSEMBLED — envelopes supplied by operator"
        banner_at = content.find(controller_banner)
        first_section = content.find("# Section 0:")
        assert banner_at != -1, "controller assembly must identify operator-supplied envelopes"
        assert banner_at < first_section, "the controller banner must precede the analysis"
        assert "**MOCK PROVIDER" not in content

        summary_data = json.loads(
            (temp_dir / "run_summary.json").read_text(encoding="utf-8")
        )
        manifest_data = json.loads(
            (temp_dir / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert summary_data["provider"] == "controller"
        assert summary_data["model"] == "not-applicable"
        assert registry_model not in json.dumps(summary_data)
        assert manifest_data["configuration"]["provider"] == "controller"
        assert manifest_data["configuration"]["model"] == "not-applicable"
        assert registry_model not in json.dumps(manifest_data)

        # 2. Test Quick Depth (skips 13, 17, 18)
        params_quick = OrchestratorParams(
            company="Test Company Inc.",
            ticker="TEST",
            analysis_date="2025-01-31",
            depth="Quick",
            focus=[],
            weights="default",
        )
        summary_quick = orchestrator.run(
            run_id="test_run_quick",
            params=params_quick,
            mode="controller",
            output_root=temp_dir,
        )
        quick_content = Path(summary_quick["outputs"]["report"]).read_text(
            encoding="utf-8"
        )
        assert "# Section 13:" not in quick_content, (
            "Section 13 should be skipped in Quick mode"
        )
        assert "# Section 17:" not in quick_content, (
            "Section 17 should be skipped in Quick mode"
        )
        assert "# Section 18:" not in quick_content, (
            "Section 18 should be skipped in Quick mode"
        )
        assert "# Section 0:" in quick_content, (
            "Section 0 should be present in Quick mode"
        )

        # 3. Test Provenance log with pipe characters
        prov_path = temp_dir / "test_prov.log"
        prov = ProvenanceLog(path=prov_path)
        prov.append("A1", "Financial metric | Table column | Value: $100M")
        prov.append("A2", "Second entry | note")
        prov.flush()

        loaded_prov = ProvenanceLog(path=prov_path)
        loaded_prov.load_existing()
        assert len(loaded_prov.entries) == 2, (
            f"Expected 2 entries, got {len(loaded_prov.entries)}"
        )
        assert (
            loaded_prov.entries[0].message
            == "Financial metric | Table column | Value: $100M"
        )

        print("ALL FUWFORGE TESTS PASSED SUCCESSFULLY!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_automated_mock_run_keeps_mock_banner(tmp_path):
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Test Company Inc.",
        ticker="MOCK",
        analysis_date="2025-01-31",
        depth="Quick",
        focus=[],
        weights="default",
    )

    summary = orchestrator.run(
        run_id="test_mock_run",
        params=params,
        mode="automated",
        output_root=tmp_path,
    )
    content = Path(summary["outputs"]["report"]).read_text(encoding="utf-8")

    assert "\nprovider: mock\n" in content
    assert "\nmodel: not-applicable\n" in content
    banner_at = content.find("**MOCK PROVIDER")
    first_section = content.find("# Section 0:")
    assert banner_at != -1, "mock run must carry its synthetic-output banner"
    assert banner_at < first_section, "the mock banner must precede the analysis"
    assert "**CONTROLLER-ASSEMBLED" not in content


if __name__ == "__main__":
    test_controller_assembly()
