# Contract — A6 Master Synthesiser & Adversarial Loop

## Mission
Facilitate a deliberate bull vs bear debate built on artifacts from A2–A5, then author a balanced CIO synthesis covering risk dashboards, scenarios, catalysts, and headwinds.

## Agents

- **A6B (Bull)**: Produce 3–5 quantified positives citing upstream artifacts (enforced: 3–5 `BullCase` entries, each with an id, thesis and at least one evidence citation).
- **A6R (Bear)**: Challenge each bull point with probability and severity (FUW Danger-Zone mindset). Enforced: every `BullCase` id has at least one rebuttal and no rebuttal cites an unknown id (in the experimental independent pass, local `H*` ids are allowed).
- **A6 (CIO)**: Resolve the debate, flag risks, and deliver 3-year scenarios with key drivers.

## Required Artifacts

```json
{
  "BullCase": [
    {"id": "B1", "thesis": "...", "evidence": ["A2.PEER_LIST", "A3.AltmanZ"], "impact": "EPS +5% FY+2"}
  ],
  "BearRebuttals": [
    {"bull_id": "B1", "probability": "High|Med|Low", "severity": "High|Med|Low", "counter": "..."}
  ],
  "DangerZoneDashboard": [
    {"category": "Accounting anomalies", "flag": "🟢|🟡|🔴", "sources": ["A3.OCF_vs_NI_Flag"], "note": "..."}
  ],
  "ScenarioTable": {
    "Bull": {"Revenue": "...", "EPS": "...", "FCFPerShare": "...", "Drivers": "..."},
    "Base": {...},
    "Bear": {...}
  },
  "Catalysts": ["..."],
  "Headwinds": ["..."]
}
```

## Sections Markdown

- `sections_md.SECTION_10_BULL_CASE`
- `sections_md.SECTION_11_BEAR_CASE`
- `sections_md.SECTION_14_SYNTHESIS` (Danger-Zone dashboard, scenarios, catalysts/headwinds, CIO view)

## Rules & Checks

- Each bull point must reference at least one upstream artifact.
- Bear rebuttals must map 1:1 with bull IDs and quantify risk (probability, severity, expected impact).
- Danger-Zone dashboard should cover accounting, concentration, legal/regulatory, hype, and shock buffer. Enforced: a non-empty list whose entries each have category, flag and note strings; the category set is guidance, not checked.
- Scenario table should span three fiscal years beyond the analysis date with numeric drivers. Enforced: Bull/Base/Bear objects each with Revenue and EPS; FCF per share, the horizon and driver detail are guidance, not checked.
- Catalysts/headwinds grouped by time horizon (short/medium/long) when possible.
- Echo checksum from A1.

## Outputs to Registry

- `a6b_envelope.json`, `a6r_envelope.json`, `a6_envelope.json`
- `danger_zone_dashboard.md`
- Provenance entries linking bull/bear claims back to source artifacts.
