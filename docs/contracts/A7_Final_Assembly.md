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
  "Glossary": [{"term": "...", "definition": "..."}],
  "comparison_fields": {
    "quality_score_10": 0.0,
    "valuation_score_10": 0.0,
    "expected_5y_irr_base_pct": 0.0,
    "downside_case_5y_irr_pct": 0.0,
    "fcf_yield_ttm_pct": 0.0,
    "roic_ttm_pct": 0.0,
    "net_debt_to_ebitda": 0.0,
    "moat_score_10": 0.0,
    "reinvestment_runway_score_10": 0.0,
    "balance_sheet_score_10": 0.0,
    "fragility_score_10": 0.0,
    "evidence_quality_score_10": 0.0,
    "data_completeness_score_10": 0.0
  }
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

- Scorecard math must show raw score × weight and recompute the overall total. The weights above are the `default` profile; `--weights` selects another profile (`resolve_weights`), so they are not fixed.
- Table of contents links to each section by heading anchor.
- Lay summary written in plain language (7th-grade readability) when required.
- Ensure citations carry through from upstream sections.
- Echo checksum from A1 and confirm no new external data introduced without provenance entry.

## Canonical Model Output and Evidence

Put structured outputs under `artifacts` and narrative strings under `sections_md`. Gemini receives a per-agent native JSON schema; other model providers receive the same schema in their prompts. Malformed outer envelopes are rejected rather than recovered from nested fragments.

Known alternative layouts can be explicitly mapped: root `comparison_fields`, structured section-16 category scores, section-15 SWOT and section-17 glossary terms. Canonical artifacts take precedence. Scores and supplied financial values are preserved; weighting arithmetic is derived from run weights, not guessed category scores. Runner completeness telemetry is separate from the model's `data_completeness_score_10`.

Citations come from the runner's retrieved evidence inventory, with cached bytes and hashes. Source availability does not establish that every generated factual assertion or forecast is correct. Disclose unsupported measurements and scenario assumptions. In strict mode, missing required data or invented citation URLs cannot be repaired with plausible placeholders. Diagnostic lenient-mode reports explicitly warn about defaults and unverified claims.

## Machine Enforcement

`src/contracts.py` encodes the required artifacts, section coverage and scorecard
arithmetic above and is checked by the orchestrator (`validate_contract`) and by
`ModelAgent` (with automatic repair retries). Keep this document in sync; the
drift test `tests/test_contracts.py::test_contract_docs_match_code` fails otherwise.

## Outputs to Registry

- `a7_envelope.json`
- `TICK_YYYY-MM-DD_report.md` (assembled)
- `TICK_YYYY-MM-DD_artifacts.json` (aggregate)
- `TICK_YYYY-MM-DD_provenance.log` (finalized hash chain)
