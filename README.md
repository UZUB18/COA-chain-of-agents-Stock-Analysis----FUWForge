# FUWForge (Fired Up Wealth) on COA v2.1

FUWForge (Fired Up Wealth) wraps the COA v2.1 research playbook in a deterministic, contract-driven stack. Seven agents (A1–A7) march through gated phases, swap checksummed envelopes, and forge a final report that survives audit-grade scrutiny. The name nods to heat and pressure—facts → ideas → numbers—so nothing soft slips through.

## Repository Layout

```
.
├── config/                 # Orchestration, depth, and routing settings
├── docs/                   # Contracts, runbooks, manuals, architecture notes
│   ├── contracts/          # Detailed agent requirements (A1–A7)
│   ├── manual_destroyer/   # “Destroyer Mode” copy-ready prompts for full manual runs
│   ├── i_am_become_the_orchestrator/  # CLI-only kit for hands-on orchestrators
│   └── runbooks/           # Controller-mode SOPs and checklists
├── schemas/                # Machine-validated envelope schema v2.1
├── src/                    # Python scaffolding (orchestrator, controller CLI, utilities)
├── templates/              # Intake forms, markdown shells, tables
└── README.md
```

## Key Components

- `config/depth_modes.yaml` — Quick/Standard/Deep definitions (lookbacks, optional sections).
- `config/orchestrator.yaml` — Declarative phase flow and validation gates.
- `schemas/envelope_v2_1.json` — Unified envelope schema all agents must honor.
- `src/orchestrator.py` — `FUWForgeOrchestrator`, a deterministic runner with checksum enforcement.
- `src/controller.py` — CLI helpers for manual/controller mode validation & assembly.
- `docs/contracts/` — Agent-by-agent contracts (artifacts, markdown expectations, guardrails).
- `docs/manual_destroyer/` — Condensed prompts to operate as the “Destroyer of Stocks” without juggling multiple files.
- `docs/i_am_become_the_orchestrator/` — Entirely manual, CLI-only bundle (run card, prompts, checklist, danger-zone stub).
- `templates/` — Ready-made input forms, Missing Data Table, Danger-Zone dashboard, scorecard shell.

## Getting Started

1. Copy `templates/inputs_blank.yaml`, fill company/ticker/date/depth/focus.
2. Run A1 through A7 via either automated mode (wire agents into `FUWForgeOrchestrator`), Destroyer mode (`docs/manual_destroyer/prompts.md`), or the CLI-only kit (`docs/i_am_become_the_orchestrator/prompts_core.md`).
3. After each agent, validate the JSON envelope with `python -m src.controller validate <file> --expected-checksum <A1_checksum>`.
4. Assemble the final report with `python -m src.controller assemble <run_dir> --run-id ... --company ... --ticker ... --analysis-date ... --depth ...`.
5. Deliverables land as `<TICK>_<DATE>_report.md`, `<TICK>_<DATE>_artifacts.json`, and the hash-chained provenance log.

## Manual “Destroyer Mode”

If you want to be the orchestrator yourself, jump into `docs/manual_destroyer/`. It includes:

- A runbook tailored for “Destroyer of Stocks” energy.
- Copy/paste prompt blocks with placeholders for ticker, checksum, focus tags.
- Fast-check bullets so you never forget a required artifact before moving on.

Pair it with the controller CLI (`python -m src.controller`) to validate envelopes without writing custom scripts.

## Inspiration & Gratitude

Let’s be honest: almost every clever trick in FUWForge is borrowed from Eric and the Fired Up Wealth community on YouTube, Patreon, and Discord. He’s the reason this stack obsesses over filings-first discipline, adversarial loops, and quant-backed synthesis. Think of this repo as a heated love letter to the “facts → ideas → numbers” playbook Eric beats into our heads—with extra checkpoints for those of us who like to double- and triple-check the math.
