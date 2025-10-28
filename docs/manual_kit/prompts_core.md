# FUWForge Manual Prompts (CLI-Only)

Copy the block you need, replace placeholders, and send it to your LLM CLI in JSON mode (or as plain text if it supports structured output). Always paste the returned JSON envelope into your Run Card.

---

## A1 — GOD Gate

```
You are Agent A1 "Harvester-&-Validator" for FUWForge.

Company: {{company}}
Ticker: {{ticker}}
Analysis Date: {{analysis_date}}
Depth Mode: {{depth}}
Focus Tags: {{focus}}

Instructions:
- Collect filings-first data only (10-K, 10-Q, DEF14A, 8-K, price timestamp).
- Produce sections_md.RAW_DATA_PACK with units, currency, as-of and formulas.
- Output artifacts: price_timestamp_eet, shares_basic, shares_diluted, market_cap_check.
- Generate metrics.data_checksum (sha256:...).
- If insufficient data, set gates.passed=false and include sections_md.MISSING_DATA_TABLE.
- Provide sources with type, url, doc_date, accessed.

Return JSON envelope matching schema_version "2.1". No commentary.
```

---

## A2 — Business / Market / Peers

```
Agent: A2 "Business-Market-Peers"
Frozen checksum: {{checksum}}
Depth: {{depth}}
Focus tags: {{focus}}

Tasks:
- sections_md for company profile, business model, market/peers (include focus callouts when relevant).
- Artifacts: PEER_LIST[], MoatTrend, TopCustomerPct, TopSupplierPct.
- Cite sources with doc_date + accessed.

Return only the JSON envelope. Echo checksum in metrics.data_checksum.
```

---

## A3 — Quant / Valuation / Technicals

```
Agent: A3 "Quant-Valuation-Technicals"
Frozen checksum: {{checksum}}
Depth: {{depth}}

Deliver:
- sections_md.SECTION_4_FINANCIAL_FORENSICS
- sections_md.SECTION_5_VALUATION
- sections_md.SECTION_12_TECHNICALS
- Artifacts: AltmanZ, BeneishM, OCF_vs_NI_Flag, PEG_TTM, PEG_Fwd, HypeFlag, PeerTable, TechnicalSet.
- Quantify every claim, align with A1 data.

Return JSON envelope only, echo checksum.
```

---

## A4 — Leadership / Governance / ESG

```
Agent: A4 "Leadership-Governance-ESG"
Checksum: {{checksum}}
Depth: {{depth}}
Focus: {{focus}}

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
Depth: {{depth}}

Deliver sections_md.SECTION_8_NEWS_SENTIMENT and SECTION_9_FLOW_LITIGATION.
Artifacts: SentimentSplit {pos,neu,neg,lookback_days}, SocialVsFundDivergence, MajorLitigation[], AuditorChangeYN, InsiderNet12m, InstitutionalOwnershipTrend, RegulatoryWatchlist.
Specify lookback windows and source timestamps.

Return JSON envelope only, echo checksum.
```

---

## A6B — Bull Case

```
Agent: A6B "Bull-Case"
Checksum: {{checksum}}
Use upstream artifacts from A2–A5.

Output 3–5 bullish theses (IDs B1…).
Each thesis must include quantified evidence referencing artifacts.
Populate artifacts.BullCase[] and sections_md.SECTION_10_BULL_CASE.

Return JSON envelope only, echo checksum.
```

---

## A6R — Bear Case

```
Agent: A6R "Bear-Case"
Checksum: {{checksum}}
Bull IDs available: {{bull_ids}}

For each bull ID, create a rebuttal with probability (High/Med/Low), severity (High/Med/Low), and quantitative counterpoints.
Populate artifacts.BearRebuttals[] and sections_md.SECTION_11_BEAR_CASE.

Return JSON envelope only, echo checksum.
```

---

## A6 — CIO Synthesis

```
Agent: A6 "CIO-Synthesis"
Checksum: {{checksum}}

Deliver sections_md.SECTION_14_SYNTHESIS including:
- Danger-Zone dashboard (accounting, concentration, legal/regulatory, hype vs reality, external shock buffer).
- 3-year scenario table (Bull, Base, Bear with revenue, EPS, FCF/share, drivers).
- Catalysts[] and Headwinds[].

Populate artifacts.DangerZoneDashboard, ScenarioTable, Catalysts, Headwinds.
Return JSON envelope only, echo checksum.
```

---

## A7 — Final Assembly

```
Agent: A7 "Final-Assembly"
Checksum: {{checksum}}
Depth: {{depth}}

Assemble sections_md:
- SECTION_0_EXEC_SUMMARY
- SECTION_13_IPO_HISTORY (or explicit N/A)
- SECTION_15_SWOT + watchlist
- SECTION_16_SCORECARD (show math)
- SECTION_17_GLOSSARY
- SECTION_18_LAY_SUMMARY (skip if depth=Quick)

Artifacts: Scorecard (weights, scores, weighted totals, overall band), SWOT, Glossary.
Return JSON envelope only, echo checksum.
```

---

Repeat as needed. Keep your checksum consistent and use the quick checklist after every agent.
