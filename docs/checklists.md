# Quality Control Checklists

These checklists operationalize the hard gates and MEAF/FUW controls described in the framework blueprint. Use them during manual review or bake them into automated validation.

## A1 Gate Pass Checklist

- [ ] 10-K, 10-Q, DEF14A, and material 8-K set present and dated within policy.
- [ ] Single price timestamp recorded; `shares_basic` and `shares_diluted` provided.
- [ ] 5–10 year Income Statement, Balance Sheet, and Cash Flow series complete.
- [ ] Segment and geography totals reconcile to reported revenue.
- [ ] Altman Z and other solvency inputs collected.
- [ ] Shares × price ≈ market cap (±5% tolerance).
- [ ] `sections_md.RAW_DATA_PACK` rendered with units, as-of dates, and formulas.
- [ ] `metrics.data_checksum` generated and stored.

## A2–A5 Envelope Checklist

- [ ] Schema v2.1 validation succeeded.
- [ ] `metrics.data_checksum` matches A1 frozen checksum.
- [ ] Required artifacts for the agent populated (see `docs/contracts/`).
- [ ] Depth-mode coverage satisfied (lookback windows, optional sections).
- [ ] Source citations include type, URL, document date, and accessed timestamp.

## A6 Synthesis Checklist

- [ ] Bull points quantified and cite upstream artifacts.
- [ ] Bear points rebut bull points directly, with probability and severity.
- [ ] Danger-Zone Dashboard populated with flags and MEAF-friendly notes.
- [ ] 3-year scenario table (Bull/Base/Bear) with key drivers.
- [ ] Catalysts and headwinds enumerated with quantitative hooks.

## A7 Final Assembly Checklist

- [ ] Table of contents present; IPO block filled or marked N/A.
- [ ] Balanced SWOT plus watchlist items.
- [ ] Scorecard shows weight math and overall rating band.
- [ ] Glossary populated with defined terms.
- [ ] Lay summary present for Standard/Deep depth modes.

## Provenance & Metrics Checklist

- [ ] Append-only provenance log updated after every agent.
- [ ] Hash chain verified against prior entry.
- [ ] Latency, token counts, retries recorded per agent.
- [ ] Confidence scores assigned per agent with consistent rubric.
