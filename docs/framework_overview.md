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

1. `TICK_YYYY-MM-DD_report.md` – consolidated sections 0–18.
2. `TICK_YYYY-MM-DD_artifacts.json` – flattened agent envelopes + artifact registry entries.
3. `TICK_YYYY-MM-DD_provenance.log` – append-only, hash-linked log of every material claim.

## Controls

- **Immutability**: Agents cannot mutate prior output because envelopes are stored read-only and verified via SHA-256.
- **Depth Modes**: Quick/Standard/Deep definitions live in `config/depth_modes.yaml` and control lookbacks, optional sections, and metric coverage.
- **Focus Routing**: Optional focus tags (e.g., `AI strategy`, `Regulatory`) branch prompts using `config/focus_routing.yaml`.
- **Retry Discipline**: The orchestrator supports up to three retries on soft validation failures before halting with a flagged status.

For a ground-truth description of prompt requirements and expected artifacts, reference the contract files under `docs/contracts/`. For manual execution instructions, see `docs/runbooks/controller_mode.md`.
