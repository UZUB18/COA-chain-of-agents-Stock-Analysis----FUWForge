# Quick Checklist — “I Am Become the Orchestrator”

Tick these boxes after each agent to keep the run clean.

## Global
- [ ] A1 checksum pasted into every downstream prompt.
- [ ] All envelopes include schema_version, agent, gates, sections_md, artifacts, sources, metrics.
- [ ] Sources list URL, doc_date, accessed timestamp.

## Agent-Specific

### A1
- [ ] `gates.passed` true (or Missing Data Table emitted).
- [ ] RAW_DATA_PACK includes business, financial, valuation, risk tables with units.
- [ ] Artifacts present: price_timestamp_eet, shares_basic, shares_diluted, market_cap_check.
- [ ] `metrics.data_checksum` captured.

### A2
- [ ] PEER_LIST has ≥3 tickers with rationale.
- [ ] MoatTrend = Expanding / Stable / Eroding / Unclear.
- [ ] TopCustomerPct & TopSupplierPct filled or “Not disclosed”.

### A3
- [ ] AltmanZ includes inputs + classification.
- [ ] BeneishM includes feasible flag + score.
- [ ] TechnicalSet covers SMA50/200, RSI, MACD, Bollinger, support/resistance.
- [ ] HypeFlag set.

### A4
- [ ] CEO_Pay_Ratio numeric.
- [ ] Auditor + AuditorTenure present.
- [ ] GlassdoorScore numeric (note low sample if needed).
- [ ] AttritionNote explicit.
- [ ] ESG_Momentum set (Improving/Stable/Deteriorating/Unclear).
- [ ] LowSampleSizeCultureData boolean.

### A5
- [ ] SentimentSplit has pos/neu/neg totals + lookback days.
- [ ] SocialVsFundDivergence = Aligned / Mild / Pump Risk.
- [ ] MajorLitigation list populated or explained if empty.
- [ ] AuditorChangeYN set.
- [ ] InsiderNet12m includes shares, value, direction.
- [ ] InstitutionalOwnershipTrend includes direction + delta_pct.
- [ ] RegulatoryWatchlist array present (empty allowed).

### A6B
- [ ] ≥3 bull theses (B1…).
- [ ] Each thesis cites upstream artifacts.

### A6R
- [ ] Rebuttal for every bull ID.
- [ ] Probability + severity fields populated.

### A6
- [ ] Danger-Zone dashboard has 5 categories with 🟢/🟡/🔴.
- [ ] Scenario table covers Bull/Base/Bear with revenue/EPS/FCF/share + drivers.
- [ ] Catalysts[] and Headwinds[] non-empty.

### A7
- [ ] Table of contents (Section 0) present.
- [ ] SWOT includes watchlist.
- [ ] Scorecard shows math + overall band.
- [ ] Glossary includes ≥3 terms.
- [ ] Lay summary included unless depth = Quick.

## Final Release
- [ ] Run card + envelopes saved.
- [ ] Final report assembled in section order.
- [ ] Personal notes / timestamps captured.
