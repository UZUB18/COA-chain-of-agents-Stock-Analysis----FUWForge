# Framework Overview

This document translates the COA v2.1 blueprint into tangible system components. It explains how FUWForge (Fired Up Wealth) coordinates its contract-bound agents, which artifacts flow between them, and how auditability is enforced end-to-end.

## Operating Model

- **Controller**: `FUWForgeOrchestrator` (see `src/orchestrator.py`) drives the phased flow.
- **Agents**: Seven primary agents (A1–A7) plus Bull/Bear variants of A6. Each agent consumes a frozen context, runs its contract, and outputs a schema-validated envelope.
- **Artifacts**: Every envelope is immutable and checksummed. The orchestrator only advances once hard gates pass (checksum echo, required artifacts, depth compliance).
- **Storage**: Results are written to the artifact registry (`run_id`-scoped directory) and chained through the provenance log (`TICK_YYYY-MM-DD_provenance.log`).

## Execution Phases

| Phase | Agents | Purpose | Hard Gate Highlights |
|-------|--------|---------|----------------------|
| Phase 1 | A1 | Harvest, reconcile, and freeze context | Schema v2.1 pass, data checksum, price timestamp freeze |
| Phase 2 | A2–A5 (parallel) | Domain deep dives (business, quant, governance, news) | Envelope validation, required artifacts, checksum match |
| Phase 3 | A6B, A6R, A6 | Adversarial synthesis and scenario modeling | Danger-Zone table, bull/bear loop, catalysts & headwinds |
| Phase 4 | A7 | Final assembly and packaging | Scorecard math, ToC, glossary, lay summary (depth dependent) |

## Artifacts Per Run

1. `TICK_YYYY-MM-DD_report.md` – consolidated sections 0–18 with a YAML header (run ID, ticker, date, depth, checksum).
2. `TICK_YYYY-MM-DD_artifacts.json` – flattened agent envelopes + artifact registry entries.
3. `TICK_YYYY-MM-DD_provenance.log` – append-only, hash-linked log of every material claim.
4. `run_summary.json` – status, params, measured metrics, checksum, halt reason (written even when a run halts).

## Controls

- **Machine-checked contracts**: `src/contracts.py` encodes required artifacts, structural shapes, section coverage, scorecard arithmetic and bull/bear linkage. `ModelAgent` self-validates and retries with the exact violations before the orchestrator halts the run.
- **Integrity**: Each envelope carries a `content_checksum` (SHA-256 over sections/artifacts/sources/lineage) and an `upstream` list of consumed envelope checksums. `python -m src.controller verify-run <run_dir>` recomputes everything and re-verifies the provenance chain; `seal` adds checksums to controller-mode envelopes.
- **Honest normalization**: Injected placeholder defaults are recorded in `metrics.injected_defaults`, surfaced as warnings, and reduce `data_completeness_score_10`. `--strict` / `FUWFORGE_STRICT=1` makes them fatal.
- **Depth Modes**: `config/depth_modes.yaml` defines lookbacks (fed to prompts) and `skip_sections`; `require_lay_summary` is enforced by the A7 section contract.
- **Focus Routing**: Optional focus tags (e.g., `AI strategy`, `Regulatory`) branch prompts using `config/focus_routing.yaml`.
- **Retry Discipline**: `config/orchestrator.yaml` sets `max_retries` and `retry_backoff_seconds`; model agents retry with the validation error appended, then the run halts with diagnostics.
- **Measured metrics**: the orchestrator times wall-clock latency and counts attempts itself instead of trusting model-reported numbers (token counts remain model-reported in envelope metrics).

For a ground-truth description of prompt requirements and expected artifacts, reference the contract files under `docs/contracts/`. For manual execution instructions, see `docs/runbooks/controller_mode.md`.
