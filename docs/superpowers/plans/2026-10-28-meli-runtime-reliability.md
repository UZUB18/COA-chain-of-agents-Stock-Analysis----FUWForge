# MELI Runtime Reliability Implementation Plan

**Goal:** Make a Deep run honest about source coverage, daily prices, historical figures, and audit assurance, while preserving existing safety checks and behavior for older runs.

**Approach:** Extend the existing collector, evidence pack, typed-fact selection, and audit/report flow in small independently tested changes. Do not add a provider, database, dependency, generic fact checker, or new infrastructure. The user requested a plan, not implementation; no code changes are part of this document.

**Context:** The 2026-10-28 MELI run completed its agents but omitted two oversized historical filings, treated an intraday bar as a close, mixed reporting vintages, and did not pass the source-backed Beneish gate. See `runs/meli_deep_20261028/retrieved_inputs/omissions.json`, `runs/meli_deep_bounded_20261028/MELI_2026-10-28_report.md`, and `runs/meli_deep_bounded_20261028/audit_source_backed.json`.

## Constraints and decisions

- Keep the 8 MiB document limit, HTTPS/host validation, hashes, strict mode, and the existing source-backed gate. Never silently treat omitted material as complete or weaken verification to make MELI pass.
- Leave archived runs and their source bytes, checksums, receipts, and existing transforms unchanged. New metadata or transform behavior must be explicitly versioned or opt-in for newly collected packs; old snapshots must still reconstruct with their original rules.
- No browser testing. Use offline tests first. Do not make paid provider calls as part of automated tests.
- The one-command collector may continue after **only an oversized older annual filing** is omitted. The latest selected annual and market data remain mandatory; a missing or invalid mandatory source still fails before provider calls. Other retrieval, validation, or checksum errors must not be silently converted to omissions.
- A completed Deep run with missing annuals is **incomplete evidence coverage**, not a complete ten-year filing set. Eight selected annual records do not imply eight complete annual statements: an amendment may not replace an entire original filing.
- Prefer the previous day's completed price whenever collection occurs on the bar's US/Eastern calendar date. This conservative rule also excludes the current day's bar after the market closes; it avoids a market-calendar dependency and is explicitly disclosed.
- Source-bound arithmetic and checks do not prove every generated sentence, issuer disclosure, economic assumption, or rating is correct.

## Alternatives considered

1. **Recommended:** retain bounded downloads; record only specific older annual size failures as omissions, then run with explicit coverage warnings. This preserves the existing security boundary and makes the limitation visible.
2. Raise the size limit or introduce a special larger SEC limit. This could include more filings but changes the memory and verification boundary throughout retrieval and evidence replay; defer until there is a measured requirement and separate review.
3. Automatically create composite SG&A from other expense tags. This could unlock Beneish, but economic comparability and replay bindings must be proven first. Until then the existing unavailable state and source-backed failure are correct.

## Task 1 — Preserve filing coverage when an older annual is too large

**Likely files:** `src/evidence.py`, `src/public_data.py`, `src/orchestrator.py`, `tests/test_evidence.py`, `tests/test_public_data.py`, `tests/test_web_research.py`, `README.md`.

