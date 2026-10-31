# Mock agent implementation for artificial / synthetic testing.

from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.base import AgentContext, AgentOutput, BaseAgent
from src.envelope import Envelope
from src.utils.checksum import checksum_json
from src.calculation_witness import beneish_witness
from src.utils.finance import (
    beneish_m_score,
    generate_comparison_fields,
    resolve_weights,
)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


class MockAgent(BaseAgent):
    """
    Simulates agent execution by generating contract-compliant envelopes
    with deterministic or randomly generated data.
    """

    id: str = "AX"
    name: str = "Mock Agent"

    def __init__(
        self,
        agent_id: Optional[str] = None,
        name: Optional[str] = None,
        prompt_path: Optional[Path] = None,
        randomize: bool = False,
    ):
        super().__init__(prompt_path=prompt_path)
        if agent_id:
            self.id = agent_id
        if name:
            self.name = name
        self.randomize = randomize

    def run(self, context: AgentContext) -> AgentOutput:
        generator = getattr(
            self, f"_generate_{self.id.lower()}", self._generate_fallback
        )
        envelope_data = generator(context)
        envelope = Envelope.from_dict(envelope_data)
        return AgentOutput(
            envelope=envelope,
            raw_response={"mock": True, "agent": self.id, "generated_at": _now_iso()},
        )

    def _generate_a1(self, context: AgentContext) -> Dict[str, Any]:
        params = context.params
        ticker = params.get("ticker", "TICK")
        company = params.get("company", f"{ticker} Corp")
        analysis_date = params.get("analysis_date", "2025-01-31")

        # Simulate gate failure if requested
        if params.get("fail_gate") or params.get("a1_gate_fail"):
            return {
                "schema_version": "2.1",
                "agent": {"id": "A1", "name": "Harvester-&-Validator"},
                "timestamp": _now_iso(),
                "params": params,
                "gates": {
                    "passed": False,
                    "notes": [
                        "Missing required 10-K annual filings for historical lookback."
                    ],
                },
                "sections_md": {
                    "MISSING_DATA_TABLE": (
                        "| Metric / Filing | Required | Found | Remediation |\n"
                        "|---|---|---|---|\n"
                        "| 10-K (FY2024) | Yes | None | Provide audited annual filing |\n"
                    )
                },
                "artifacts": {
                    "price_timestamp_eet": f"{analysis_date}T16:00:00Z",
                    "shares_basic": "0",
                    "shares_diluted": "0",
                    "market_cap_check": {
                        "price": 0.0,
                        "shares": 0,
                        "cap": 0.0,
                        "delta_pct": 0.0,
                    },
                },
                "sources": [
                    {
                        "type": "SEC",
                        "url": "https://www.sec.gov/edgar",
                        "doc_date": analysis_date,
                        "accessed": _now_iso(),
                    }
                ],
                "warnings": ["Run halted due to GOD gate failure."],
                "metrics": {
                    "data_checksum": "sha256:gate_failed",
                    "latency_ms": 45,
                    "tokens": 120,
                    "retries": 0,
                },
                "confidence": 0.0,
            }

        price = round(random.uniform(50.0, 350.0) if self.randomize else 150.0, 2)
        shares_basic_val = (
            random.randint(500_000_000, 2_000_000_000)
            if self.randomize
            else 1_200_000_000
        )
        shares_diluted_val = int(
            shares_basic_val
            * (1.0 + (random.uniform(0.01, 0.04) if self.randomize else 0.02))
        )
        market_cap = round(price * shares_basic_val, 2)

        # Same derivation as ModelAgent: the evidence pack is part of the input.
        if context.frozen_checksum:
            data_checksum = context.frozen_checksum
        elif context.evidence_checksum:
            data_checksum = checksum_json({"params": params, "evidence_checksum": context.evidence_checksum})
        else:
            data_checksum = checksum_json(params)

        raw_data_pack = (
            f"### Raw Data Intake: {company} ({ticker})\n\n"
            f"**Price Timestamp (EET)**: {analysis_date}T16:00:00Z\n"
            f"**Share Count (Basic)**: {shares_basic_val:,}\n"
            f"**Share Count (Diluted)**: {shares_diluted_val:,}\n"
            f"**Closing Share Price**: ${price:.2f}\n"
            f"**Implied Market Capitalization**: ${market_cap:,.2f}\n\n"
            f"| Metric | FY-3 | FY-2 | FY-1 | TTM |\n"
            f"|---|---|---|---|---|\n"
            f"| Revenue ($M) | 28,400 | 33,100 | 39,800 | 44,200 |\n"
            f"| Gross Margin | 62.5% | 63.8% | 65.1% | 66.2% |\n"
            f"| Operating Cash Flow ($M) | 6,800 | 8,200 | 10,400 | 12,100 |\n"
            f"| Free Cash Flow ($M) | 5,400 | 6,700 | 8,900 | 10,500 |\n"
        )

        return {
            "schema_version": "2.1",
            "agent": {"id": "A1", "name": "Harvester-&-Validator"},
            "timestamp": _now_iso(),
            "params": params,
            "gates": {
                "passed": True,
                "notes": [
                    "Primary filings reconciled and frozen context checksum generated."
                ],
            },
            "sections_md": {"RAW_DATA_PACK": raw_data_pack},
            "artifacts": {
                "price_timestamp_eet": f"{analysis_date}T16:00:00Z",
                "shares_basic": str(shares_basic_val),
                "shares_diluted": str(shares_diluted_val),
                "market_cap_check": {
                    "price": price,
                    "shares": shares_basic_val,
                    "cap": market_cap,
                    "delta_pct": 0.0,
                },
            },
            "sources": [
                {
                    "type": "10-K",
                    "url": "https://www.sec.gov/edgar/data/10k",
                    "doc_date": "2024-12-31",
                    "accessed": _now_iso(),
                },
                {
                    "type": "Exchange Feed",
                    "url": "https://nasdaq.com/feed",
                    "doc_date": analysis_date,
                    "accessed": _now_iso(),
                },
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": data_checksum,
                "latency_ms": 115,
                "tokens": 420,
                "retries": 0,
            },
            "confidence": 0.98,
        }

    def _generate_a2(self, context: AgentContext) -> Dict[str, Any]:
        ticker = context.params.get("ticker", "TICK")
        company = context.params.get("company", f"{ticker} Corp")
        focus = context.params.get("focus", [])
        focus_list = focus if isinstance(focus, list) else [focus]

        peers = [f"{ticker}_PEER1", f"{ticker}_PEER2", f"{ticker}_PEER3"]
        moat = (
            random.choice(["Expanding", "Stable", "Eroding"])
            if self.randomize
            else "Expanding"
        )

        sections_md = {
            "SECTION_1_COMPANY_PROFILE": (
                f"# Section 1: Company Profile — {company}\n\n"
                f"{company} ({ticker}) provides high-margin enterprise infrastructure and cloud solutions. "
                "The business exhibits strong network effects and sticky recurring software subscription models."
            ),
            "SECTION_2_BUSINESS_MODEL": (
                f"# Section 2: Business Model & Monetization\n\n"
                "Operating leverage is powered by a SaaS/usage hybrid model with gross margins exceeding 65%. "
                "Customer acquisition costs continue to decline as enterprise land-and-expand motions mature."
            ),
            "SECTION_3_MARKET_AND_PEERS": (
                f"# Section 3: Market Position and Peer Analysis\n\n"
                f"Key competitive benchmark peers include {', '.join(peers)}. {company} maintains market leadership "
                f"with an estimated 32% share in core target verticals."
            ),
        }

        if any("ai" in str(tag).lower() for tag in focus_list):
            sections_md["AI_STRATEGY"] = (
                "### AI Infrastructure & Monetization Strategy\n\n"
                "The company integrates proprietary domain-specific LLM workflows directly into its enterprise platform, "
                "unlocking incremental tier pricing and high-margin AI inference revenue."
            )

        return {
            "schema_version": "2.1",
            "agent": {"id": "A2", "name": "Business-Market-Peers"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": sections_md,
            "artifacts": {
                "PEER_LIST": peers,
                "MoatTrend": moat,
                "TopCustomerPct": "Not disclosed",
                "TopSupplierPct": "Not disclosed",
            },
            "sources": [
                {
                    "type": "10-K",
                    "url": "https://www.sec.gov/edgar/data/10k",
                    "doc_date": "2024-12-31",
                    "accessed": _now_iso(),
                },
                {
                    "type": "Investor Day Presentation",
                    "url": "https://ir.company.com/deck",
                    "doc_date": "2024-11-15",
                    "accessed": _now_iso(),
                },
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 130,
                "tokens": 480,
                "retries": 0,
            },
            "confidence": 0.95,
        }

    def _generate_a3(self, context: AgentContext) -> Dict[str, Any]:
        ticker = context.params.get("ticker", "TICK")
        delta = (random.uniform(-0.3, 0.4)) if self.randomize else 0.0
        altman_z = round(3.85 + delta, 2)
        peg_ttm = round(1.25 + delta, 2)
        peg_fwd = round(1.10 + delta, 2)

        # The Beneish score is computed by the deterministic calculator from
        # explicit synthetic indices, never hard-coded. Mock output is test
        # infrastructure, so the inputs are clearly synthetic; the point is
        # that the arithmetic path under test is the real one.
        beneish_indices = {
            "dsri": 1.00 + delta * 0.10,
            "gmi": 1.00 + delta * 0.05,
            "aqi": 1.00 - delta * 0.05,
            "sgi": 1.00 + delta * 0.08,
            "depi": 1.00,
            "sgai": 1.00,
            "lvgi": 1.00 - delta * 0.05,
            "tata": delta * 0.10,
        }
        beneish = beneish_m_score(**beneish_indices)
        beneish_m = beneish["score"]
        beneish_note = (
            f"{beneish['classification']} "
            f"(calculator {beneish['calculator_id']} v{beneish['calculator_version']}, "
            "computed from explicit synthetic indices; screening signal only)"
        )

        return {
            "schema_version": "2.1",
            "agent": {"id": "A3", "name": "Quant-Valuation-Technicals"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_4_FINANCIAL_FORENSICS": (
                    f"# Section 4: Financial Forensics & Health\n\n"
                    f"**Altman Z-Score**: {altman_z} (Safe Zone, low bankruptcy risk).\n"
                    f"**Beneish M-Score**: {beneish_m} ({beneish_note}).\n"
                    "**Operating Cash Flow vs Net Income**: Aligned, indicating sound earnings quality."
                ),
                "SECTION_5_VALUATION": (
                    f"# Section 5: Valuation & Multiple Diagnostics\n\n"
                    f"**PEG (TTM)**: {peg_ttm}x\n"
                    f"**PEG (Forward)**: {peg_fwd}x\n"
                    "Multiples are balanced against historical 5-year averages and sector peer medians."
                ),
                "SECTION_12_TECHNICALS": (
                    f"# Section 12: Technical Diagnostics for {ticker}\n\n"
                    "Moving averages demonstrate an intact medium-term bullish consolidation. "
                    "RSI-14 stands at 54.2 with neutral-to-positive momentum."
                ),
            },
            "artifacts": {
                "AltmanZ": {
                    "provenance": "synthetic",
                    "score": altman_z,
                    "classification": "Safe Zone",
                    "inputs": {
                        "X1": 0.24,
                        "X2": 0.36,
                        "X3": 0.29,
                        "X4": 1.90,
                        "X5": 1.12,
                    },
                },
                "BeneishM": {
                    **beneish,
                    "provenance": "deterministic",
                    "calculation_witness": beneish_witness(
                        indices=beneish_indices, result=beneish["raw_score"]
                    ).to_dict(),
                },
                "OCF_vs_NI_Flag": "Aligned",
                "PEG_TTM": peg_ttm,
                "PEG_Fwd": peg_fwd,
                "HypeFlag": "Reality",
                "PeerTable": [
                    {
                        "ticker": f"{ticker}_PEER1",
                        "metric": "P/E",
                        "company": 27.5,
                        "peer_median": 25.8,
                    },
                    {
                        "ticker": f"{ticker}_PEER2",
                        "metric": "EV/Sales",
                        "company": 6.8,
                        "peer_median": 6.2,
                    },
                ],
                "TechnicalSet": {
                    "SMA_50": 148.5,
                    "SMA_200": 141.2,
                    "RSI_14": 54.2,
                    "MACD": {"line": 1.15, "signal": 0.85},
                    "Bollinger": {"upper": 156.0, "lower": 142.0},
                },
            },
            "sources": [
                {
                    "type": "Financial Model",
                    "url": "https://fuwforge.internal/quant/a3",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 145,
                "tokens": 510,
                "retries": 0,
            },
            "confidence": 0.96,
        }

    def _generate_a4(self, context: AgentContext) -> Dict[str, Any]:
        ticker = context.params.get("ticker", "TICK")
        glassdoor = round(
            4.2 + (random.uniform(-0.2, 0.3) if self.randomize else 0.0), 1
        )

        return {
            "schema_version": "2.1",
            "agent": {"id": "A4", "name": "Leadership-Governance-ESG"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_6_LEADERSHIP_GOVERNANCE": (
                    f"# Section 6: Leadership & Corporate Governance\n\n"
                    "Independent board representation stands at 82%. Audit and compensation committees "
                    "are comprised entirely of non-executive directors. Auditor tenure is stable at 12 years."
                ),
                "SECTION_7_CULTURE_ESG": (
                    f"# Section 7: Corporate Culture & ESG Profile\n\n"
                    f"Employee sentiment reflects high leadership confidence with Glassdoor rating of {glassdoor}/5.0. "
                    "ESG momentum is categorized as Improving due to carbon reduction initiatives."
                ),
            },
            "artifacts": {
                "CEO_Pay_Ratio": 118.5,
                "Auditor": "PricewaterhouseCoopers LLP",
                "AuditorTenure": "12 years",
                "GlassdoorScore": glassdoor,
                "AttritionNote": "Normal voluntary attrition (7.1% annualized)",
                "ESG_Momentum": "Improving",
                "LowSampleSizeCultureData": False,
            },
            "sources": [
                {
                    "type": "DEF 14A Proxy",
                    "url": "https://sec.gov/Archives/edgar/data/def14a",
                    "doc_date": "2024-04-15",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 95,
                "tokens": 390,
                "retries": 0,
            },
            "confidence": 0.94,
        }

    def _generate_a5(self, context: AgentContext) -> Dict[str, Any]:
        return {
            "schema_version": "2.1",
            "agent": {"id": "A5", "name": "News-Sentiment-Flows"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_8_NEWS_SENTIMENT": (
                    "# Section 8: News Sentiment Analysis\n\n"
                    "Qualitative headline flow over the trailing 90 days remains 64% positive, "
                    "focused on new enterprise product rollouts and key executive additions."
                ),
                "SECTION_9_FLOW_LITIGATION": (
                    "# Section 9: Insider Flows, Institutional Positioning & Litigation\n\n"
                    "Form 4 filings reflect moderate net insider purchases. Institutional sponsorship "
                    "increased +1.6% QoQ. No active material litigation cases pending."
                ),
            },
            "artifacts": {
                "SentimentSplit": {
                    "pos": 0.64,
                    "neu": 0.26,
                    "neg": 0.10,
                    "lookback_days": 90,
                },
                "SocialVsFundDivergence": "Aligned",
                "MajorLitigation": [],
                "AuditorChangeYN": "No",
                "InsiderNet12m": {
                    "shares": 42000,
                    "value": 6300000.0,
                    "direction": "NetBuy",
                },
                "InstitutionalOwnershipTrend": {"direction": "Up", "delta_pct": 1.6},
                "RegulatoryWatchlist": [],
            },
            "sources": [
                {
                    "type": "News Wire & SEC Form 4",
                    "url": "https://fuwforge.internal/feeds/sentiment",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 110,
                "tokens": 430,
                "retries": 0,
            },
            "confidence": 0.93,
        }

    def _generate_a6b(self, context: AgentContext) -> Dict[str, Any]:
        return {
            "schema_version": "2.1",
            "agent": {"id": "A6B", "name": "Bull-Case"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_10_BULL_CASE": (
                    "# Section 10: Bull Case Theses\n\n"
                    "1. **Enterprise Cloud Expansion**: High net retention (>118%) drives compounding recurring revenue.\n"
                    "2. **Operating Margin Leverage**: Automation and infrastructure optimization expand operating margins by +200 bps.\n"
                    "3. **Capital Return Acceleration**: Expanding FCF yields enable robust annual share repurchases."
                )
            },
            "artifacts": {
                "BullCase": [
                    {
                        "id": "B1",
                        "thesis": "Enterprise Cloud Expansion",
                        "evidence": ["A2.PEER_LIST", "A3.AltmanZ"],
                        "impact": "Topline revenue CAGR +15%",
                    },
                    {
                        "id": "B2",
                        "thesis": "Operating Margin Leverage",
                        "evidence": ["A3.OCF_vs_NI_Flag"],
                        "impact": "Operating margin +200 bps",
                    },
                    {
                        "id": "B3",
                        "thesis": "Capital Return Acceleration",
                        "evidence": ["A5.SentimentSplit"],
                        "impact": "FCF per share +12% annualized",
                    },
                ]
            },
            "sources": [
                {
                    "type": "Synthesis",
                    "url": "internal:a6b",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 80,
                "tokens": 310,
                "retries": 0,
            },
            "confidence": 0.95,
        }

    def _generate_a6r(self, context: AgentContext) -> Dict[str, Any]:
        return {
            "schema_version": "2.1",
            "agent": {"id": "A6R", "name": "Bear-Case"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_11_BEAR_CASE": (
                    "# Section 11: Bear Rebuttals\n\n"
                    "1. **Deal Elongation Scrutiny**: Macro enterprise IT budget tightening may delay cloud expansions (Probability: Med, Severity: Med).\n"
                    "2. **Talent & Hosting Cost Inflation**: High specialist salaries could offset operational automation gains (Probability: Low, Severity: High).\n"
                    "3. **Multiple De-rating Risk**: Premium PEG valuation multiples remain vulnerable if growth decelerates below 10% (Probability: Med, Severity: Med)."
                )
            },
            "artifacts": {
                "BearRebuttals": [
                    {
                        "bull_id": "B1",
                        "probability": "Med",
                        "severity": "Med",
                        "counter": "Macro enterprise IT budget scrutiny could delay expansion cycles.",
                    },
                    {
                        "bull_id": "B2",
                        "probability": "Low",
                        "severity": "High",
                        "counter": "AI compute capex and engineer hiring costs could compress gross margins.",
                    },
                    {
                        "bull_id": "B3",
                        "probability": "Med",
                        "severity": "Med",
                        "counter": "Multiple de-rating could offset share repurchases if revenue guidance softens.",
                    },
                ]
            },
            "sources": [
                {
                    "type": "Synthesis",
                    "url": "internal:a6r",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 85,
                "tokens": 330,
                "retries": 0,
            },
            "confidence": 0.94,
        }

    def _generate_a6(self, context: AgentContext) -> Dict[str, Any]:
        return {
            "schema_version": "2.1",
            "agent": {"id": "A6", "name": "CIO-Synthesis"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_14_SYNTHESIS": (
                    "# Section 14: Master Synthesis & Scenario Lab\n\n"
                    "### Danger-Zone Dashboard\n"
                    "All core accounting, governance, and liquidity flags remain in the green band.\n\n"
                    "### 3-Year Forward Scenario Matrix\n"
                    "| Scenario | FY+1 Revenue | FY+1 EPS | FCF / Share | Probability |\n"
                    "|---|---|---|---|---|\n"
                    "| Bull Case | $51.5B (+16%) | $8.20 | $9.10 | 30% |\n"
                    "| Base Case | $47.8B (+11%) | $7.30 | $8.00 | 55% |\n"
                    "| Bear Case | $42.5B (+2%) | $5.90 | $6.40 | 15% |\n"
                )
            },
            "artifacts": {
                "DangerZoneDashboard": [
                    {
                        "category": "Accounting anomalies",
                        "flag": "🟢",
                        "sources": ["A3.BeneishM", "A3.OCF_vs_NI_Flag"],
                        "note": "Clean accruals and cash conversion",
                    },
                    {
                        "category": "Customer concentration",
                        "flag": "🟢",
                        "sources": ["A2.TopCustomerPct"],
                        "note": "No single customer exceeds 10%",
                    },
                    {
                        "category": "Litigation & regulatory",
                        "flag": "🟢",
                        "sources": ["A5.MajorLitigation"],
                        "note": "No pending class actions",
                    },
                    {
                        "category": "Hype vs reality",
                        "flag": "🟢",
                        "sources": ["A3.HypeFlag"],
                        "note": "Valuation supported by cash flow fundamentals",
                    },
                    {
                        "category": "Balance sheet shock buffer",
                        "flag": "🟢",
                        "sources": ["A3.AltmanZ"],
                        "note": "Altman Z safe zone cushion",
                    },
                ],
                "ScenarioTable": {
                    "Bull": {
                        "Revenue": "$51.5B (+16%)",
                        "EPS": "$8.20",
                        "FCFPerShare": "$9.10",
                        "Drivers": "Cloud market share gains, enterprise expansion",
                    },
                    "Base": {
                        "Revenue": "$47.8B (+11%)",
                        "EPS": "$7.30",
                        "FCFPerShare": "$8.00",
                        "Drivers": "Steady 11% growth, guided operating leverage",
                    },
                    "Bear": {
                        "Revenue": "$42.5B (+2%)",
                        "EPS": "$5.90",
                        "FCFPerShare": "$6.40",
                        "Drivers": "Extended deal approvals, modest pricing concessions",
                    },
                },
                "Catalysts": [
                    "Next-generation enterprise platform launch",
                    "Analyst day investor presentation and long-term margin target updates",
                    "International datacenter footprint deployment",
                ],
                "Headwinds": [
                    "Foreign exchange headwinds in EMEA region",
                    "Elevated enterprise IT scrutiny for non-essential software",
                ],
            },
            "sources": [
                {
                    "type": "Synthesis",
                    "url": "internal:a6",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 140,
                "tokens": 530,
                "retries": 0,
            },
            "confidence": 0.96,
        }

    def _generate_a7(self, context: AgentContext) -> Dict[str, Any]:
        ticker = context.params.get("ticker", "TICK")
        company = context.params.get("company", f"{ticker} Corp")

        weights, weight_errors = resolve_weights(context.params.get("weights"))
        if weight_errors:
            # Invalid overrides are surfaced, and canonical weights are used.
            weights = resolve_weights("default")[0]
        scores = {
            "Financials": 8.5,
            "Moat": 8.0,
            "Growth": 7.5,
            "Valuation": 6.8,
            "MgmtGov": 8.4,
            "RiskInverse": 8.0,
            "ESG": 7.2,
            "Market": 7.6,
        }
        if self.randomize:
            scores = {k: round(random.uniform(6.5, 9.5), 1) for k in scores}

        scorecard_dict: Dict[str, Any] = {}
        total_weighted = 0.0
        for cat, weight in weights.items():
            sc = scores[cat]
            w_score = round(sc * weight, 3)
            scorecard_dict[cat] = {"weight": weight, "score": sc, "weighted": w_score}
            total_weighted += w_score

        overall_score = round(total_weighted, 2)
        rating_band = (
            "Buy"
            if overall_score >= 7.5
            else ("Hold" if overall_score >= 6.0 else "Sell")
        )
        scorecard_dict["Overall"] = {"score": overall_score, "rating_band": rating_band}

        return {
            "schema_version": "2.1",
            "agent": {"id": "A7", "name": "Final-Assembly"},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                "SECTION_0_EXEC_SUMMARY": (
                    f"# Section 0: Executive Summary — {company} ({ticker})\n\n"
                    f"**Investment Recommendation**: {rating_band} (Overall Institutional Score: {overall_score}/10)\n\n"
                    f"{company} represents a resilient, high-quality franchise with durable competitive advantages. "
                    "The business demonstrates expanding free cash flow conversion, investment-grade balance sheet liquidity, "
                    "and attractive secular growth opportunities."
                ),
                "SECTION_13_IPO_HISTORY": (
                    f"# Section 13: Capital History & Share Count Evolution\n\n"
                    f"{company} completed its initial public offering in 2018. Over the past 5 years, the company has "
                    "consistently retired shares via open-market repurchases, keeping net dilution under 0.8% annually."
                ),
                "SECTION_15_SWOT": (
                    f"# Section 15: Institutional SWOT Matrix\n\n"
                    "| Category | Points |\n"
                    "|---|---|\n"
                    "| **Strengths** | High recurring software revenue, negative net debt, pricing power |\n"
                    "| **Weaknesses** | Premium multiple provides modest margin of safety |\n"
                    "| **Opportunities** | Enterprise cloud penetration, international expansion |\n"
                    "| **Threats** | Hyperscaler platform consolidation, macro elongation |\n"
                ),
                "SECTION_16_SCORECARD": (
                    f"# Section 16: Institutional Scorecard (FUW Quality Matrix)\n\n"
                    f"**Overall Score**: {overall_score} / 10.0 ({rating_band})\n\n"
                    f"| Category | Weight | Raw Score | Weighted Score |\n"
                    f"|---|---|---|---|\n"
                    + "\n".join(
                        f"| {cat} | {weights[cat] * 100:.1f}% | {scores[cat]:.1f} | {scorecard_dict[cat]['weighted']:.3f} |"
                        for cat in weights
                    )
                    + f"\n| **Total** | 100.0% | — | **{overall_score:.2f}** |\n"
                ),
                "SECTION_17_GLOSSARY": (
                    "# Section 17: Financial & Analytical Glossary\n\n"
                    "- **Altman Z-Score**: Predictive metric measuring probability of corporate bankruptcy.\n"
                    "- **Beneish M-Score**: Mathematical model evaluating likelihood of earnings manipulation.\n"
                    "- **Free Cash Flow (FCF)**: Cash generated after supporting operations and maintaining capital assets."
                ),
                "SECTION_18_LAY_SUMMARY": (
                    f"# Section 18: Lay Summary for Everyday Investors\n\n"
                    f"In simple terms, {company} makes essential software tools that large companies depend on every day. "
                    "Because customers rarely cancel, the company brings in reliable cash year after year and uses that cash "
                    "to improve products and buy back shares."
                ),
            },
            "artifacts": {
                "Scorecard": scorecard_dict,
                "SWOT": {
                    "Strengths": [
                        "Durable recurring subscription revenues",
                        "Investment-grade balance sheet liquidity",
                        "Proven enterprise pricing power",
                    ],
                    "Weaknesses": [
                        "Premium multiple requires consistent operational execution",
                        "Concentration in North American enterprise market",
                    ],
                    "Opportunities": [
                        "Expansion of automated workflow modules",
                        "Platform cross-selling to existing installed base",
                    ],
                    "Threats": [
                        "Budget consolidation onto single platform suites",
                        "Regulatory scrutiny regarding M&A integrations",
                    ],
                    "Watchlist": [
                        "Gross margin trajectory next quarter",
                        "Enterprise renewal rate velocity",
                    ],
                },
                "Glossary": [
                    {
                        "term": "Altman Z-Score",
                        "definition": "Predictive metric measuring probability of corporate bankruptcy.",
                    },
                    {
                        "term": "Beneish M-Score",
                        "definition": "Quantitative model assessing likelihood of financial statement manipulation.",
                    },
                    {
                        "term": "Free Cash Flow (FCF)",
                        "definition": "Cash generated after supporting operations and maintaining capital assets.",
                    },
                ],
                "comparison_fields": generate_comparison_fields(
                    quality_score_10=overall_score,
                    valuation_score_10=float(scores.get("Valuation", 6.8)),
                    expected_5y_irr_base_pct=12.5,
                    downside_case_5y_irr_pct=-3.5,
                    fcf_yield_ttm_pct=3.6,
                    roic_ttm_pct=28.4,
                    net_debt_to_ebitda_val=-0.45,
                    moat_score_10=float(scores.get("Moat", 8.0)),
                    reinvestment_runway_score_10=float(scores.get("Growth", 7.5)),
                    balance_sheet_score_10=float(scores.get("Financials", 8.5)),
                    fragility_score_10=round(
                        10.0 - float(scores.get("RiskInverse", 8.0)), 1
                    ),
                    evidence_quality_score_10=9.0,
                    data_completeness_score_10=9.0,
                ),
            },
            "sources": [
                {
                    "type": "Assembly",
                    "url": "internal:a7",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": list(weight_errors),
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 160,
                "tokens": 620,
                "retries": 0,
            },
            "confidence": 0.97,
        }

    def _generate_fallback(self, context: AgentContext) -> Dict[str, Any]:
        return {
            "schema_version": "2.1",
            "agent": {"id": self.id, "name": self.name},
            "timestamp": _now_iso(),
            "params": context.params,
            "gates": {"passed": True, "notes": []},
            "sections_md": {
                f"SECTION_{self.id}": f"# Section from {self.name}\n\nGenerated output."
            },
            "artifacts": {},
            "sources": [
                {
                    "type": "Internal",
                    "url": f"internal:{self.id.lower()}",
                    "doc_date": "2025-01-31",
                    "accessed": _now_iso(),
                }
            ],
            "warnings": [],
            "metrics": {
                "data_checksum": context.frozen_checksum,
                "latency_ms": 50,
                "tokens": 150,
                "retries": 0,
            },
            "confidence": 0.90,
        }


# Agent subclass convenience bindings
class A1Mock(MockAgent):
    id = "A1"
    name = "Harvester-&-Validator"


class A2Mock(MockAgent):
    id = "A2"
    name = "Business-Market-Peers"


class A3Mock(MockAgent):
    id = "A3"
    name = "Quant-Valuation-Technicals"


class A4Mock(MockAgent):
    id = "A4"
    name = "Leadership-Governance-ESG"


class A5Mock(MockAgent):
    id = "A5"
    name = "News-Sentiment-Flows"


class A6BMock(MockAgent):
    id = "A6B"
    name = "Bull-Case"


class A6RMock(MockAgent):
    id = "A6R"
    name = "Bear-Case"


class A6Mock(MockAgent):
    id = "A6"
    name = "CIO-Synthesis"


class A7Mock(MockAgent):
    id = "A7"
    name = "Final-Assembly"
