# FUWForge Hardening and Cleanup Implementation Plan

**Goal:** Fix every confirmed defect from the 2026-10-30 full-code review, make `verify-run` report what it claims to report, remove footguns and dead code, and sequence the two already-proposed plans (MELI reliability, targeted web search/presentation) on top of a sound base.

**Source of findings:** `docs/CODEBASE_REFERENCE.md` §9 (ids such as B1, I2, F5 below refer to that list). Items marked ✔ there were re-checked by hand against commit `33322d6`; the rest were reported by a full-read review and must be reproduced with a failing test before fixing.

**Approach:** Small, independently tested commits on a new branch (e.g. `hardening-2026-10`), one task per commit, each starting with a failing test. No new runtime dependency, no new provider, no database change beyond the existing SQLite ledger. Offline tests only; no paid calls.

## Constraints

- **Archived runs are evidence.** Never modify files under `runs/`. Every task that touches verification must keep existing archived runs verifiable, or change their verdict only where this plan says so on purpose (e.g. an audit that now correctly reports a defect).
- **Checksum and identity changes are versioned, never silent.** Anything that changes `content_checksum`, the canonical JSON form, or the execution key ships with an explicit scheme/version field and keeps the legacy path for old envelopes and receipts.
- Keep strict-by-default for real providers, the 8 MiB document limit, HTTPS/host rules, exact numeric binding, and the source-backed gate.
- Each task ends with its focused tests green, then `.venv/bin/python -m pytest -q` once per phase and `git diff --check`.

## Decisions to confirm before the affected phase

| # | Decision | Recommendation | Blocks |
|---|---|---|---|
| D1 | Fix the non-injective checksum and cover semantic metrics by introducing checksum scheme v2 | Yes: v2 for new envelopes, v1 verified as legacy with an explicit `legacy_checksum_scheme` warning | Task 2.5 |
| D2 | Meaning of a date-only `analysis_date` cutoff | Treat it as end of that day (`T23:59:59Z`) everywhere, so SEC facts filed on D and documents dated D are both included; MELI Task 2 separately excludes an *intraday* price bar | Task 4.7 |
| D3 | Contract docs promise rules code does not enforce | Enforce the cheap structural ones (A6B 3–5 theses, A6R covers every bull id); relax the docs for the rest | Task 7.2 |
| D4 | Widen the execution-key code hash | Yes, hash all behavior-defining modules; accept a one-time loss of reuse for in-flight runs (finish or abandon them before upgrading) | Task 3.1 |
| D5 | Rename the installed top-level package from `src` to `fuwforge` | Defer; large mechanical change, do it alone after everything else is green | Task 6.6 |
| D6 | Compute Altman Z in the runner from typed facts (like Beneish) | Yes, as an improvement after correctness work; otherwise delete the unused calculator and the doc claims | Task 5.2 |

---

## Phase 0 — Baseline (do first)

### Task 0.1 — Make the suite order-independent (I9 ✔)
**Files:** `src/controller.py`, `src/orchestrator.py`, `src/wizard.py`, `tests/test_controller_assembly.py`, `tests/test_output_safety.py`.
- [ ] Failing test: run `tests/test_output_safety.py tests/test_controller_assembly.py` in that order; it fails today.
- [ ] Stop writing `os.environ["FUWFORGE_STRICT"]` in `_build_params` (controller.py ~:120). Carry `strict` explicitly from CLI args into `FUWForgeOrchestrator(strict=...)`; keep reading the env var only as an input default.
- [ ] Test: after `main(["run", ..., "--strict"])`, `FUWFORGE_STRICT` is unchanged in `os.environ`.
- [ ] Run `.venv/bin/python -m pytest -q -p no:randomly` and once with the two files reversed.

**Acceptance:** full suite passes in any file order.

### Task 0.2 — Record the archived-run baseline
- [ ] Script (kept under `scripts/` or run ad hoc, not committed output): for every directory under `runs/` with `run_summary.json`, save `verify-run --json` and `verify-run --integrity-only --json` output to a scratch location outside the repo.
- [ ] After each later phase, re-run and diff. Every verdict change must be listed in the commit message with the task that caused it.

---

## Phase 1 — Stop damaging or mislabeling runs