- [ ] Add a specific oversized-document exception that remains a `ValueError`, so callers can distinguish this limit from other failures without matching message text. Keep explicit `fetch-sources` fail-closed by default.
- [ ] Extend the existing automatic-collection path to skip that exception only for selected historical annuals after the newest annual; do not redownload all documents or catch arbitrary exceptions. Preserve existing checks for the newest annual, market data, all remaining requested documents, SEC contact, and pack validation.
- [ ] Record requested/found/**successfully retrieved** annual counts plus each omission's title, URL, filing date, and reason in checksum-protected evidence-pack metadata. Keep `collection.json` consistent with what was actually retrieved. Retain omission metadata through `EvidencePack.snapshot()` and Gemini web intake, and include a short factual coverage summary in agent context and the final report's data-gap section.
- [ ] Add offline tests proving: one oversized old filing yields a valid, hash-checked pack with an explicit gap; an oversized newest annual or invalid market source fails; unrelated download errors still fail; snapshot/web intake and tamper detection preserve the omission record. Existing complete packs and explicit manifests must behave as before.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_evidence.py tests/test_public_data.py tests/test_web_research.py tests/test_evidence_snapshot_cutoff.py`.

**Acceptance:** MELI-like collection can progress without manual pack construction while clearly stating that the requested history was not fully retrieved. It must never call the reduced pack a complete ten-year filing set.

## Task 2 — Use a defensible daily price and date

**Likely files:** `src/public_data.py`, `src/evidence.py`, `src/agents/model.py`, `src/contracts.py` only if the existing text contract needs clarification; `tests/test_public_data.py`, `tests/test_model_agents.py`, `tests/test_evidence_snapshot_cutoff.py`.

- [ ] At collection, freeze a versioned market transform containing the US/Eastern collection date and the maximum eligible bar date: the earlier of the requested analysis date and the day before collection. Derive this from the recorded collection time, not the wall clock during later verification. Use the same rule for Yahoo and Stooq. Do not change how pre-existing transforms reconstruct.
- [ ] Recompute the last price and indicators from only eligible bars; preserve the existing stale-history check and fail if no usable history remains. Explicitly identify the bar date and that the current collection-day bar was excluded. The source download's timestamp is a retrieval time, not a price timestamp.
- [ ] For new runs, keep the legacy `price_timestamp_eet` key for compatibility but use a date-only value when the source supplies only a daily bar. Bind the A1 structured price and date to the frozen market evidence; reject a conflicting model value through the existing strict validation/repair path. Instruct agents not to manufacture a time or call the analysis-date bar a closing price. Existing consumers and legacy mock/controller runs must remain compatible; this does not guarantee all free-form prose is correct.
- [ ] Add tests covering an intraday current-date bar, a request after close on the same date, historical cutoff, weekend, US/Eastern date boundary, absent/stale prices, and replay of both old and new packs. Assert that price and indicators exclude the ineligible bar and a conflicting model-supplied price or quote time fails strict validation.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_public_data.py tests/test_evidence.py tests/test_evidence_snapshot_cutoff.py tests/test_model_agents.py tests/test_controller_assembly.py`.

**Acceptance:** In the MELI scenario the price and indicators use the October 27 bar; the report says which day the price represents. An older saved evidence pack still verifies without changing its sealed bytes.

## Task 3 — Stop mixing historical filing versions in the financial table

**Likely files:** `src/evidence.py`, `src/facts.py`, `src/agents/model.py`, `src/orchestrator.py`; `tests/test_evidence_prompt_selection.py`, `tests/test_sec_facts_intake.py`, `tests/test_model_agents.py`, `tests/test_run_semantic_audit.py`.

- [ ] Reuse the existing typed SEC facts and as-of/supersession logic to select a small annual revenue, cost, and gross-profit table. Only present a row as reconcilable when company, annual period, currency/unit, consolidated basis, and filing/revision provenance are compatible. Prefer a consistent later filing available by the cutoff; do not use later filings for an earlier cutoff. If a compatible row cannot be established, show the affected fields as unavailable rather than combine versions.
- [ ] Render that canonical table deterministically in the existing raw-data section and identify its period and filing provenance. Direct the agents to use it instead of inventing a historical table. Keep the existing untrusted-source boundary and do not change unrelated financial facts or narrative.
- [ ] Check the canonical row's revenue minus cost against gross profit using the actual source precision/rounding, not a blanket loose tolerance. This check applies only to the structured table; do not claim it verifies free-form prose or the investment rating.
- [ ] Add a small MELI-shaped synthetic fixture: original 2022 values and later revised values must produce a consistent later row at a later cutoff and the original row before the revision. A mixed-vintage row, incompatible unit/period, missing value, and non-reconciling numbers must be rejected or clearly unavailable. Check that the final report renders the same table.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_evidence_prompt_selection.py tests/test_sec_facts_intake.py tests/test_fact_binding.py tests/test_model_agents.py tests/test_run_semantic_audit.py`.

**Acceptance:** The runner-generated table cannot present the observed $10.537B revenue with $5.582B cost and $5.198B gross profit as a single reconciled 2022 row. The table does not turn absent or ambiguous facts into invented values. Free-form narrative may still contain unsupported or conflicting claims and requires human review.

## Task 4 — Make assurance and gaps hard to misread

**Likely files:** `src/orchestrator.py`, `src/controller.py`, `tests/test_cli_operations.py`, `tests/test_run_semantic_audit.py`, `tests/test_output_safety.py`, `docs/audit-levels.md`.

- [ ] Put a concise, deterministic evidence-coverage warning near the start of the generated report when the pack records missing annuals or provisional/unavailable core inputs. State that a model's score and rating are judgments. Do not label the report source-backed merely because agents or ordinary strict checks passed.
- [ ] Reuse the existing `verify-run` assurance labels and per-level results. Keep the source-backed gate and exit codes unchanged. If a persistent audit result is desired, write it as a separate sidecar after verification; never edit a sealed report after its manifest has been created.
- [ ] Test a gap-bearing report, a complete evidence pack, and an unavailable Beneish witness. Ordinary strict must continue to disclose gaps, while `--require-source-backed` must still fail when its requirements are unmet. Verify the report and final-artifact hashes remain valid.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_cli_operations.py tests/test_run_semantic_audit.py tests/test_audit_zero_coverage.py tests/test_audit_evidence_coverage.py tests/test_output_safety.py`.

**Acceptance:** A reader can distinguish stage completion, integrity, source-backed calculation replay, and investment judgment without interpreting one as proof of the others.

## Task 5 — Investigate MELI's Beneish SG&A gap without inventing a value

**Likely files:** `src/forensic_inputs.py`, `src/fact_binding.py`, `tests/test_forensic_inputs.py`, `tests/test_derived_input_verification.py`, `docs/calculator-versions.md`.

- [ ] Compare the filing definitions and periods of selling/marketing and general/administrative expense against the existing total SG&A concept. Determine whether the components are non-overlapping, complete, consistently classified and available for both required years; document the evidence and conclusion.
- [ ] Only if that comparison establishes a defensible equivalent, use the existing derivation and fact-binding machinery to sum the independently sourced components for each year. Preserve operand IDs, units, periods, filing/cutoff and accounting basis; reject missing, overlapping, inconsistent or changed components. Version and document any change to the calculator convention.
- [ ] If equivalence is not established, retain the current `unavailable` result and source-backed failure, with a precise reason. Do not substitute general-and-administrative expense alone or a neutral ratio.
- [ ] Test whichever conclusion the evidence supports. For an implemented derivation, include positive replay and negative missing-component, mismatched period/currency/basis, and tampered-operand cases; retain the existing AMD acceptance path.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_forensic_inputs.py tests/test_derived_input_verification.py tests/test_amd_acceptance.py tests/test_audit_adversarial_gate.py`.

**Acceptance:** MELI passes the source-backed gate only if all existing requirements are genuinely met; otherwise it remains explicitly unavailable. This task does not promise a passing score.

## Final verification and handoff

- [ ] Run each task's focused tests first. Once the changes are integrated, run `.venv/bin/python -m pytest -q --tb=short` once and `git diff --check`. Review `git status` to confirm archived runs and unrelated files were not changed.
- [ ] Run an offline end-to-end fixture with an omitted old annual, a same-day market bar and revised facts. Verify source coverage, price date, table reconciliation, report wording and both audit modes.
- [ ] As a separate optional live acceptance step, collect a fresh MELI Deep pack, then run one explicitly chosen provider/model and both audit commands in a new directory. Live source/provider access and any bill are separate from the offline checks; do not retry an uncertain paid call automatically. Record actual coverage, usage and the remaining manual-review limitations.
- [ ] Report changed files, relevant test results, both audit results, and any remaining unverified claims. Do not claim browser verification.
