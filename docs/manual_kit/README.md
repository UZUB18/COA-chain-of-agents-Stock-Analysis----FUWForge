# FUWForge Manual Kit (CLI-Only Edition)

This kit lets you run the entire FUWForge pipeline by hand using nothing more than your favourite LLM CLI (OpenAI, Gemini, Claude, Perplexity, etc.). No Python runtime, no dependencies—just disciplined copy/paste and good taste in filings.

## Kit Contents

- `run_card_template.md` — Single-page control sheet where you paste inputs, agent prompts, and JSON responses.
- `prompts_core.md` — Concise prompt blocks for A1–A7 with placeholder variables.
- `quick_checklist.md` — One-page validation list to keep every gate honest.
- `danger_zone_stub.md` — Markdown scaffold for the A6 danger-zone dashboard if you want to write it manually.

## Workflow (You = Orchestrator)

1. **Clone the kit** into a clean workspace or cloud drive so you can duplicate it per run.
2. **Fill the Run Card**
   - Copy `run_card_template.md` to a new file named `RUN-<ticker>-<date>.md`.
   - Populate the intro block (company, ticker, analysis date, depth, focus tags).
3. **Run Agents A1 → A7**
   - For each agent, grab the matching prompt from `prompts_core.md`.
   - Replace placeholders (`{{ticker}}`, `{{checksum}}`, etc.) and send it to your LLM CLI.
   - Paste the raw JSON response into the Run Card under that agent’s heading.
   - Use `quick_checklist.md` after every agent to make sure required artifacts are present before moving on.
4. **Freeze + Echo Checksum**
   - After A1, highlight the emitted `metrics.data_checksum`.
   - Paste that value into every downstream prompt to maintain immutability.
5. **Assemble the Report**
   - Copy the markdown sections from each agent’s envelope (listed under `sections_md`) into a final document in the canonical order (0–18).
   - Use `danger_zone_stub.md` if A6 needs manual tweaks.
6. **Archive the Run**
   - Save your Run Card, final report, and raw JSON envelopes together for audit trail.

## Tips for Smooth Manual Runs

- **LLM CLI Choice**: Use deterministic temperature (0–0.2) and enable JSON mode if available.
- **Retries**: If an agent misses required artifacts, resend the same prompt with “Please fix missing field X”.
- **Focus Tags**: Copy/paste the optional focus lines in prompts whenever your run demands extra coverage (AI strategy, regulatory, valuation, etc.).
- **Sentiment & Price Data**: If your LLM doesn’t have live browsing, collect the data manually beforehand and feed it in the prompt context.
- **Logging**: Note the timestamp of each agent execution in the Run Card for provenance.

With this kit you are officially the Destroyer-in-Chief—wield it responsibly.