### Task 1.1 — A refused resume must not overwrite archived outputs (B1 ✔)
**Files:** `src/orchestrator.py` (`run()` ~:158-306), `tests/test_execution_resume.py`, `tests/test_execution_recovery.py`.
- [ ] Failing tests, one per cause, asserting `run_summary.json` and `run_manifest.json` bytes are unchanged after the refusal: (a) resume with a different evidence pack, (b) resume with uncertain receipts and no `--recovery-choice`, (c) strict non-mock run without an evidence pack against an existing dir.
- [ ] Move all preflight checks (target dir, evidence identity, strict-evidence requirement, recovery requirement) into a `_preflight()` that runs **before** the `try` that writes halted outputs. Only failures after dispatch begins write a halted summary/manifest.
- [ ] Keep current behavior for a genuine mid-pipeline halt on resume (that is a new attempt and should be recorded).

**Acceptance:** the three refusals exit non-zero with the same messages and leave every byte of the run directory unchanged.

### Task 1.2 — Wizard: audit actually runs; fail-closed default (B2 ✔, B5 ✔)
**Files:** `src/wizard.py`, `src/controller.py`, `tests/test_wizard.py`.
- [ ] Replace hand-built `argparse.Namespace` objects in the wizard with `build_parser().parse_args([...])` so new CLI flags can never be missing again (fixes the `args.strict` AttributeError on ~:302).
- [ ] Default "Strict" to **Yes** for real providers, matching `cmd_run`; `allow_defaults` only when the user explicitly opts in, with the same warning text the CLI uses.
- [ ] Packet output name `.json`, not `.zip`.
- [ ] Tests: wizard flow with mocked prompts reaches and prints the audit result; default answers produce a strict run.

### Task 1.3 — Controller-assembled runs are not labeled mock (B3)
**Files:** `src/controller.py` (`cmd_assemble`, `cmd_validate`), `src/orchestrator.py` (`_write_outputs`), `tests/test_controller_assembly.py`.
- [ ] Reproduce: `assemble` of non-mock envelopes produces "MOCK PROVIDER" banner and `provider: mock`.
- [ ] Use provider label `controller` (model `not-applicable`) for `mode="controller"`; banner "CONTROLLER-ASSEMBLED — envelopes supplied by operator". Keep the mock banner for real mock runs (tests lock its position).

---

## Phase 2 — Make `verify-run` tell the truth

### Task 2.1 — Provenance tampering fails `envelope_integrity` (I1 ✔)
**Files:** `src/run_semantic_audit.py` (~:942-948), `src/run_audit.py`, `tests/test_adversarial_audit.py`.
- [ ] Failing test: edit one provenance line in a copied mock run; expect `levels.envelope_integrity == "failed"` with a provenance error.
- [ ] Replace the substring filter with structured results: have `verify_run` tag envelope-checksum errors (e.g. return them separately or prefix `envelope:`), and drop only those duplicates.

### Task 2.2 — `verify-run --strict` applies strict contract rules (I2 ✔)
**Files:** `src/run_semantic_audit.py` (`_audit_graph` ~:1381, `semantic_audit` signature), `src/controller.py` (`cmd_verify_run`), `docs/audit-levels.md`.
- [ ] Thread `strict` into `semantic_audit` → `_audit_graph` → `validate_contract(strict=strict)`. Keep contract warnings as report warnings instead of discarding them.
- [ ] Tests: lenient run with injected defaults → passes default audit with warnings, fails `--strict` at `graph_and_contracts`.
- [ ] Expected baseline diff: archived lenient runs now fail `--strict` (intended; list them).

### Task 2.3 — Missing roles report `incomplete` honestly (I5 ✔)
**Files:** `src/run_semantic_audit.py` (~:1234-1255, `AuditReport.overall_state`), `src/controller.py`, `tests/test_run_semantic_audit.py`, `tests/test_research_packet.py`.
- [ ] Policy: missing roles + manifest `halted` → `completeness=incomplete`, no error, overall `incomplete`. Missing roles + manifest `completed` (or no manifest) → error (contradiction), overall `failed`.
- [ ] Tighten `test_research_packet` which currently accepts either state.

### Task 2.4 — Seal and manifest safety (I6)
**Files:** `src/controller.py` (`cmd_seal` ~:361-501, `_rebuild_run_manifest` ~:504), `tests/test_cli_operations.py`.
- [ ] Failing test first (reasoned, not yet reproduced): strip `content_checksum` from an edited A7, run `seal` without `--force`. Expect refusal because `run_manifest.json.roles["A7"]` records a different digest.
- [ ] Compare every envelope against `manifest.roles` when a manifest exists, not only against its own recorded checksum.
- [ ] Run `find_graph_cycles` before the `--force` fixpoint loop; bound iterations to the role count.
- [ ] `_rebuild_run_manifest` failures print an error and exit non-zero instead of leaving a stale manifest.
- [ ] Rewrite envelopes with the same serializer the orchestrator uses so a no-op seal does not change bytes.

