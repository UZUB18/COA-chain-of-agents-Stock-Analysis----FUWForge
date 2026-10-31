> **Historical document (2026-10-08).** Parts of this handoff are outdated. The
> current behaviour, limits and known bugs are in `docs/CODEBASE_REFERENCE.md`
> (§9 lists open issues).

# FUWForge upgrade handoff

## Status

**The eight research-compiler upgrades are integrated into the production execution path and the offline suite is green.**

Last full offline validation, **2026-10-08**, Europe/Amsterdam:

```bash
.venv/bin/python -m pytest -q --tb=short
```

Result: **410 passed, 3 deselected, 0 failed**. Exit code 0.

Also verified clean:

- `.venv/bin/python -m compileall -q src evals tests`
- `evals/score_research.py ... --markdown --fail-on-errors` on the clean fixture (exit 0, 9/9 cases) and on deliberately wrong answers (exit 1, fails closed).
- CLI smoke: offline mock run, `verify-run` (all levels `verified`), `verify-run --strict`, `seal`, `export-packet`, `--independent-bear` opt-in, and the default path making exactly one A6R call.

This is not a completion certificate for research quality. It records that the upgrade work is integrated and checked; what remains limited is listed under **Known limits**.

- Workspace: `/home/trbuzu/Documents/COA-chain-of-agents-Stock-Analysis----FUWForge`.
- No commits, deployment, paid/live calls, or archived-run changes were made. Archives remain untouched.
- The working tree still contains the modified and untracked implementation files from the original work; preserve them.

## What was fixed in this pass

All work below is in production execution paths, not isolated modules.

### 1. Summary payload crash (`src/orchestrator.py`)

`_summary_payload` read `params.flow_mode`, which `OrchestratorParams` does not have. This raised `AttributeError` on **every** run and failed 50 tests. `run(..., mode=...)` now records the real mode into run state, and the summary reads it from there. The fabricated `registry.model` fallback was replaced with the true backend model (`"not-applicable"` when no model backend is configured); `AgentRegistry` now exposes `model`, populated from the backend in `model_registry`.

### 2. Exact lineage restoration and sealing (`src/controller.py`, `src/run_semantic_audit.py`)

- **Stripped lineage is unavailable, not contradictory.** The audit already warned on stripped lineage, but the manifest-graph check hard-errored on the same condition, so a legacy/stripped run could never verify. It is now a warning, matching the existing semantic. A non-empty sealed lineage that *contradicts* the manifest is still a hard error.
- **`seal --force` recomputes descendants to a fixpoint.** When a parent changed, non-force sealing refuses. With `--force`, descendant parent-digest stamps are recomputed transitively (A2 → A6 → A7) and the output says plainly that the run now certifies the edited bytes. A one-pass implementation silently left grandchildren stale and failed strict verification.
- Sealing still rebuilds the now-stale `run_manifest.json`.

### 3. Durable resume (`src/orchestrator.py`, `src/execution_store.py`)

- **Resume no longer crashes on its own evidence.** `run()` called `pack.snapshot()`, which uses `mkdir(exist_ok=False)`; any resumed run with an evidence pack aborted before receipt reuse. Resume now reloads the existing snapshot (re-verifying every document against its digest) and **refuses** when it differs from the supplied pack, instead of overwriting the bytes earlier envelopes were generated from.
- **Reused envelopes no longer mutate.** `_accept_agent` appended contract warnings before the reuse branch, growing the warning list while deliberately *not* recomputing `content_checksum`. Reproduced: warnings grew 1 → 2 on resume, the checksum went stale, and the **second resume died** with `reusable receipt checksum mismatch`. Reuse is now read-only — validation still runs, so a reused envelope cannot bypass its contract.
- **`ExecutionStore.begin` clears the previous attempt's outcome.** The UPSERT reset `state` but kept the prior attempt's `raw_response`, `accepted_envelope`, both checksums, `usage`, `latency_ms`, `error`, and `recovery_choice`, letting one receipt describe two attempts. All are now cleared. `recovery_choice` must be cleared specifically because `requires_recovery()` only fires when it is `NULL` — a retained decision would silently discharge a *new* attempt's recovery obligation. Identity and lineage (provider/model/prompt/evidence/upstream/created_at) are preserved.
- Retry bounding verified: with a durable callback present, `retries` clamps to 0 (one attempt), so an uncertain provider call is never silently re-billed.
- SQLite per-thread connections and WAL confirmed correct; do **not** revert to a shared transaction connection.

