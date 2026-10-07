# Orchestration Flow (Declarative Spec)

The orchestrator executes the COA v2.1 phases deterministically. The phase sequence is implemented in `src/orchestrator.py`; `config/orchestrator.yaml` only holds run settings (`max_retries`, `retry_backoff_seconds`).

Every phase runs the same acceptance pipeline per agent: schema validation → required artifacts → hard gate → machine-checkable contract (`src/contracts.py`) → content checksum + upstream lineage → persist → record measured metrics. Failures halt the run and still write `run_summary.json`.

## Phase 1 — GOD Gate (A1)

1. Invoke A1 with intake parameters and frozen context instructions.
2. Validate against `schemas/envelope_v2_1.json` and the A1 contract (market-cap reconciliation ±5%, share counts, raw data pack).
3. If `gates.passed` is false, halt and emit the Missing Data Table report.
4. If pass, record `metrics.data_checksum`, seal the envelope with `content_checksum`, and append the frozen context to the provenance log.

## Phase 2 — Parallel Core Analysis (A2–A5)

1. Fan out to A2, A3, A4, A5 using the frozen context and checksum.
2. After each agent completes (validated in canonical order for determinism):
   - Validate schema compliance.
   - Ensure all required artifacts are present and structurally valid.
   - Confirm the echoed checksum matches A1.
   - Verify section coverage for the agent's assigned sections.
3. Model agents retry up to `run_settings.max_retries` with the validation error appended; on persistent failure the run halts with diagnostics.

## Phase 3 — Adversarial Synthesis (A6B, A6R, A6)

1. Run A6B (Bull) using artifacts from A2–A5; each thesis must cite at least one upstream artifact.
2. Run A6R (Bear) with explicit instructions to challenge the bull case; each rebuttal must map to a bull ID.
3. Run A6 (CIO) to reconcile both views, fill the Danger-Zone dashboard, and author the 3-year scenario table.
4. Validate the presence and structural integrity of:
   - `DangerZoneDashboard` (non-empty, flagged rows)
   - `ScenarioTable` (Bull/Base/Bear with Revenue and EPS)
   - `Catalysts` and `Headwinds` lists
   - Bull/bear ID linkage

## Phase 4 — Final Assembly (A7)

1. Assemble sections 0–18, preserving order and markdown fidelity; conflicting duplicate section keys abort assembly.
2. Confirm scorecard arithmetic (raw × weight = weighted, weights sum to 1.0, overall = sum of weighted).
3. Validate A7 sections: ToC/exec summary, IPO history, SWOT, scorecard, glossary, and the lay summary unless the depth mode skips it (`skip_sections` / `require_lay_summary`).
4. Write the report (with run metadata header), artifacts bundle, provenance log, and `run_summary.json`.

## Mode Differences

- **Automated mode**: `FUWForgeOrchestrator` invokes agents via API or model client, captures outputs, and performs validation and sealing automatically.
- **Controller mode**: Operators run prompts manually and paste envelopes into the run directory; the orchestrator consumes local JSON files instead of direct agent responses. Run `python -m src.controller seal <run_dir>` to add content checksums, then `verify-run` to audit.

Consult `docs/runbooks/controller_mode.md` for the hands-on SOP.
