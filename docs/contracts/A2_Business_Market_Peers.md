# Contract — A2 Business, Market, Peers

## Mission
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
- Validate concentration metrics: if unavailable, state “Not disclosed” explicitly.
- Echo `metrics.data_checksum` from A1.

## Outputs to Registry

- `a2_envelope.json`
- `section_1_3.md` (concatenated markdown for sections 1–3)
- Update provenance with peer selection rationale.
