# Live Gemini Flash 3.8 check

## Result

The direct Google API and normal-mode pipeline work. This output is **not strict-ready or verified investment research**.

- Model: `gemini-3.8-flash`, using the configured local API key.
- Connectivity: passed, valid expected JSON, 2.663 seconds.
- Analysis: bundled Apple example, historical analysis date 2025-01-31, Quick mode.
- All nine agents completed; report assembly succeeded.
- Analysis duration: 164.26 seconds.
- API calls: 10 for analysis plus one connectivity probe. A6 needed one retry.
- Schema/content-checksum/lineage/provenance audit: passed without warnings.
- Normal contracts: passed. Strict contracts: failed for A6 and A7.
- API billing amounts and actual token usage were not measured.

## Quality blockers

1. **A6 malformed JSON and partial-object recovery.** A6's final raw response is invalid JSON at line 57. The extractor recovered an inner object with only `Drivers`, `EPS`, `FCFPerShare`, and `Revenue`, rather than the intended envelope. The normalizer then injected the CIO synthesis section, ScenarioTable, DangerZoneDashboard, Catalysts and Headwinds. This is a framework parsing/normalization failure masked by permissive defaults, not evidence of a valid original A6 envelope.
2. **A7 schema mismatch.** The model placed comparison_fields at the top level and structured scorecard content under sections rather than canonical artifacts. The normalizer replaced Scorecard, SWOT, Glossary and comparison_fields with defaults instead of preserving the full model output. For example, raw ROIC was 56.2%, while final comparison_fields ROIC became the runner default of 22.5%. Neither value was independently fact-verified.
3. **Ungrounded sources.** The agents have no live filing/price retrieval tool. The accepted envelopes contain a generic https://sec.gov citation, with runner-generated access timestamps, not evidence of downloaded filings. A1's raw response omitted sources entirely, and this source injection is not recorded as an injected default. Even a strict contract pass is therefore not sufficient evidence of source quality.

## Next steps

Provide the complete envelope schema to model prompts (or constrain generation with a supported JSON schema), reject invalid outer JSON instead of accepting nested fragments, preserve valid alternative-format artifacts only through explicit mappings, and require document-specific evidence supplied by real retrieval. Then repeat strict-mode analysis before trusting scores or comparing provider quality.

## Evidence

- `connectivity.json`
- `analysis/live_check.json`
- `analysis/live_audit.json`
- `analysis/run_summary.json`
- `analysis/a6_raw.json`, `analysis/a7_raw.json`
- `analysis/AAPL_2025-01-31_report.md`
