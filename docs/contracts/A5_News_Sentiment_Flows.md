# Contract — A5 News, Sentiment, Insider & Institutional Flows

## Mission

### Explicit unavailable-data exception

Required keys remain present. A literal null is allowed only for paths listed for this agent in `src/data_gaps.py`, with a non-empty, exact field/reason entry in `metrics.missing_data` (canonical provider format: `[{"field": "artifact.path", "reason": "Evidence reviewed and why this measurement is unavailable"}]`). The runner renders a data-gap table; these reasons are included in the content checksum. This does not excuse invented citations, missing required keys, malformed non-null values or unknown critical A1 measurements. Null means unavailable—not zero, neutral, flat or no events. Sentiment is unavailable as a whole object, never partially null fractions. An unavailable Glassdoor score requires `LowSampleSizeCultureData=true`; an unavailable Beneish score requires `feasible=false` and an explanation. Reports with such gaps explicitly disclose incomplete research coverage.

Synthesize the company’s recent qualitative signals: headline flow, sentiment trends, litigation/regulatory context, insider transactions, and institutional positioning.

## Required Artifacts

```json
{
  "SentimentSplit": {"pos": 0.0, "neu": 0.0, "neg": 0.0, "lookback_days": 90},
  "SocialVsFundDivergence": "Aligned|Mild|Pump Risk",
  "MajorLitigation": [
    {"case": "...", "stage": "...", "materiality": "...", "source": "..."}
  ],
  "AuditorChangeYN": "Yes|No",
  "InsiderNet12m": {"shares": 0, "value": 0.0, "direction": "NetBuy|NetSell|Neutral"},
  "InstitutionalOwnershipTrend": {"direction": "Up|Down|Flat", "delta_pct": 0.0},
  "RegulatoryWatchlist": ["..."]
}
```

## Sections Markdown

- `sections_md.SECTION_8_NEWS_SENTIMENT`: top headlines, earnings transcripts, sentiment analytics, divergence commentary.
- `sections_md.SECTION_9_FLOW_LITIGATION`: insider/13F trends, litigation summaries, regulatory/KYC notes.

## Rules & Checks

- Include headline time windows (e.g., “Lookback: 180 days, refreshed 2025-10-28”).
- Quantify sentiment coverage (article count, source diversity) and identify blind spots.
- Reconcile insider data with Form 4 filings; highlight if activity is clustered.
- Report institutional flow with context (e.g., BlackRock +0.8% holdings QoQ).
- Echo checksum from A1 and cite every data source with URLs and access timestamps.

## Outputs to Registry

- `a5_envelope.json`
- `sentiment_breakdown.csv` (if structured data captured)
- Provenance updates for each litigation/regulatory entry.