### Task 2.5 — Checksum scheme v2 (I3 ✔, I4 ✔) — needs D1
**Files:** `src/utils/checksum.py`, `src/orchestrator.py` (seal), `src/run_audit.py`, `src/run_semantic_audit.py`, `src/controller.py` (seal), `src/execution_store.py`, `schemas/envelope_v2_1.json`, `docs/audit-levels.md`, new `tests/test_checksum_scheme.py`.
- [ ] Add `envelope_content_checksum_v2`: real canonical JSON (`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False)`), covering all current fields **plus** the semantic metrics `data_checksum, evidence_checksum, injected_defaults, source_grounding, unverified_source_urls, runner_beneish_block, missing_data`. Telemetry (latency, tokens, retries, provider, model) stays excluded.
- [ ] New envelopes record `checksum_scheme: "fuwforge-canonical-json-v2"` at top level (hashed). Verification dispatches on it; absent → v1 with warning `legacy_checksum_scheme (non-injective canonical form)`.
- [ ] Receipts: `accepted_checksum` comparisons use the envelope's own scheme, so old receipts keep validating.
- [ ] Tests: the two known v1 collisions differ under v2; editing each newly covered metric after sealing fails integrity; every archived run still verifies (with the legacy warning).
- [ ] Afterwards remove the second canonicalization (`execution_store._canonical_json` and `execution_key` already use real JSON; share one helper).

### Task 2.6 — Smaller audit fixes
**Files:** `src/run_semantic_audit.py`, `tests/test_run_semantic_audit.py`.
- [ ] Upstream digest pointing at an **unsealed** parent → warning (matches legacy `verify_run`) instead of silent pass.
- [ ] Non-dict `run_manifest.json` → `final_artifacts` failure, never an exception.
- [ ] Manifest schema errors attributed to `final_artifacts` so `failed_levels` is never empty when overall is failed.
- [ ] Report unexpected roles once.
- [ ] `build_run_manifest` sets `evidence.state` from an actual `EvidencePack` load, not from the presence of a checksum.
- [ ] Re-check A1's frozen `data_checksum` echo across all envelopes during the audit (graph level).

---

## Phase 3 — Resume and execution-store safety

### Task 3.1 — Execution key covers the code that shapes output (B4 ✔, D4)
**Files:** `src/orchestrator.py` (~:712-733), `tests/test_execution_identity.py`.
- [ ] Today `inspect.getsourcefile(type(agent))` hashes `src/agents/registry.py`. Replace with a stable digest of a declared module list: `agents/model.py`, `agents/mock.py`, `agents/output_format.py`, `agents/output_schema.py`, `contracts.py`, `data_gaps.py`, `forensic_inputs.py`, `fact_binding.py`, `utils/beneish.py` (sorted, content-hashed, computed once per process).
- [ ] Test: changing a byte in any listed module changes the key; changing an unrelated module does not.
- [ ] Release note: in-flight resumable runs lose reuse after the upgrade.

### Task 3.2 — No paid re-dispatch without an operator decision (B7)
**Files:** `src/orchestrator.py` (`_run_agent` resume branch ~:734-790), `tests/test_execution_recovery.py`.
- [ ] A `failed` receipt that has a recorded provider response and fails local recovery requires `--recovery-choice retry`, like `unknown_completion`.

### Task 3.3 — Execution-store state machine (I7)
**Files:** `src/execution_store.py`, `tests/test_execution_resume.py`.
- [ ] Allowed-transition table enforced in every `mark_*`, `begin`, `resolve_recovery`; illegal transitions raise. Example: `resolve_recovery(retry)` on `validated` must not demote it.
- [ ] `begin(execution_key=...)` verifies the key against the identity fields.
- [ ] `abandon` is durable: `begin()` refuses an abandoned key unless called with an explicit override.
- [ ] Optional improvement: per-node choices `--recovery-choice A3=retry,A5=abandon`.
- [ ] Default store path: require an explicit path (drop the per-thread-empty `:memory:` default) or use a shared-cache URI.

