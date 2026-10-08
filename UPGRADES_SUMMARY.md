# FUWForge — research-compiler upgrades: what changed, what is left

**Scope:** the eight-item 2026–2027 research-compiler upgrade program.
**Dates covered:** 2026-10-07 (yesterday — first implementation pass) and 2026-10-08 (today — integration and bug-fix pass).
**Audited baseline commit:** `5f43c877b864c450f736b64f20b44741bd0ea630`.
**Source of requirements:** [`COA-chain-of-agents-Stock-Analysis----FUWForge/docs/upgrades/2026-2027-implementation-plan.md`](COA-chain-of-agents-Stock-Analysis----FUWForge/docs/upgrades/2026-2027-implementation-plan.md) and [the upgrade report](COA-chain-of-agents-Stock-Analysis----FUWForge/docs/upgrades/2026-2027-upgrade-report.md).

This document is a plain-language summary for a human reader. It explains what each upgrade does, what was actually changed on each day, and — importantly — what is still incomplete or unsafe. It is deliberately explicit about the bad parts.

---

## How to read the status labels

- **Done** — implemented in the real execution path and covered by tests that run offline.
- **Partial** — the module exists and has tests, but it is not fully wired into production, or a known correctness gap remains.
- **Not done** — not implemented.

> **Do not read "Done" as "trusted."** Every item here is about *provenance and verifiability*, not about whether a research conclusion is correct. See [Limits that apply to everything](#limits-that-apply-to-everything).

---

## The eight upgrades at a glance

| # | Upgrade | Status | One-line meaning |
|---|---|---|---|
| 1 | Correct + version the Beneish calculator | **Done** | The M-Score formula now uses the published coefficients and says which version produced a number. |
| 2 | Capture exact execution-time lineage | **Done** | Each agent records the exact parents it was shown, not whatever happened to be accepted before it. |
| 3 | Portable, independently verifiable evidence | **Done** | Evidence can be bundled, hashed, and restored; changes are detected. |
| 4 | Independent, layered run audits | **Partial** | The audit separates its levels honestly, but arithmetic is not yet bound to source facts. |
| 5 | Typed, point-in-time financial facts | **Partial** | Typed SEC facts exist but are not fully wired; silent failure remains. |
| 6 | Bounded research packets | **Done** | A review-sized, human-approval-gated export exists. |
| 7 | Durable receipts + safe resume | **Done** | Interrupted runs resume by reusing verified work instead of re-paying for it. |
| 8 | Optional independent-bear challenge + frozen evaluation | **Done (experimental)** | A blind second-opinion bear pass, opt-in, plus a deterministic offline scorer. |

---

# Day 1 — 2026-10-07 (yesterday): building the machinery

The first pass created most of the new modules, schemas, tests, and policy documents. It stopped mid-way with several untested edits, so the suite was left in a **red** state (397 passed / 5 failed) and several features were present as modules but not yet trusted in the real run path. Work done yesterday (all still in the tree):

### 1. Beneish calculator and contracts
- New [`src/utils/beneish.py`](src/utils/beneish.py): eight keyword-only, required inputs; no defaults; booleans/strings/nulls/non-finite values rejected. Correct published coefficients (TATA `+4.679`, LVGI `-0.327`). Version `2.0.0`, variant `m-score-8-variable`, `math.fsum` for precision.
- Golden values: seven indices = 1 with TATA 0 → `-2.48`; TATA 0.1 → `-2.0121`; LVGI 1→2 shifts by `-0.327`.
- Contracts in [`src/contracts.py`](src/contracts.py) reject bare/unlabelled scores; model-reported numbers are never promoted to fact-backed by arithmetic replay alone.
- Docs: [calculator-versions.md](docs/calculator-versions.md), [calculator-impact.md](docs/calculator-impact.md). Archive-impact review found 15 archives: 6 helper-unused, 9 inputs-unavailable, none with all eight inputs — so no historical reclassification was possible. Archives untouched.

### 2. Execution-time lineage
- New [`src/execution_identity.py`](src/execution_identity.py): immutable `UpstreamRef`, `capture_upstream`. Frozen input snapshots so acceptance-order races cannot invent parents.

### 3. Evidence portability
- New [`src/evidence_bundle.py`](src/evidence_bundle.py): build/verify/restore with containment, symlink, duplicate, size, and trusted-manifest-digest checks. Doc: [evidence-portability.md](docs/evidence-portability.md).

### 4. Layered audit + packets
- New [`src/run_semantic_audit.py`](src/run_semantic_audit.py) (separates byte-integrity / source-reconstruction / graph-contracts / arithmetic-replay / final-binding / completeness), [`src/calculation_witness.py`](src/calculation_witness.py), [`src/research_packet.py`](src/research_packet.py), and new schemas for the run manifest and research packet.

### 5. Typed SEC facts
- New [`src/facts.py`](src/facts.py), [`src/sec_facts.py`](src/sec_facts.py), point-in-time intake in [`src/public_data.py`](src/public_data.py), fact schema `financial_fact_v1.json`.

### 6. Durable resume
- New [`src/execution_store.py`](src/execution_store.py): SQLite execution ledger with per-thread connections (WAL), receipts, resume, selective invalidation, and an explicit "uncertain completion → operator chooses retry/abandon" recovery model.

### 7. Frozen evaluation
- New [`evals/score_research.py`](evals/score_research.py), 9 frozen cases in `evals/research_cases.jsonl`, prediction fixtures, and CI wiring. Doc: [evaluation-policy.md](docs/evaluation-policy.md).

### 8. The blind-bear challenge
- Opt-in `--independent-bear`: a blind A6R pass (sees only A1–A5) followed by a reconciliation against the bull case, with the blind theses and initial envelope/checksum preserved. Default-off.

### State at end of day 1
`397 passed, 5 failed`. The five failures and several "latest edits" had **no checks at all**. This is what today picked up.

---

# Day 2 — 2026-10-08 (today): integration, bug fixes, and honest verification

Today's goal was to finish the production-path integration and make the suite green, then verify the real behavior rather than trusting the tests.

## The blocker that broke everything
`_summary_payload` read `params.flow_mode`, but `OrchestratorParams` has no such field. This raised `AttributeError` on **every run** and failed 50 tests. Fix: record the real `mode` argument into run state and read it from there. Also replaced the fabricated `registry.model` fallback with the real backend model. (→ `src/orchestrator.py`, `src/agents/registry.py`)

## Fixes by upgrade

### Upgrade 2 — lineage and sealing (`src/controller.py`, `src/run_semantic_audit.py`)
- **Stripped lineage is "unavailable," not "wrong."** The audit already warned when lineage was stripped, but the manifest-graph check hard-errored on the same thing, so a legacy/stripped run could never verify. Now it warns (matching the existing semantics). A *non-empty* lineage that genuinely contradicts the manifest is still a hard error.
- **`seal --force` recomputes descendants to a fixpoint.** When a parent changes, non-force sealing refuses. With `--force`, descendant parent-digest stamps are recomputed transitively (A2 → A6 → A7) and the tool says plainly that the run now certifies the *edited* bytes. (A one-pass version left grandchildren stale and failed strict verification.)

### Upgrade 7 — durable resume (`src/orchestrator.py`, `src/execution_store.py`)
Three real durability bugs, each reproduced before fixing:
- **Resume crashed on its own evidence.** `run()` called `snapshot()`, which uses `mkdir(exist_ok=False)`; any resumed run with an evidence pack aborted before it could reuse any receipt. Now resume reloads and re-verifies the existing snapshot and **refuses** if the evidence differs from the supplied pack.
- **Reused envelopes mutated.** `_accept_agent` appended contract warnings *before* the reuse branch, growing the warning list while deliberately *not* recomputing `content_checksum`. Reproduced: warnings grew 1 → 2, the checksum went stale, and the **second resume died** with `reusable receipt checksum mismatch`. This was invisible to the suite because mock agents emit no warnings. Reuse is now read-only (validation still runs, so a reused envelope can't bypass its contract).
- **`ExecutionStore.begin` left the previous attempt attached.** The UPSERT reset `state` but kept the prior attempt's raw response, accepted envelope, both checksums, usage, latency, error, and `recovery_choice`, so one receipt could describe two attempts. All attempt-scoped fields are now cleared. `recovery_choice` **must** be cleared because `requires_recovery()` only fires when it's NULL — a retained decision would silently discharge a *new* attempt's recovery obligation.
- Verified: retries are bounded to one attempt when a durable callback is present (no silent re-billing); SQLite per-thread connections are correct and must not be reverted.

### Upgrade 8 — independent-bear prompt wiring (`src/agents/model.py`)
The feature was structurally present but the **rendered prompt was self-defeating**:
- The A6R prompt told the model to "match upstream BullCase ids" even in the blind phase, where no bull case is supplied at all.
- The runner's phase directive was wrapped in `<untrusted_source_data>`, which the prompt's own rules tell the model to ignore — so the instruction would be silently voided.
- The normalizer overwrote the blind pass's local ids with `B1/B2/B3`, fabricating a mapping to theses that don't exist yet and destroying the independence the phase exists to create.

Now the two phases render distinct, coherent task blocks; the directive is authoritative text outside the untrusted wrapper; and blind-phase local ids survive. Verified against the *real* rendered prompts, and the default run still makes exactly one A6R call.

### Upgrade 1 — Beneish (fixture, not the contract)
`test_data_gaps` nulled `BeneishM.score` but left `raw_score` numeric, producing an inconsistent artifact the contract correctly rejected. **The contract was kept strict**; the fixture now builds the unavailable result through the canonical `unavailable_beneish` helper, which clears every numeric field.

### Upgrade 8 — evaluation fixture
The explicit-unknown prediction fixture omitted five expected available indices (AQI 1.02, SGI 1.10, DEPI 1.01, SGAI 0.95, LVGI 1.00). The scorer was right to fail it. The fixture was fixed; **coverage scoring was not weakened**.

## Tests added today
- `tests/test_independent_bear.py` (4): renders the real prompt for both phases; locks the id-preservation rule and that the directive sits outside the untrusted wrapper.
- `tests/test_execution_resume.py` (+4): evidence reuse, changed-evidence refusal, reused-envelope byte preservation, receipt attempt reset.

## Final state today
`410 passed, 0 failed` (was 50 failing). `compileall` clean. Scorer passes clean fixtures and **fails closed** on wrong answers. CLI smoke-verified: mock run, `verify-run` (all levels verified), `verify-run --strict`, `seal`, `export-packet`, `--independent-bear`, and default single-A6R-call. Docs updated: this file, [HANDOFF.md](HANDOFF.md), and a new README section on audit levels / missingness / evidence trust / resume / challenge / packets.

---

# The bad things left

These are the real, current limitations. They are not hypothetical — several are confirmed in the code today.

## A. Not fully implemented

### 1. Typed facts (Upgrade 5) are the biggest gap — module exists, wiring incomplete
- **`EvidencePack` silently swallows attachment failures.** In [`src/evidence.py:367-371`](src/evidence.py) the fact-ledger attachment load is wrapped in `try/except Exception: pass`. Malformed typed input produces **silence**, not an error — so a corrupted fact ledger looks like "no facts" rather than "broken input."
- **A raw `sec_companyfacts` attachment is routed to the wrong loader.** It goes through `load_fact_ledger` (the typed-ledger loader), but raw SEC JSON needs the SEC parser. The routing exists but the handler is wrong for that attachment kind.
- **Automatic public collection does not fetch/attach CompanyFacts.** Not implemented.
- **SEC availability is derived from calendar-day filing dates.** Midnight timestamps can imply false intraday availability; conservative intraday behavior and documented date-cutoff semantics are not done.
- **Later same-period filings are treated as restatements** even when the comparative figures are unchanged. Repeated disclosures and genuinely amended values are not yet separated.

### 2. Arithmetic replay (part of Upgrade 4) doesn't bind inputs to facts
`replay_beneish` in [`src/run_semantic_audit.py`](src/run_semantic_audit.py) verifies that a cited fact ID *exists* and passes chronology — but it never checks that the witness's numeric `inputs[key]` **equals that fact's value**. A witness could cite a real fact ID while using a fabricated number. Equating an arbitrary fact ID with fact-backed arithmetic is still possible.
Related: the `ratio` unit check is short-circuited — the comparison is `expected_unit != "ratio" and fact_unit != expected_unit`, so whenever the expected unit *is* `"ratio"` the unit check is skipped entirely.

## B. Deliberate limits that are correct but easy to forget
- **Evidence is hashed, not signed.** A matching digest proves bytes are unchanged *since collection*, not that they came from the issuer or say what they appear to say. There is no public-key signature. Missing bytes cannot be reconstructed from a digest.
- **Audit levels are independent.** A green integrity level does not imply source authenticity, factual truth, or correct arithmetic. A clean completed-subgraph does not imply a complete report.
- **Local idempotency ≠ exactly-once billing.** If a provider call's outcome is uncertain, the run stops for an explicit operator `retry|abandon` choice; a retry may re-bill.
- **The independent-bear challenge is experimental.** Promotion needs cost and reviewer-effort evidence; no retrospective predictive-performance claim is justified.
- **The `-1.78` Beneish threshold is a screening policy, not a fraud finding.**

## C. The meta-limitation that matters most
Every "Done" above means *the code runs and the checks are wired* — **not that the research output is trustworthy.** These upgrades build provenance and verifiability rails. They do not make the underlying analysis correct, and passing 410 tests does not make a stock call right. A self-consistent run can still be consistently wrong about a company.

---

## Suggested order for the remaining work
1. Make `EvidencePack` attachment loading fail loudly instead of swallowing exceptions, and route raw `sec_companyfacts` through the SEC parser. (Removes the silent-corruption risk.)
2. Bind each witness input to the referenced fact's value and stop short-circuiting the `ratio` unit check. (Makes arithmetic genuinely fact-backed.)
3. Conservative intraday SEC availability + documented date-cutoff semantics; separate repeated disclosures from restatements.
4. Only after 1–3, consider promoting the independent-bear challenge out of experimental status with cost and reviewer-effort data.

---

*Workspace: `/home/trbuzu/Documents/COA-chain-of-agents-Stock-Analysis----FUWForge`. No commits, deployments, paid/live calls, or archived-run changes were made across either day; archives remain untouched. Verification command: `.venv/bin/python -m pytest -q --tb=short` (expect 410 passed).*