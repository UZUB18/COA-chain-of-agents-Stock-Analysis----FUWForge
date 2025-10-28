# Manual Prompts — “I Am Become the Orchestrator”

Copy the block you need, replace placeholders, and run it through your LLM CLI with JSON/fixed-format output. Paste the response into your run card.

---

## A1 — GOD Gate

```
Agent: A1 "Harvester-&-Validator"
Company: {{company}}
Ticker: {{ticker}}
Analysis Date: {{analysis_date}}
Depth Mode: {{depth_mode}}
Focus Tags: {{focus_tags}}

Instructions:
- Gather filings-first data (10-K, 10-Q, DEF14A, 8-K, price timestamp).
- Render sections_md.RAW_DATA_PACK with units, currency, as-of, formulas.
- Emit artifacts: price_timestamp_eet, shares_basic, shares_diluted, market_cap_check.
- Generate metrics.data_checksum (sha256:...).
- If data missing, set gates.passed=false and output sections_md.MISSING_DATA_TABLE.
- Include sources with type/url/doc_date/accessed.

Return ONLY the JSON envelope (schema_version "2.1").
```

---

## A2 — Business / Market / Peers

```
Agent: A2 "Business-Market-Peers"
Frozen checksum: {{checksum}}
Depth Mode: {{depth_mode}}
Focus Tags: {{focus_tags}}

Deliver:
- sections_md for profile, business model, market & peers (include focus callouts).
- artifacts: PEER_LIST[], MoatTrend, TopCustomerPct, TopSupplierPct.
- Cite primary sources with doc_date + accessed.

Return JSON envelope only, echo checksum in metrics.data_checksum.
```

---

## A3 — Quant / Valuation / Technicals

```
Agent: A3 "Quant-Valuation-Technicals"
Checksum: {{checksum}}
Depth Mode: {{depth_mode}}

Deliver:
- sections_md.SECTION_4_FINANCIAL_FORENSICS
- sections_md.SECTION_5_VALUATION
- sections_md.SECTION_12_TECHNICALS
- artifacts: AltmanZ, BeneishM, OCF_vs_NI_Flag, PEG_TTM, PEG_Fwd, HypeFlag, PeerTable, TechnicalSet.
- Quantify every claim, align with A1 data, cite sources.

Return JSON envelope only, echo checksum.
```

---

## A4 — Leadership / Governance / ESG

```
Agent: A4 "Leadership-Governance-ESG"
Checksum: {{checksum}}
Depth Mode: {{depth_mode}}
Focus Tags: {{focus_tags}}

Deliver sections_md for governance and culture/ESG.
Artifacts required: CEO_Pay_Ratio, Auditor, AuditorTenure, GlassdoorScore, AttritionNote, ESG_Momentum, LowSampleSizeCultureData.
Quantify claims, cite sources, echo checksum.

Return JSON envelope only.
```

---

## A5 — News / Sentiment / Flows

```
Agent: A5 "News-Sentiment-Flows"
Checksum: {{checksum}}
Depth Mode: {{depth_mode}}

Deliver sections_md.SECTION_8_NEWS_SENTIMENT and SECTION_9_FLOW_LITIGATION.
Artifacts: SentimentSplit {pos, neu, neg, lookback_days}, SocialVsFundDivergence, MajorLitigation[], AuditorChangeYN, InsiderNet12m, InstitutionalOwnershipTrend, RegulatoryWatchlist.
Specify lookback windows and source timestamps.

Return JSON envelope only, echo checksum.
```

---

## A6B — Bull Case

```
Agent: A6B "Bull-Case"
Checksum: {{checksum}}
Reference artifacts from A2–A5.

Deliver 3–5 bullish theses (IDs B1…).
Each thesis must cite upstream artifacts and quantify impact.
Populate artifacts.BullCase[] and sections_md.SECTION_10_BULL_CASE.

Return JSON envelope only, echo checksum.
```

---

## A6R — Bear Case

```
Agent: A6R "Bear-Case"
Checksum: {{checksum}}
Bull IDs: {{bull_ids}}

For each bull ID, craft a rebuttal with probability (High/Med/Low) and severity (High/Med/Low) plus quantitative counter-evidence.
Populate artifacts.BearRebuttals[] and sections_md.SECTION_11_BEAR_CASE.

Return JSON envelope only, echo checksum.
```

---

## A6 — CIO Synthesis

```
Agent: A6 "CIO-Synthesis"
Checksum: {{checksum}}

Deliver sections_md.SECTION_14_SYNTHESIS with:
- Danger-Zone dashboard (accounting, concentration, legal/regulatory, hype vs reality, external shock buffer).
- Scenario table (Bull/Base/Bear, 3-year horizon, revenue/EPS/FCF per share + drivers).
- Catalysts[] and Headwinds[] lists.

Populate artifacts.DangerZoneDashboard, ScenarioTable, Catalysts, Headwinds.
Return JSON envelope only, echo checksum.
```

---

## A7 — Final Assembly

```
Agent: A7 "Final-Assembly"
Checksum: {{checksum}}
Depth Mode: {{depth_mode}}

Assemble sections_md:
- SECTION_0_EXEC_SUMMARY
- SECTION_13_IPO_HISTORY (or mark N/A)
- SECTION_15_SWOT (with watchlist items)
- SECTION_16_SCORECARD (show math, weights)
- SECTION_17_GLOSSARY
- SECTION_18_LAY_SUMMARY (skip if depth=Quick)

Artifacts: Scorecard (weights, scores, weighted totals, overall band), SWOT, Glossary.
Return JSON envelope only, echo checksum.
```

---

Repeat until the report is forged. Always paste outputs into the run card and tick the quick checklist before advancing.