### Task 3.4 — Resume is read-only on disk too (B8) and keeps finished sibling work (B6)
**Files:** `src/orchestrator.py`, `tests/test_execution_resume.py`.
- [ ] Do not overwrite `a*_raw.json` when reusing; log reuse in provenance (already) and the summary.
- [ ] When one of A2–A5 fails, accept and persist the siblings that already completed (they are independent) before re-raising, so the next resume reuses them without a recovery choice.

### Task 3.5 — CLI resume ergonomics
- [ ] `--resume` without a run dir or `--run-id` → error instead of a new timestamped run.
- [ ] `--resume` implies no web search; no need to pass `--no-web-search` for Gemini.

---

## Phase 4 — Facts, SEC ingestion and evidence correctness

### Task 4.1 — No epoch placeholder facts (F1 ✔, F3)
**Files:** `src/sec_facts.py` (~:387-413), `src/evidence.py` (`_select_prompt_facts`), `tests/test_sec_facts_intake.py`.
- [ ] Deferred custom/dimensional concepts are recorded in intake coverage (`deferred_concepts`), not emitted as `Fact`s.
- [ ] Remove the `is_arelle_available()` branch (Arelle is never used); behavior must not depend on installed packages.
- [ ] `_select_prompt_facts` skips `value is None`.

### Task 4.2 — No borrowed accession numbers (F2 ✔)
**Files:** `src/sec_facts.py` (~:436), tests.
- [ ] An observation without `accn` is excluded with a warning and counted in coverage, exactly like a missing filing date.

### Task 4.3 — Disclosure chains and consistency checks (F4)
**Files:** `src/sec_facts.py` (~:576-723), `src/facts.py` (`check_ledger_consistency`), `docs/sec-availability-policy.md`.
- [ ] Repeated disclosures also carry `supersedes=prior` so the chain is linear and the original is always superseded by later filings.
- [ ] Consistency grouping treats reported+restatement as one family (forecast/estimate separate) so an unlinked conflicting restatement is flagged.
- [ ] Fix the `superseded_incomparable` doc text and field names (§4, §5) to match code.

### Task 4.4 — Persist intake coverage; CompanyFacts size gap is explicit (F5)
**Files:** `src/public_data.py`, `src/evidence.py`, `src/orchestrator.py` (report), tests. Coordinate with MELI Task 1.
- [ ] Write `SealIntakeCoverage` into `collection.json` and the attachment metadata (checksum-covered).
- [ ] CompanyFacts over 8 MiB → explicit "no typed facts: CompanyFacts exceeded limit" gap in the report's data-gap section. A larger dedicated limit is a separate, reviewed decision (MELI plan alternative 2).

### Task 4.5 — Validate every fact path; enforce limits at write time (F6)
- [ ] Embedded `evidence.json["facts"]` go through `validate_fact` like attachments.
- [ ] `fetch_evidence` rejects more than 16 attachments before writing.

### Task 4.6 — Units and tolerances say what they mean (F8, F9)
**Files:** `src/fact_binding.py`, `src/calculation_witness.py`, `src/forensic_inputs.py`, `docs/fact-binding-policy.md`.
- [ ] Give `percent` scale 0.01 relative to `ratio` so 12.5 % vs 0.125 is a unit mismatch, not compatible.
- [ ] Either make Beneish replay exact (same `fsum` path → bit-identical) or document the 1e-9 replay and 5e-7 model-index tolerances in the policy doc as float-noise allowances. Recommendation: exact for replay, keep 5e-7 for model-reported indices and document it.
- [ ] `InputBinding.to_dict` includes `constants`; `roles[*].concepts` keyed per period.

### Task 4.7 — One cutoff rule (F12) — needs D2
**Files:** `src/facts.py` (`usable_as_of`), `src/evidence.py`, `evals/score_research.py`, `docs/sec-availability-policy.md`.
- [ ] Introduce `cutoff_instant(analysis_date)` → `DT23:59:59Z` and use it for facts, documents, and the eval scorer.
- [ ] Tests at the boundary: fact filed on D, document dated D, fact filed D+1.

### Task 4.8 — Evidence bundles use the run's real cutoff (F7)
**Files:** `src/evidence_bundle.py`, `src/controller.py` (~:845).
- [ ] `bundle-evidence` reads `analysis_date` from the run/snapshot and passes it; refuse to infer it from the newest document.
- [ ] `restore_bundle` requires `analysis_date` in the manifest (drop the `2099-12-31` fallback; old bundles fail with a clear message or require `--analysis-date`).
- [ ] Same default size limit for `verify-evidence` and `restore-evidence` (512 MiB), overridable by flag.

