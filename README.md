```
 ███████╗██╗   ██╗██╗    ██╗    ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
 ██╔════╝██║   ██║██║    ██║    ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
 █████╗  ██║   ██║██║ █╗ ██║    █████╗  ██║   ██║██████╔╝██║  ███╗█████╗  
 ██╔══╝  ██║   ██║██║███╗██║    ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  
 ██║     ╚██████╔╝╚███╔███╔╝    ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
 ╚═╝      ╚═════╝  ╚══╝╚══╝     ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

# Contract-Driven Multi-Agent Research Pipeline

**A deterministic, adversarial-tested engine for equity analysis that survives audit without sacrificing throughput.**

FUWForge implements a seven-agent pipeline (A1–A7) where each stage operates under explicit contract, writes to cryptographically-checksummed JSON envelopes, and refuses to proceed until the next agent validates its input. The result: financial research that's both repeatable and defensible—no hand-waving, no black boxes.

Built on the filings-first methodology from Fired Up Wealth's COA framework, adapted for academic rigor and production deployment.

---

## Architecture at a glance

```
[Intake] → A1 → A2 → A3 → A4 → A5 → A6 → A7 → [Report]
           ▼    ▼    ▼    ▼    ▼    ▼    ▼
         [Envelope checksum gates at every transition]
```

**Seven agents. Seven contracts. One unbroken chain of provenance.**

| Agent | Domain | Output Artifact |
|-------|--------|-----------------|
| **A1** | Source acquisition & validation | Filing inventory, data integrity checksums |
| **A2** | Market structure & competitive analysis | Peer matrices, market positioning |
| **A3** | Quantitative valuation | DCF models, multiples, technical indicators |
| **A4** | Thesis synthesis | Investment memo spine, narrative scaffolding |
| **A5** | Scenario modeling | Probability-weighted cases (bull/base/bear) |
| **A6** | Risk interrogation | Red-flag taxonomy, adversarial stress tests |
| **A7** | Report assembly | Investor-ready document + audit trail |

---

## Why FUWForge exists

Most research pipelines are academic thought experiments or black-box SaaS tools. FUWForge is neither:

- **Deterministic by design**: Same inputs, same outputs. Every time.
- **Adversarially hardened**: A6 exists solely to attack the thesis built by A1–A5.
- **Filings-first discipline**: Primary sources over aggregated data; provenance over convenience.
- **Audit-native**: Every envelope carries its lineage. No "trust me" artifacts.
- **Dual-mode operation**: Run it automated via orchestrator or manually through CLI—same contracts, same validation.

This isn't a tool for generating content. It's a framework for *defending* analysis.

---

## What's in the box

```
fuwforge/
├── config/          # Depth modes, routing rules, orchestrator wiring
├── docs/            # Agent contracts, runbooks, manual operation guides
│   ├── manual_destroyer/           # Agent-by-agent manual prompts
│   └── i_am_become_the_orchestrator/  # CLI-only control mode
├── schemas/         # JSON Schema validation (envelope_v2_1.json)
├── src/             # Orchestrator, controller CLI, utilities
│   ├── orchestrator.py   # Automated multi-agent coordinator
│   └── controller.py     # CLI for validation, assembly, inspection
└── templates/       # Intake forms, scorecards, dashboards
    ├── inputs_blank.yaml
    ├── danger_zone_dashboard.md
    └── missing_data_table.md
```

---

## Operating modes

### Mode 1: Automated orchestrator

For production runs where you need throughput without sacrificing determinism.

1. Fill `templates/inputs_blank.yaml` with ticker, focus, analysis date, and depth mode.
2. Wire agent endpoints to `FUWForgeOrchestrator` in `src/orchestrator.py`.
3. Execute the run. Each agent writes its envelope, validates the checksum, and hands off.
4. Validate the chain: `python -m src.controller validate <envelope>`
5. Assemble the final report: `python -m src.controller assemble <run_dir> --run-id <id> --ticker <TICK> --analysis-date <YYYY-MM-DD> --depth <mode>`

### “Destroyer Mode” manual cockpit
1. Open `docs/manual_destroyer/prompts.md` (or the CLI-only bundle in `docs/i_am_become_the_orchestrator/`).
2. March through A1–A7 prompts, pasting responses into the provided markdown shells.
3. Validate each JSON envelope with the controller CLI to keep the chain unbroken.
4. Drop artifacts into the Danger-Zone dashboard (`templates/danger_zone_dashboard.md`) if you need a red-team snapshot.

## Agent roster (A1–A7)

| Agent | Mandate | Key Artifacts |
|-------|---------|---------------|
| A1 | Harvest filings + sanity-check source data | Raw doc inventory, intake checksum |
| A2 | Business, market, peer synthesis | Market map, competitor artifacts |
| A3 | Quant valuation + technicals | Valuation tables, technical checkpoints |
| A4 | Mosaic builder | Investment memo spine, thesis markers |
| A5 | Scenario lab | Upside/base/downside cases, KPIs |
| A6 | Danger-zone auditor | Risk dashboard, red/yellow flag log |
| A7 | Final assembler | Investor-ready report, provenance log |

## Quickstart checklist

1. Clone the repo and skim `docs/framework_overview.md` for the big picture.
2. Pick a depth mode in `config/depth_modes.yaml` and adjust if needed.
3. Fill out `templates/inputs_blank.yaml` with your run details.
4. Choose automated vs. manual mode and follow the matching playbook above.
5. Commit the assembled report, JSON artifacts, and provenance log to lock in the audit trail.

## Safety rails & provenance

- Each envelope version is validated against `schemas/envelope_v2_1.json`.
- Checksums are chained so tampering is obvious during review.
- Manual templates carry the same artifact IDs as automated runs for easy hand-offs.
- Danger-zone dashboards keep risk conversations tethered to documented evidence.

## Gratitude

This stack exists because of Eric and the Fired Up Wealth community—YouTube, Patreon, Discord. The filings-first discipline, adversarial loops, and quant-backed synthesis you see here are his fingerprints. FUWForge just adds the rails so teams can run the playbook at scale without losing the heat.
