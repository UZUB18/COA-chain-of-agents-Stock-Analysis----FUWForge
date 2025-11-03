```
  _______ _    _ _      ______                             
 |  ____| |  | | |    |  ____|                            
 | |__  | |  | | |    | |__ ___  _   _ _ __   __ _  ___   
 |  __| | |  | | |    |  __/ _ \| | | | '_ \ / _` |/ _ \  
 | |____| |__| | |____| | | (_) | |_| | | | | (_| | (_) | 
 |______|\____/|______|_|  \___/ \__,_|_| |_|\__, |\___/  
                                             __/ |       
                                            |___/        
```

# FUWForge Admissions Packet

**Bring Fired Up Wealth’s COA v2.1 research engine into your shop—scripted, audited, and ready for the heat.**

FUWForge packages seven contract-bound agents (A1–A7) into a deterministic pipeline. Every phase writes to a JSON envelope, seals it with a checksum, and refuses to advance until the next agent signs off. The result is a filings-first research process that survives adversarial review without sacrificing speed.

## Why teams pick FUWForge

- Filings-first muscle memory baked into every agent contract.
- Deterministic envelopes and checksum gates keep artifacts tamper-evident.
- Dual operating modes: wire an orchestrator, or run it manually with nothing more than an LLM CLI.
- Templates and runbooks that make onboarding new analysts feel like admissions, not archaeology.

## What ships in this repo

- `config/` — Depth modes, orchestrator wiring, routing rules.
- `docs/` — Contracts, runbooks, manual kits, architecture notes.
- `schemas/` — `envelope_v2_1.json` ensures every artifact conforms.
- `src/` — Orchestrator, controller CLI, helper utilities.
- `templates/` — Intake forms, scorecards, danger-zone dashboards, missing data tables.

## Operating modes

### Deterministic orchestrator
1. Copy `templates/inputs_blank.yaml`, fill ticker, focus, analysis date, and depth.
2. Feed the config into `FUWForgeOrchestrator` (`src/orchestrator.py`) wired to your agent endpoints.
3. Use `python -m src.controller validate <envelope>` to confirm checksums before promotion.
4. Assemble the run with `python -m src.controller assemble <run_dir> --run-id <id> --ticker <TICK> --analysis-date <YYYY-MM-DD> --depth <mode>`.

<<<<<<< HEAD
Let’s be honest: almost every clever trick in FUWForge is borrowed from Eric and the Fired Up Wealth community on YouTube, Patreon, and Discord. He’s the reason this stack obsesses over filings-first discipline, adversarial loops, and quant-backed synthesis. Think of this repo as a heated love letter to the “facts → ideas → numbers” playbook Eric beats into our heads—with extra checkpoints for those of us who like to double- and triple-check the math. ---->>> patreon.com/firedupwealth // https://www.youtube.com/@FiredUpWealth // twitter.com/FIREDUpWealth
=======
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
>>>>>>> b69abf9 (Add ASCII banner to README)
