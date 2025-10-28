# Contract — A7 Final Synthesis & Delivery

## Mission
Compile the validated artifacts into an audit-ready, reader-friendly report covering sections 0–18, render the weighted scorecard math, and generate the lay summary when depth permits.

## Inputs

- Validated envelopes from A1–A6 (checksum-matched).
- Depth mode (`Quick`, `Standard`, `Deep`) to determine optional sections.
- Focus tags to highlight specific subsections (e.g., AI strategy callouts).

## Required Artifacts

```json
{
  "Scorecard": {
    "Financials": {"weight": 0.25, "score": 0.0, "weighted": 0.0},
    "Moat": {"weight": 0.20, "score": 0.0, "weighted": 0.0},
    "Growth": {"weight": 0.15, "score": 0.0, "weighted": 0.0},
    "Valuation": {"weight": 0.15, "score": 0.0, "weighted": 0.0},
    "MgmtGov": {"weight": 0.10, "score": 0.0, "weighted": 0.0},
    "RiskInverse": {"weight": 0.10, "score": 0.0, "weighted": 0.0},
    "ESG": {"weight": 0.025, "score": 0.0, "weighted": 0.0},
    "Market": {"weight": 0.025, "score": 0.0, "weighted": 0.0},
    "Overall": {"score": 0.0, "rating_band": "Buy|Hold|Sell"}
  },
  "SWOT": {
    "Strengths": ["..."],
    "Weaknesses": ["..."],
    "Opportunities": ["..."],
    "Threats": ["..."],
    "Watchlist": ["..."]
  },
  "Glossary": [{"term": "...", "definition": "..."}]
}
```

## Sections Markdown

- `sections_md.SECTION_0_EXEC_SUMMARY` (ToC, quick hits)
- `sections_md.SECTION_13_IPO_HISTORY` (or explicit N/A clause)
- `sections_md.SECTION_15_SWOT`
- `sections_md.SECTION_16_SCORECARD`
- `sections_md.SECTION_17_GLOSSARY`
- `sections_md.SECTION_18_LAY_SUMMARY` (skip for Quick depth)

## Rules & Checks

- Scorecard math must show raw score × weight and recompute the overall total.
- Table of contents links to each section by heading anchor.
- Lay summary written in plain language (7th-grade readability) when required.
- Ensure citations carry through from upstream sections.
- Echo checksum from A1 and confirm no new external data introduced without provenance entry.

## Outputs to Registry

- `a7_envelope.json`
- `TICK_YYYY-MM-DD_report.md` (assembled)
- `TICK_YYYY-MM-DD_artifacts.json` (aggregate)
- `TICK_YYYY-MM-DD_provenance.log` (finalized hash chain)
