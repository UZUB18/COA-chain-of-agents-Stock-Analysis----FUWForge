# Controller Mode Runbook

Use this SOP when running the framework manually (no automated agent invocation).

## 1. Prepare Inputs

1. Copy `templates/inputs_blank.yaml` to `runs/<run_id>/input.yaml`.
2. Fill in company, ticker, analysis date, depth, focus tags, and custom weights if overriding defaults.
3. Record the run ID in the provenance log header.

## 2. Execute A1

1. Load the `docs/contracts/A1_GOD_Gate.md` prompt into your IDE or model client.
2. Provide the filled input form and enforce the “no analysis” clause.
3. Save the raw agent response to `runs/<run_id>/a1_raw.json`.
4. Run `python -m src.controller validate runs/<run_id>/a1_raw.json` to enforce schema and gate rules.
5. On success, copy the validated envelope to `runs/<run_id>/a1_envelope.json`. If failure, remediate data gaps and rerun A1.

## 3. Parallel Agents (A2–A5)

For each agent:

1. Load its contract prompt (`docs/contracts/A2_*.md`, etc.).
2. Provide frozen context, checksum, and relevant focus tags.
3. Save response as `runs/<run_id>/aX_raw.json`.
4. Validate via the controller CLI; fix contract breaches or missing artifacts before proceeding.

## 4. Adversarial Loop (A6B, A6R, A6)

1. Run A6B (bull case), ensure each thesis references upstream artifacts.
2. Run A6R (bear rebuttal) referencing bull IDs.
3. Run A6 (CIO synthesis) ensuring Danger-Zone dashboard and scenario table render correctly.
4. Validate each envelope with the CLI.

## 5. Final Assembly (A7)

1. Provide A7 with all validated envelopes and the contracts.
2. Confirm the scorecard math and lay summary (if applicable).
3. Validate the final envelope.
4. Run `python -m src.controller assemble runs/<run_id>` to build the final report and artifact bundle.

## 6. QA & Packaging

1. Complete the checklists in `docs/checklists.md`.
2. Review the provenance log for continuity (hash chain).
3. Archive the run directory (inputs, envelopes, report, logs).

## Notes

- Never bypass validation; rerun the agent if schema or artifact checks fail.
- If A1 fails, halt immediately and address the Missing Data Table items.
- Limit retries to three attempts per agent unless you document an exception in the provenance log.