### Task 4.9 — Retrieval hygiene (F10, F11)
**Files:** `src/evidence.py`, `src/public_data.py`.
- [ ] Price history downloaded once; the probe reuses the fetched bytes (fits MELI Task 2).
- [ ] Discovery JSON hashes recorded inside the pack metadata so they are under the pack checksum.
- [ ] After connect, check the socket peer address is global (closes the DNS-rebinding gap without new deps).
- [ ] SEC throttle guarded by a lock.
- [ ] Record the `pypdf` version used for extraction in each PDF record; on a version mismatch at load, report `extraction_mismatch` instead of a hash failure.

---

## Phase 5 — Calculators and output honesty

### Task 5.1 — No fabricated scores (F15, B9, B10, B11)
**Files:** `src/utils/finance.py`, `src/agents/model.py`, `src/agents/mock.py`, `src/agents/output_format.py`, tests.
- [ ] `generate_comparison_fields`: `evidence_quality_score_10` / `data_completeness_score_10` default to `None` with a documented gap, not 9.0 (A7 gap paths already allow this).
- [ ] A3 lenient early return (~model.py:1679) still runs Beneish normalization and rendering.
- [ ] Mock A1 `data_checksum` includes the evidence checksum like the model path.
- [ ] A6R: normalize `Medium` → `Med` so output always matches the schema enum.
- [ ] `_parse_numeric`: `%` divides by 100 only where the field is a ratio; parse `bn`; unknown suffix → error, not a guess.

### Task 5.2 — Runner-computed Altman Z (D6)
**Files:** `src/forensic_inputs.py` (or new `src/altman_inputs.py`), `src/utils/finance.py`, `src/calculation_witness.py`, `src/run_semantic_audit.py`, `src/agents/model.py`, `docs/calculator-versions.md`.
- [ ] Same pattern as Beneish: concept alternatives (working capital, retained earnings, EBIT, total liabilities, sales, assets) bound to typed facts, market value from A1 market evidence, versioned calculator, witness, replay, unavailable when inputs are missing.
- [ ] Rounding before classification removed (classify the unrounded score).
- [ ] If D6 is "no": delete `altman_z_score` and fix `architecture.md`.
- [ ] PEG: document as model-reported estimate (no calculator) in the A3 contract doc.

### Task 5.3 — Research packet fixes (I8)
**Files:** `src/research_packet.py`, `schemas/research_packet_v1.json`, `tests/test_research_packet.py`.
- [ ] Accept list-form `missing_data` via `data_gaps.missing_data_map`.
- [ ] Scorecard claim reads `Scorecard.Overall.score`.
- [ ] Calculations schema allows the witness fields the code actually emits (`input_periods`, `input_bases`, `derivations`); test with a real runner Beneish witness.
- [ ] Remove `approved` from the schema enum (code already forbids it).
- [ ] `analysis_date` never falls back to today; missing → export error.

### Task 5.4 — Sentry and eval scorer (F13, F14)
- [ ] Sentry: validate LLM JSON (materiality/action enums, probability 0–1) and fail closed; report formatter uses the configured threshold; use or drop the unused DangerZone extraction.
- [ ] Scorer: report malformed (non-mapping) predictions as failures; a scalar result for an `is_unavailable` calculation fails missingness; dedupe fact errors; remove unused imports. Add regression fixtures for each.

---

## Phase 6 — Simplify and make it easier to work on

### Task 6.1 — Delete dead code
- [ ] Unused: `_EMPTY_SHA256`, `_SITE_PACKAGES_ROOT`, `StubAgent` (if no test needs it), `register_factory`, `default_registry(**kwargs)`, `Envelope.merge_sections`, `set_timestamp_now`, `verify_run_semantic` (keep re-export only if tests use it), `ExecutionStore.invalidate_descendants` / `reusable_nodes` / `get_reusable_envelope` / `is_reusable` / `lookup` (or wire them in Task 3.4), unused imports in `finance.py` and `score_research.py`, dead `name` variable in `fetch_evidence`, initial-bear "Headwinds" fallback.
- [ ] Grep tests before each deletion.

### Task 6.2 — Remove redundancy in the orchestrator
- [ ] Write `run_manifest.json` once at completion (after the summary) instead of twice.
- [ ] Drop the duplicate `envelopes["A1"]` assignment and the duplicate `needing` list.
- [ ] Split `run()` (preflight / pipeline / finalize) as part of Task 1.1 so each piece is testable.

