# “I Am Become the Orchestrator” Kit

This pocket guide lets you run FUWForge entirely by hand with nothing but an LLM CLI (OpenAI, Gemini, Claude, Perplexity—take your pick). No Python runtime, no dependencies, just you fusing filings into finished research like a benevolent destroyer of weak theses.

## What’s Inside

- `prompts_core.md` — Trimmed prompts for Agents A1–A7 with slot-in placeholders.
- `run_card_template.md` — Single sheet to capture inputs, agent outputs, and timestamps.
- `quick_checklist.md` — Bite-sized validation list so every gate stays honest.
- `danger_zone_stub.md` — Markdown scaffold if you want to rewrite the A6 dashboard manually.

## Flight Plan

1. **Duplicate the run card**  
   Copy `run_card_template.md` to `RUN-<ticker>-<date>.md`. Fill in company, ticker, depth, focus tags, and your operator alias.

2. **Run the agents (A1 → A7)**  
   Grab each prompt block from `prompts_core.md`, replace placeholders (`{{ticker}}`, `{{checksum}}`, etc.), and send it through your CLI in JSON mode. Paste the returned envelope into the run card under the matching heading.

3. **Guard the checksum**  
   After A1, highlight `metrics.data_checksum` and paste it into every downstream prompt. That checksum is your immutability anchor—lose it and the whole run resets.

4. **Quick-check before advancing**  
   After each agent, skim the bullets in `quick_checklist.md`. If an artifact is missing, ask the model to fix it before moving on.

5. **Assemble the final report**  
   Once A7 is done, copy the markdown sections (`sections_md`) in order 0–18 into your preferred editor. Use `danger_zone_stub.md` if you want to hand-tune the dashboard.

6. **Archive everything**  
   Save the run card, raw envelopes, and final report in the same folder. That’s your provenance trail.

## Survival Tips

- Set temperature to 0–0.2 to keep responses deterministic.
- If the model refuses a required artifact, resend with “Missing X, regenerate including it.”
- Preload price, sentiment, or insider data if your CLI can’t browse.
- Time-stamp each agent run in the card; future-you (or audit) will appreciate it.

You are now officially the Orchestrator. Go turn filings into fire.
