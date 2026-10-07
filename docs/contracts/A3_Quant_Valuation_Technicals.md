# Contract — A3 Quant, Valuation, Technicals

## Mission

### Explicit unavailable-data exception

Required keys remain present. A literal null is allowed only for paths listed for this agent in `src/data_gaps.py`, with a non-empty, exact field/reason entry in `metrics.missing_data` (canonical provider format: `[{"field": "artifact.path", "reason": "Evidence reviewed and why this measurement is unavailable"}]`). The runner renders a data-gap table; these reasons are included in the content checksum. This does not excuse invented citations, missing required keys, malformed non-null values or unknown critical A1 measurements. Null means unavailable—not zero, neutral, flat or no events. Sentiment is unavailable as a whole object, never partially null fractions. An unavailable Glassdoor score requires `LowSampleSizeCultureData=true`; an unavailable Beneish score requires `feasible=false` and an explanation. Reports with such gaps explicitly disclose incomplete research coverage.

Deliver quantitative forensics across financial statements, valuation history, and technical indicators. Highlight manipulation risks, solvency, and valuation context versus peers and history.

## Required Artifacts

```json
{
  "AltmanZ": {"score": 0.0, "classification": "...", "inputs": {...}},
  "BeneishM": {"feasible": true|false, "score": 0.0, "classification": "..."},
  "OCF_vs_NI_Flag": "Aligned|Divergent|Negative",
  "PEG_TTM": 0.0,
  "PEG_Fwd": 0.0,
  "HypeFlag": "Reality|Balanced|Hype Risk",
  "PeerTable": [
    {"ticker": "T1", "metric": "...", "company": 0.0, "peer_median": 0.0}
  ],
  "TechnicalSet": {
    "SMA_50": 0.0,
    "SMA_200": 0.0,
    "RSI_14": 0.0,
    "MACD": {"line": 0.0, "signal": 0.0},
    "Bollinger": {"upper": 0.0, "lower": 0.0},
    "SupportResistance": [{"level": 0.0, "type": "Support|Resistance"}]
  }
}
```

## Sections Markdown

- `sections_md.SECTION_4_FINANCIAL_FORENSICS`: IS/BS/CF trends, margin evolution, cash conversion.
- `sections_md.SECTION_5_VALUATION`: multiples vs 5y history and peer median, DCF/DCF-lite assumptions if depth permits.
- `sections_md.SECTION_12_TECHNICALS`: trend diagnostics, momentum indicators, trading range notes.

## Rules & Checks

- Use MRQ/TTM data when available; otherwise document gaps.
- Reconcile trailing metrics with A1 raw data tables to ensure consistency.
- Apply MEAF: every claim backed by numbers (e.g., “Gross margin expanded +180 bps YoY”).
- `HypeFlag` anchors to comparison of narrative vs fundamentals (cross-check with A5 social sentiment).
- If Beneish M-Score not feasible (e.g., missing data), set `feasible=false` and explain in warnings.
- Echo the checksum from A1.

## Outputs to Registry

- `a3_envelope.json`
- `forensics_tables.xlsx` (optional structured export, if generated)
- Provenance entries for each red/yellow manipulation flag raised.
