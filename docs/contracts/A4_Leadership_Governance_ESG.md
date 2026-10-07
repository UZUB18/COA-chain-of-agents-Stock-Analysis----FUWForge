# Contract — A4 Leadership, Governance, Culture & ESG

## Mission

### Explicit unavailable-data exception

Required keys remain present. A literal null is allowed only for paths listed for this agent in `src/data_gaps.py`, with a non-empty, exact field/reason entry in `metrics.missing_data` (canonical provider format: `[{"field": "artifact.path", "reason": "Evidence reviewed and why this measurement is unavailable"}]`). The runner renders a data-gap table; these reasons are included in the content checksum. This does not excuse invented citations, missing required keys, malformed non-null values or unknown critical A1 measurements. Null means unavailable—not zero, neutral, flat or no events. Sentiment is unavailable as a whole object, never partially null fractions. An unavailable Glassdoor score requires `LowSampleSizeCultureData=true`; an unavailable Beneish score requires `feasible=false` and an explanation. Reports with such gaps explicitly disclose incomplete research coverage.

Assess leadership effectiveness, governance quality, cultural signals, and ESG trajectory using filings, proxy statements, and reputable third-party data.

## Required Artifacts

```json
{
  "CEO_Pay_Ratio": 0.0,
  "Auditor": "Firm Name",
  "AuditorTenure": "0 years",
  "GlassdoorScore": 0.0,
  "AttritionNote": "Text with data point or 'Low sample size'",
  "ESG_Momentum": "Improving|Stable|Deteriorating|Unclear",
  "LowSampleSizeCultureData": true|false
}
```

## Sections Markdown

- `sections_md.SECTION_6_LEADERSHIP_GOVERNANCE`: board composition, independence, committee structure, compensation alignment, auditor details.
- `sections_md.SECTION_7_CULTURE_ESG`: employee sentiment, attrition, ESG goals, controversies, third-party ratings.

## Rules & Checks

- Anchor all assessments in quantitative or sourced evidence (e.g., TSR vs CEO pay changes).
- Highlight governance risks such as dual-class shares, related-party transactions, auditor changes.
- If culture data is sparse, set `LowSampleSizeCultureData=true` and include warning.
- ESG momentum must reference measurable indicators (e.g., emissions intensity, ISS rating trend).
- Echo checksum from A1.

## Outputs to Registry

- `a4_envelope.json`
- `leadership_snapshot.md`
- Append familiarity ratings or conflict logs to provenance when red flags exist.
