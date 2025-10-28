# Contract — A4 Leadership, Governance, Culture & ESG

## Mission
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
