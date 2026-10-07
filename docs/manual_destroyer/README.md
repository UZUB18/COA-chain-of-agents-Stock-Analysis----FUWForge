# Destroyer Mode (Manual Orchestrator Playbook)

> **Legacy bundle.** The canonical prompt source for automated runs is `src/agents/model.py`
> (validated against `docs/contracts/`), and the primary manual workflow lives in
> `docs/i_am_become_the_orchestrator/`. This folder is kept for reference; prefer those
> sources and treat the prompts here as a condensed copy that may lag the contracts.

Welcome to the “Destroyer of Stocks” cockpit. This folder gives you a streamlined, fully manual workflow so you can run the FUWForge stack without juggling half a dozen contracts. Each agent prompt is condensed, copy-ready, and annotated with the exact artifacts you must fill. The goal: minimum copy/paste fatigue, maximum audit-grade output.

## Quickstart

1. **Prep a Run Card**
   - Duplicate `templates/inputs_blank.yaml`.
   - Give the run a nickname (e.g., `RUN-2025-02-StockForge`).
   - Paste the frozen context section from A1 into the top of your Run Card after you complete the gate.

2. **Fire the Agents (A1 → A7)**
   - Open `prompts.md` in this folder.
   - For each agent, copy the `Prompt >>>` block, replace the placeholders (`{{ticker}}`, `{{checksum}}`, etc.), and send it to your model of choice.
   - Paste the JSON response into your Run Card under the agent’s heading.

3. **Validate on the Fly**
   - After each agent, skim the “Fast Checks” bullets in `prompts.md`. If any required artifact is missing, ask the model to patch it before moving on.
   - Once the JSON looks clean, run `python -m src.controller validate path/to/agent.json --expected-checksum {{checksum}}` if you want strict schema enforcement.

4. **Assemble the Report**
   - When all agents are locked, run `python -m src.controller assemble <run_dir> --run-id <id> --company <name> --ticker <ticker> --analysis-date <date> --depth <mode>`.
   - Or copy the markdown sections directly from the envelopes in the order listed under “Report Stack” below.

## Report Stack (Sections 0–18)

1. A7 (Final assembly shell: ToC, IPO, SWOT, scorecard, glossary, lay summary)
2. A6 (Danger-Zone dashboard, scenarios, catalysts/headwinds)
3. A6B (Bull case bullets)
4. A6R (Bear rebuttals)
5. A5 (News, sentiment, flows, litigation)
6. A4 (Leadership, governance, ESG)
7. A3 (Quant, valuation, technicals)
8. A2 (Business, market, peers, focus-callouts)
9. A1 (Raw data pack, frozen context)

## Tips for Destroyer-Level Flow

- **One Notebook, One Run**: Keep all prompts and outputs in the same document to avoid losing context.
- **Checksum Discipline**: After A1, highlight the checksum and paste it into every subsequent prompt.
- **Focus Tags**: If your run card includes focus areas (AI strategy, regulatory, etc.), toggle the relevant mini-blocks in the prompts.
- **Timeout Rule**: If an agent fails twice in a row, park the run, fix the source data, and rerun A1. Better to pause than publish bad data.

Check `prompts.md` beside this file whenever you need a quick copy/paste snippet. The Destroyer approves.
