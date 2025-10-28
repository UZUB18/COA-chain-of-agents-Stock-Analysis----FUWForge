# FUWForge Quick Checklist (Manual Runs)

Use this card after each agent to prevent missing artifacts or broken gates.

## Global Rules
- [ ] A1 checksum copied and pasted into every downstream prompt.
- [ ] Every JSON response includes `schema_version`, `agent`, `gates`, `sections_md`, `artifacts`, `sources`, `metrics`.
- [ ] Sources include URL + doc_date + accessed timestamp whenever available.

## Agent Checks

### A1
- [ ] `gates.passed` true (or Missing Data Table present).
- [ ] RAW_DATA_PACK tables include units, currency, as-of dates, formulas.
- [ ] Artifacts `price_timestamp_eet`, `shares_basic`, `shares_diluted`, `market_cap_check`.
- [ ] `metrics.data_checksum` captured.

### A2
- [ ] `PEER_LIST` has ≥3 entries with tickers.
- [ ] `MoatTrend` value: Expanding / Stable / Eroding / Unclear.
- [ ] `TopCustomerPct` and `TopSupplierPct` filled or “Not disclosed”.

### A3
- [ ] `AltmanZ` contains inputs + classification.
- [ ] `BeneishM` includes `feasible` + score.
- [ ] `TechnicalSet` lists SMA50/200, RSI, MACD, Bollinger, support/resistance.
- [ ] `HypeFlag` value set.

### A4
- [ ] `CEO_Pay_Ratio` numeric.
- [ ] `Auditor` + `AuditorTenure` captured.
- [ ] `GlassdoorScore` numeric (or note low sample).
- [ ] `AttritionNote` explicit (data or “Not disclosed”).
- [ ] `ESG_Momentum` (Improving/Stable/Deteriorating/Unclear).
- [ ] `LowSampleSizeCultureData` boolean.

### A5
- [ ] `SentimentSplit` has pos/neu/neg totals (record lookback days).
- [ ] `SocialVsFundDivergence` (Aligned/Mild/Pump Risk).
- [ ] `MajorLitigation` list populated or empty array with explanation.
- [ ] `AuditorChangeYN` set.
- [ ] `InsiderNet12m` includes shares, value, direction.
- [ ] `InstitutionalOwnershipTrend` direction + delta_pct.
- [ ] `RegulatoryWatchlist` array (empty allowed).

### A6B
- [ ] At least 3 bull theses with IDs (B1…).
- [ ] Each thesis cites upstream artifacts.

### A6R
- [ ] Rebuttal for every bull ID.
- [ ] Probability & severity fields populated.

### A6
- [ ] Danger-Zone dashboard has 5 categories with 🟢/🟡/🔴 flag.
- [ ] Scenario table covers Bull/Base/Bear with revenue, EPS, FCF/share, drivers.
- [ ] Catalysts[] + Headwinds[] not empty.

### A7
- [ ] ToC (Section 0) present.
- [ ] SWOT includes watchlist.
- [ ] Scorecard shows math and overall rating band.
- [ ] Glossary with ≥3 definitions.
- [ ] Lay summary included when depth ≠ Quick.

## Before Release
- [ ] All JSON envelopes saved with run card.
- [ ] Final report assembled in Sections 0–18 order.
- [ ] Personal notes/provenance timestamps recorded.