### Task 6.3 — Retry configuration that matches reality
- [ ] `config/orchestrator.yaml` `max_retries: 3` only affects non-durable direct use. Rename/document as `repair_attempts` capped at 1 in durable mode, or remove; skip the 15 s backoff before a repair of an already-received response (no rate-limit reason to wait).

### Task 6.4 — Provider plumbing
- [ ] AGY backend passes the prompt on stdin, not argv (ARG_MAX), and maps timeout to `unknown_completion` explicitly.
- [ ] `.env` lookup uses `resources`-style resolution (checkout root, else CWD) so installed packages don't look in `site-packages/.env`.

### Task 6.5 — One way to build args
- [ ] Wizard and tests construct CLI arguments only via the real parser (done for the wizard in 1.2; extend to test helpers).

### Task 6.6 — Package rename `src` → `fuwforge` (D5, optional, last)
- [ ] Mechanical rename with a temporary `src` shim, console script, packaged resources, tests. Do it alone in one commit after everything else is green.

---

## Phase 7 — Documentation matches code

### Task 7.1 — Fix doc drift
- [ ] `docs/fact-binding-policy.md` §6 (derivation rules, named constants, working example), §2.3 tolerance statement.
- [ ] `docs/sec-availability-policy.md` §3–§5.
- [ ] `docs/evidence-portability.md` trace file name, `untrusted_root`.
- [ ] `docs/audit-levels.md` level placement, re-seal statement, completeness definition, strict behavior (after Phase 2).
- [ ] `architecture.md` calculator claims (SMA/RSI in `evidence.py`, PEG, Altman).
- [ ] Mark `HANDOFF.md` as historical (dated) and point to `docs/CODEBASE_REFERENCE.md`.

### Task 7.2 — Contract docs vs enforcement (D3)
**Files:** `src/contracts.py`, `docs/contracts/A6_Synthesis.md`, `A7_Final_Assembly.md`, `A3_*.md`, `A2_*.md`, `tests/test_contracts.py`.
- [ ] Enforce: A6B 3–5 theses; A6R has at least one rebuttal per bull id and no unknown ids.
- [ ] Relax docs: Danger-Zone categories, ScenarioTable FCF/3-year detail, fixed A7 weights (weights are configurable), A2 "Not disclosed" literal, A3 `feasible=false` wording.
- [ ] Strengthen `test_contract_docs_match_code` with a small table of rule statements per doc that must have a matching check.

### Task 7.3 — Keep the reference current
- [ ] After each phase, update `docs/CODEBASE_REFERENCE.md` §9 (strike fixed items with the commit hash) and CLAUDE.md where behavior changed.

---

## Phase 8 — Already-proposed feature work (after Phases 1–2)

1. **MELI runtime reliability plan** (`2026-10-28-meli-runtime-reliability.md`). Fold in overlaps instead of doing them twice: F5 → Task 1, F11/price double download → Task 2, F12 cutoff → Task 2 boundary rules, Task 4 builds on the honest audit from Phase 2.
2. **Targeted web search + HTML presentation spec** (`../specs/2026-10-29-agent-web-search-presentation-design.md`). Depends on Task 2.5 (checksum scheme for new bundle files), Task 3.1 (execution identity), and Task 3.3 (state machine for the extra search/revision states). Turn the spec into its own plan before implementing.

## Suggested order and size

| Order | Phases/tasks | Rough size |
|---|---|---|
| 1 | 0.1, 0.2 | small |
| 2 | 1.1–1.3 | small–medium |
| 3 | 2.1–2.4, 2.6 | medium |
| 4 | 3.1–3.5 | medium |
| 5 | 4.1–4.3, 4.5, 4.6, 5.1, 5.3 | medium |
| 6 | 2.5 (checksum v2) | large, isolated |
| 7 | 4.4, 4.7–4.9 together with MELI plan | medium–large |
| 8 | 5.2, 5.4, 6.x, 7.x | medium |
| 9 | web-search/presentation spec | large |

## Final verification per phase

- [ ] Focused tests for each task, then `.venv/bin/python -m pytest -q --tb=short` and `git diff --check`.
- [ ] Offline smoke: `run` (mock) → `verify-run --strict` → `seal` (no-op must not change bytes) → `export-packet`.
- [ ] Re-run the Task 0.2 archived-run audit and diff; explain every changed verdict.
- [ ] `git status`: nothing under `runs/` modified.
