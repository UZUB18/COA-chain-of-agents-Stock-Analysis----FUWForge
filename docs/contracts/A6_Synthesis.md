# Contract — A6 Master Synthesiser & Adversarial Loop

## Mission
Facilitate a deliberate bull vs bear debate built on artifacts from A2–A5, then author a balanced CIO synthesis covering risk dashboards, scenarios, catalysts, and headwinds.

## Agents

- **A6B (Bull)**: Produce 3–5 quantified positives citing upstream artifacts.
- **A6R (Bear)**: Challenge each bull point with probability and severity (FUW Danger-Zone mindset).
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
- Danger-Zone dashboard covers accounting, concentration, legal/regulatory, hype, and shock buffer.
- Scenario table spans three fiscal years beyond the analysis date and states numeric drivers.
- Catalysts/headwinds grouped by time horizon (short/medium/long) when possible.
- Echo checksum from A1.

## Outputs to Registry

- `a6b_envelope.json`, `a6r_envelope.json`, `a6_envelope.json`
- `danger_zone_dashboard.md`
- Provenance entries linking bull/bear claims back to source artifacts.