### 4. Independent-bear lifecycle and prompt wiring (`src/agents/model.py`)

The feature was default-off and structurally present, but the **rendered prompt was self-defeating**. Fixed:

- The A6R prompt unconditionally ordered the model to rebut bull points and match upstream `BullCase` ids — while the blind phase supplies no bull case at all.
- The runner's phase directive was wrapped in `<untrusted_source_data>`, which `EPISTEMIC_RULES` explicitly tells the model to ignore. The directive would have been silently voided.
- `_normalize_envelope` overwrote the blind pass's local hypothesis ids with `B1/B2/B3`, fabricating a mapping to theses that do not exist and destroying the independence the phase exists to create.

Now the two phases render distinct, coherent task blocks from the real production path; the phase directive is authoritative text outside the untrusted wrapper while the prior bear view stays correctly tagged; and blind-phase local ids survive normalization. Confirmed the default path still makes exactly one A6R call, and initial/reconciliation receipts remain distinct identities.

### 5. Explicit-unknown evaluation fixture

The fixture omitted five expected available indices (AQI 1.02, SGI 1.10, DEPI 1.01, SGAI 0.95, LVGI 1.00). The scorer was right to fail it. Fixed the fixture; coverage scoring was **not** weakened.

### 6. Beneish unavailable test artifact

`test_data_gaps` nulled `BeneishM.score` but left `raw_score` numeric, producing an inconsistent artifact the contract correctly rejected. The contract was kept strict; the fixture now builds the unavailable result through the canonical `unavailable_beneish` helper, which clears every numeric output field.

### Tests added

`tests/test_independent_bear.py` (4, renders the real prompt for both phases and locks the id-preservation rule) and 4 regression tests in `tests/test_execution_resume.py` (evidence reuse, changed-evidence refusal, reused-envelope byte preservation, receipt attempt reset).

## Known limits (still true — do not overstate)

- Evidence is **hashed, not signed**. A matching digest proves bytes are unchanged since collection, not that they came from the issuer or say what they appear to say.
- Audit levels are independent; a green integrity level does not imply source authenticity, factual truth, or correct arithmetic.
- Local idempotency is not exactly-once external billing.
- The independent-bear challenge is experimental. Promotion needs cost and reviewer-effort evidence; no retrospective predictive-performance claim is justified.
- SEC facts still have open review items: conservative intraday availability, distinguishing repeated disclosures from restatements, deferred unsupported/dimensional concepts, and explicit failure reporting in `EvidencePack` attachment loading (the loader currently catches all exceptions and can silently drop malformed typed input; a raw `sec_companyfacts` attachment still requires the SEC parser, not the typed-ledger loader). Automatic public collection does not yet fetch and attach CompanyFacts.
- Witness validation does not yet bind each numeric witness input to the referenced fact's value, and `ratio` unit checks are bypassed.
- `BENeish` screening threshold `-1.78` is a screening policy, not a fraud finding.

## Operational constraints

- Read before editing; inspect the final diff. Check every shell exit code.
- Do not echo credentials or secret files.
- Do not alter archives, commit changes, deploy, or call paid/live services without authorization.
- Use `.venv/bin/python` (Python 3.12.13); system Python 3.14 lacks pytest. Live tests are deselected by default.
- No Agent Teams / workflow / Ralph unless explicitly requested.

## Suggested next work

1. Make `EvidencePack` attachment loading fail loudly instead of swallowing exceptions, and route raw `sec_companyfacts` through the SEC parser.
2. Bind each calculation-witness input to the referenced fact's value so fact-backed arithmetic is genuinely verified, and stop bypassing `ratio` unit checks.
3. Implement conservative intraday availability for SEC facts and document date-cutoff semantics.
4. Only after 1–3, consider promoting the independent-bear challenge out of experimental status with cost and reviewer-effort data.