# Research-output safety fixes

## Implemented and regression-tested

- Reject malformed outer JSON instead of recovering an inner fragment. Reject duplicate keys/non-finite JSON values; safe trailing-comma compatibility does not modify string contents.
- Preserve known A7 alternative layouts (root comparison_fields, section scorecard/SWOT/glossary) without substituting supplied financial values. Derive weighting arithmetic only from provided category scores/run weights.
- Send canonical per-agent schemas in prompts and native Gemini responseJsonSchema requests. Supply accepted upstream artifacts in full.
- In strict mode, require an explicit model-authored gate and complete required artifacts. No guessed measurement values; missing-data gates preserve nulls and halt.
- Retrieve explicit public HTTPS documents separately from model generation. Cache original bytes/text with actual retrieval timestamps and hashes; verify and snapshot those documents per run.
- Source records are runner-owned. Unknown model citation URLs fail strict mode. Missing evidence is explicitly unverified, never a generated SEC access claim.
- Real-provider `run` commands default to strict mode. `--allow-defaults` is deliberate legacy diagnostic mode, with report warnings and recorded repairs.
- Harden malformed scorecard validation so it returns errors rather than crashing.
- Ignore local credentials and secret-bearing agent runtime transcripts in Git; local .env permissions remain 600.

## Verification

- Full offline suite: **141 passed, 3 live tests deselected**.
- Includes a nine-agent source-backed strict replay, artifact-preservation regressions and detection of post-run evidence-cache tampering.
- Compilation and git diff whitespace checks passed.
- Two read-only Luna 6/MAX delegates inspected envelope handling and evidence integration. No delegate was permitted to read credential files.

## Live Gemini result

Using the configured key and `gemini-3.8-flash`, a native-schema strict run made **one API call**, completed its A1 check in **21.35 seconds**, and returned an explicit missing-data gate. It injected **zero defaults** and attached actual cached-document records rather than model-invented access metadata.

Missing inputs:

1. AAPL market price dated 2025-01-31.
2. Official basic/diluted share quantities from full filing statements.
3. Full income/balance-sheet/cash-flow tables, rather than earnings-release summaries.

This is a successful check of fail-closed behavior, **not a completed financial analysis**. Strict contract/report-integrity checks do not pass for the halted diagnostic run: required numeric measurements are intentionally null, and no assembled report/bundle exists. The halted A1 diagnostic envelope is unsealed. The cached evidence itself remains hash-checked.

## Remaining work

Add complete, date-appropriate primary filings/statements and market data, plus the governance, peer and news inputs required by later agents. Then repeat the full strict live pipeline. Retrieval hashes establish what was supplied, not independent factual truth or forecast accuracy.

Evidence: `strict_recheck.json`, `a1_envelope.json`, `AAPL_2025-01-31_missing_data.md`, `evidence/evidence.json`, `run_summary.json`, and the provenance log. The earlier lenient report remains unsafe and has not been rewritten to appear validated.
