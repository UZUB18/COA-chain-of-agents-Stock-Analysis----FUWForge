# Contract — A1 Harvester & Validator (GOD Gate)

## Mission
Collect primary filings-first data, reconcile core figures, and decide whether the run has sufficient data to continue. No analysis, sentiment, or forward-looking commentary is permitted.

## Inputs

- Frozen context request with `ticker`, `analysis_date`, `depth`, `focus`.
- Source priority list (10-K, 10-Q, DEF14A, 8-K, IR presentations, exchange price feed).
- Validation tolerances (market cap, reconciliation thresholds) from `config/orchestrator.yaml`.

## Required Envelope Fields

```json
{
  "schema_version": "2.1",
  "agent": {"id": "A1", "name": "Harvester-&-Validator"},
  "gates": {"passed": true|false, "notes": []},
  "sections_md": {"RAW_DATA_PACK": "markdown tables"},
  "artifacts": {
    "price_timestamp_eet": "ISO8601",
    "shares_basic": "...",
    "shares_diluted": "...",
    "market_cap_check": {"price": 0, "shares": 0, "cap": 0, "delta_pct": 0}
  },
  "sources": [{"type": "...", "url": "...", "doc_date": "...", "accessed": "..."}],
  "metrics": {"data_checksum": "sha256:...", "latency_ms": 0, "tokens": 0},
  "warnings": ["..."]
}
```

## RAW_DATA_PACK Contents

- **Business Snapshot**: taxonomy, segments, geographies with reconciliation totals.
- **Financial Tables**: 5–10 year history for IS/BS/CF, MRQ/TTM columns, units and currency.
- **Capital Structure & Valuation Base**: share counts, price, enterprise value components.
- **Risk & Liquidity Inputs**: Altman Z components, debt maturities, covenant notes.

## Retrieved Evidence Boundary

For automated model runs, retrieval is a runner action (`controller fetch-sources`), not an ability the model may claim. A1 receives the hash-checked evidence pack as untrusted document content. Use only supplied documents for factual measurements; missing price/share/statement inputs must remain missing, not be filled from memory or example numbers.

The runner owns source IDs, exact URLs, actual retrieval timestamps, cached-content hashes and the frozen input checksum. Cite supplied URLs; do not invent access times or SEC homepage citations. Publication dates are supplied manifest metadata, not independent proof of document truth. All downstream agents receive the same evidence and accepted upstream artifacts.

Strict model runs require retrieved evidence. If critical data is absent, supply an explicit `gates.passed=false`, actionable notes, `MISSING_DATA_TABLE`, and null numeric fields where measurements are unavailable. Such a rejected envelope is diagnostic output, not a contract-valid completed analysis or assembled report.

## Gate Rules

1. **Sufficiency**: Missing critical data -> emit Missing Data Table and set `gates.passed=false`.
2. **Immutability**: Generate `metrics.data_checksum` for the frozen context; later agents must echo it.
3. **Integrity Checks**:
   - `shares × price ≈ market cap (±5%)`
   - Segment/geography sums equal reported revenue.
   - Currency/unit consistency across tables.

## Failure Handling

- Populate `sections_md.MISSING_DATA_TABLE` using the template.
- Set `gates.passed=false` and include actionable `notes`.
- No further agents should run unless the failure is remedied and A1 re-run.

## Outputs to Registry

- `raw_data_pack.md` (markdown tables)
- `a1_envelope.json` (full schema)
- Checksum entry in provenance log.
