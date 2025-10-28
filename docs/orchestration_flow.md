# Orchestration Flow (Declarative Spec)

The orchestrator executes the COA v2.1 phases deterministically. This specification mirrors `config/orchestrator.yaml` and documents the control logic for both automated and controller modes.

## Phase 1 — GOD Gate (A1)

1. Invoke A1 with intake parameters and frozen context instructions.
2. Validate against `schemas/envelope_v2_1.json`.
3. If `gates.passed` is false, halt and emit the Missing Data Table report.
4. If pass, record `metrics.data_checksum` and append the frozen context to the provenance log.

## Phase 2 — Parallel Core Analysis (A2–A5)

1. Fan out to A2, A3, A4, A5 using the frozen context and checksum.
2. After each agent completes:
   - Validate schema compliance.
   - Ensure all required artifacts are present.
   - Confirm the echoed checksum matches A1.
   - Verify depth-mode obligations (lookback spans, optional sections).
3. Retry soft failures up to the configured maximum (default 3). On persistent failure, abort run with diagnostic payload.

## Phase 3 — Adversarial Synthesis (A6B, A6R, A6)

1. Run A6B (Bull) using artifacts from A2–A5.
2. Run A6R (Bear) with explicit instructions to challenge the bull case.
3. Run A6 (CIO) to reconcile both views, fill the Danger-Zone dashboard, and author the 3-year scenario table.
4. Validate the presence and structural integrity of:
   - `Danger-Zone Dashboard`
   - Scenario table (Bull/Base/Bear)
   - Catalysts and headwinds lists

## Phase 4 — Final Assembly (A7)

1. Assemble sections 0–18, preserving order and markdown fidelity.
2. Recalculate the weighted scorecard and confirm math matches section totals.
3. Generate Table of Contents, glossary, and lay summary (if depth allows).
4. Package final artifacts and flush provenance/metrics logs.

## Mode Differences

- **Automated mode**: `FUWForgeOrchestrator` invokes agents via API or model client, captures outputs, and performs validation automatically.
- **Controller mode**: Operators run prompts manually and paste envelopes into the validator CLI (`src/controller.py` to be added). The orchestrator consumes local JSON files instead of direct agent responses.

Consult `docs/runbooks/controller_mode.md` for the hands-on SOP and `config/orchestrator.yaml` for the machine-readable variant.
