# Contract — A2 Business, Market, Peers

## Mission

### Explicit unavailable-data exception

Required keys remain present. A literal null is allowed only for paths listed for this agent in `src/data_gaps.py`, with a non-empty, exact field/reason entry in `metrics.missing_data` (canonical provider format: `[{"field": "artifact.path", "reason": "Evidence reviewed and why this measurement is unavailable"}]`). The runner renders a data-gap table; these reasons are included in the content checksum. This does not excuse invented citations, missing required keys, malformed non-null values or unknown critical A1 measurements. Null means unavailable—not zero, neutral, flat or no events. Sentiment is unavailable as a whole object, never partially null fractions. An unavailable Glassdoor score requires `LowSampleSizeCultureData=true`; an unavailable Beneish score requires `feasible=false` and an explanation. Reports with such gaps explicitly disclose incomplete research coverage.

Describe the company’s business model, industry positioning, and peer landscape grounded in filings and trusted primary sources. Quantify concentration risks and strategy tags without forward opinions.

## Inputs

- Frozen context and checksum from A1.
- Focus tags (e.g., `AI strategy`, `Regulatory`) from orchestrator params.
- Peer discovery heuristics (industry classifications, SIC/NAICS, management disclosures).

## Required Artifacts

```json
{
  "PEER_LIST": ["TICK1", "TICK2", "..."],
  "MoatTrend": "Expanding|Stable|Eroding|Unclear",
  "TopCustomerPct": {"name": "Customer A", "percent": 0.0} | "Not disclosed",
  "TopSupplierPct": {"name": "Supplier X", "percent": 0.0} | "Not disclosed"
}
```

## Sections Markdown

- `sections_md.SECTION_1_COMPANY_PROFILE`: mission, revenue streams, segment mix.
- `sections_md.SECTION_2_BUSINESS_MODEL`: operating model, go-to-market, monetization.
- `sections_md.SECTION_3_MARKET_AND_PEERS`: market sizing, Five Forces summary, peer table.
- Optional `sections_md.AI_STRATEGY` when focus includes `AI strategy`.

## Rules & Checks

- Cite primary sources (10-K, investor day transcripts, regulatory filings) with doc dates.
- Quantify statements (e.g., revenue mix percentages, market share estimates) or flag as undisclosed.
- Ensure peers include at least three public tickers with rationale; add private comps if critical.
- Concentration metrics: either a disclosure object with a numeric `percent`, an explicit non-blank string (e.g. “Not disclosed”; the exact wording is not checked), or a documented null with a `metrics.missing_data` reason.
- A provider response of `{"percent": null, "note": "reason"}` is normalized to a null artifact and a documented `missing_data` gap; the reason must be non-empty.
- Echo `metrics.data_checksum` from A1.

## Outputs to Registry

- `a2_envelope.json`
- `section_1_3.md` (concatenated markdown for sections 1–3)
- Update provenance with peer selection rationale.
