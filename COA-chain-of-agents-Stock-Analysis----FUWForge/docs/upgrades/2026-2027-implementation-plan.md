# FUWForge research-compiler implementation plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task by task. This is a proposed implementation plan, not a patch already applied to the repository.

**Goal:** Make material research claims traceable to dated source facts and independently replayable calculations, with accurate execution lineage and recoverable evidence.

**Architecture:** Keep the current nine-role orchestration and controller. Correct and version the numerical layer, capture execution inputs, archive evidence and strengthen independent audits. Add typed facts and packet export before introducing durable selective reruns or additional challenge passes.

**Tech stack:** Existing Python/pytest/controller/contracts; standard-library hashing and filesystem verification; optional SEC XBRL/Arelle integration; SQLite for the first durable execution ledger; Inspect AI only after deterministic evaluation cases exist.

**Spec:** [FUWForge report](../reports/03-fuwforge.md) and [shared research packet](../reports/04-shared-research-packet.md). Audited commit `5f43c877b864c450f736b64f20b44741bd0ea630`.

## Global constraints

Do not overwrite archived runs or silently reseal legacy lineage. Missing facts remain unknown, not default neutral values. Hash integrity is not claim truth or publisher authenticity. Preserve bounded source retrieval, private-network restrictions and secret separation. Default tests must not make paid/live model calls. A successful reference test is not a successful upstream integration test.

## Review focus

A wrong coefficient must be caught by an independent golden result. Parallel acceptance order must not create false parents. Missing source files must fail source reconstruction. An amended filing must not silently overwrite the original period/version. A crash after a provider call must not be described as guaranteed exactly-once execution.

## Task 1 - Correct and version the financial formula (P0)

**Files:** Create `src/utils/beneish.py`, `tests/test_finance_formula.py` and `docs/calculator-versions.md`. Modify `src/utils/finance.py` and any discovered call sites. Add a calculation version to quantitative output metadata through the relevant artifact/schema path.

**Interfaces:** `beneish_score(*, dsri,gmi,aqi,sgi,depi,sgai,lvgi,tata) -> float` has no input defaults and returns full precision. The existing outward helper can preserve its report shape but must classify the raw score and identify the calculator version. Missing required inputs become an explicit unavailable result at the orchestration boundary.

- [ ] Write golden tests: all seven ratio indices equal 1 and TATA 0 => -2.48; TATA 0.1 => -2.0121; increasing LVGI from 1 to 2 changes score by -0.327. Add absent, boolean, string and nonfinite input cases.
- [ ] Run `pytest -q tests/test_finance_formula.py`; confirm the intended failures before adapting the reference implementation.
- [ ] Adapt `docs/reference/beneish.py`. Change the existing wrapper's coefficients and remove plausible missing-data defaults. Preserve rounded display output separately from raw classification. Add formula source and threshold-policy identifiers; do not equate the screen with a fraud finding.
- [ ] Trace every actual call path and quantitative artifact producer. Add a test proving that a reported Beneish result, when produced, comes from the deterministic calculator rather than an unchecked model number. If an existing path does not compute the helper, mark its result provenance rather than assuming the fix automatically reaches it.
- [ ] Run `pytest -q` using the project's default non-live selection. Replay source-backed archived calculations where inputs are recoverable. Write an impact file with `changed`, `unchanged`, `not_using_helper` or `inputs_unavailable`; do not fabricate a global historical impact count.
- [ ] Commit the formula correction independently from prompt changes. New results receive a new calculator version; old run artifacts remain immutable.

Minimal golden fixture:

```python
import pytest
from src.utils.beneish import beneish_score

def test_published_eight_variable_coefficients():
    value = beneish_score(dsri=1., gmi=1., aqi=1., sgi=1.,
                         depi=1., sgai=1., lvgi=1., tata=.1)
    assert value == pytest.approx(-2.0121)
```

## Task 2 - Capture actual consumed-parent lineage (P0)

**Files:** Create `src/execution_identity.py` and `tests/test_execution_identity.py`. Modify `src/orchestrator.py`; extend `tests/test_run_integrity.py` using the existing mock-run construction rather than a live provider.

**Interfaces:** Immutable `UpstreamRef(agent, content_checksum)`. `capture_upstream(serialized_envelopes) -> tuple[UpstreamRef,...]`. Extend `AgentExecution` with `consumed_upstream`; store the exact rendered prompt hash in execution metadata.

- [ ] Write a pure test capturing four A1-only parent snapshots, then adding A2 to the accepted dictionary and changing the dictionary's A1 hash. All four captures must remain A1-only with the original hash.
- [ ] Add a mock orchestration regression that completes the core parallel phase and inspects the four written envelopes. Each must record the parents actually supplied at execution, not earlier siblings in acceptance order. Run `pytest -q tests/test_execution_identity.py tests/test_run_integrity.py` and confirm the regression fails against the old acceptance logic.
- [ ] Adapt `docs/reference/execution_identity.py`. In model/mock execution, freeze the input envelope payload before constructing `AgentContext`, capture parents from that same snapshot, and return them in `AgentExecution`. At `_accept_agent`, assign the captured values instead of iterating over the current `envelopes` dictionary.
- [ ] Validate cross-agent contracts against the appropriate actual inputs. Keep canonical output ordering separate. For imported controller/legacy envelopes, preserve their identity and mark unverifiable lineage explicitly; do not synthesize evidence that an old prompt consumed a particular parent. Strict resumable execution rejects unsealed parents.
- [ ] Run focused integrity tests and full `pytest -q`. Compare a mock run's exact prompt inputs and recorded graph. Verify canonical serialization is stable and a changed model/prompt/evidence digest changes execution identity.
- [ ] Commit. Do not add caching/selective reruns until the corrected graph is covered by the integration test.

Integration delta, with imports and dataclass field added in the actual file:

```python
# Before constructing context for a fresh model/mock execution:
input_snapshot = copy.deepcopy(envelopes or {})
consumed_upstream = capture_upstream({
    agent_id: envelope.to_dict()
    for agent_id, envelope in input_snapshot.items()
})
# context.envelopes = input_snapshot
# return AgentExecution(..., consumed_upstream=consumed_upstream)

# When accepting that fresh execution:
env.upstream = [
    {"agent": ref.agent, "content_checksum": ref.content_checksum}
    for ref in execution.consumed_upstream
]
```

This is a targeted integration sketch; the complete pure utility is runnable in the reference package. Controller-mode handling remains a deliberate separate branch.

## Task 3 - Portable evidence bundle (P0)

**Files:** Create `src/evidence_bundle.py`, `tests/test_evidence_bundle.py` and `docs/evidence-portability.md`. Modify `src/controller.py`, `src/evidence.py`, `README.md` and `.gitignore` comments to describe the actual storage policy.

**Interfaces:** Proposed commands `bundle-evidence <run_dir> --output <directory>` and `verify-evidence <directory> --manifest-sha256 <trusted_digest>`. A manifest lists contained relative paths, exact byte sizes, SHA-256 values, extractor identity and original source metadata. Do not call a local manifest hash authentic unless it is compared to an independently trusted value.

- [ ] Test a valid bundle, a missing body, modified bytes, modified extracted text, a coherent rewritten manifest against the old trusted root, duplicate/unlisted files, traversal and symlink entries. Add an extraction-version mismatch test.
- [ ] Run `pytest -q tests/test_evidence_bundle.py` and verify the intended missing behaviors.
- [ ] Adapt the reference bounded-directory verifier, then add a producer adapter around the existing `EvidencePack`. Keep raw and extracted evidence together. Set an explicit production total-size policy, initially 512 MiB for a complete supported pack, while retaining the existing per-document limits; the reference helper defaults to the smaller 64 MiB demo limit.
- [ ] Support restoration into a fresh directory and then invoke the existing re-extraction/hash checks. If an archive transport is added, implement secure bounded extraction separately; the directory verifier alone is not a safe tar/zip extractor. Avoid publishing private compiled research or source material without appropriate permission.
- [ ] Run focused tests, full `pytest -q`, and a clean-directory restore/verify experiment with a local synthetic evidence pack. Test a unavailable/missing bundle path and confirm the audit reports source reconstruction unavailable rather than success.
- [ ] Commit documentation and mechanism together. Keep a trusted manifest reference in the release/review record; do not rely solely on a hash shipped beside the editable bundle.

## Task 4 - Independent semantic and calculation audit (P1)

**Files:** Modify `src/run_audit.py`, `src/contracts.py`, `src/orchestrator.py` and `src/controller.py`. Create `schemas/run_manifest_v1.json`, `tests/test_run_semantic_audit.py` and `src/calculation_witness.py`.

**Interfaces:** Audit result separately reports envelope integrity, source reconstruction, graph/contracts, calculation replay and final-artifact binding. Completed runs require the exact expected role set. Halted runs can verify their completed subgraph but must remain explicitly incomplete.

- [ ] Write tests for duplicate role IDs, missing completed roles, swapped parent hashes, a changed report after sealing, a source-unit mismatch and a well-shaped but arithmetically wrong quantitative artifact. Existing schema-only validation must not satisfy these tests.
- [ ] Run `pytest -q tests/test_run_semantic_audit.py`; record the failures.
- [ ] Rerun semantic contracts in actual dependency order. Bind the exact final report, artifact bundle and evidence root into a run manifest. Keep a calculation witness with formula ID/version, input fact IDs/values/units, parameters and unrounded result.
- [ ] Implement independent recomputation for the first deterministic metric, Beneish. Reject unsupported or missing inputs at the appropriate level. Do not call `verify_run` successful at all levels just because its envelope hashes pass.
- [ ] Run new audit tests, existing contract/integrity/CLI tests and `pytest -q`. Produce a diagnostic report for a good synthetic run and each deliberately damaged copy. Never mutate the good fixture while testing a damaged variant.
- [ ] Commit. Document the distinction between matching bytes, valid calculations and reviewed factual support in CLI output.

## Task 5 - Typed fact ledger and filing chronology (P1)

**Files:** Create `src/facts.py`, `schemas/financial_fact_v1.json`, `tests/test_facts.py` and `tests/fixtures/facts/`. Modify `src/public_data.py`, `src/evidence.py`, A1 intake and A3 quantitative contract/prompt paths.

**Interfaces:** Facts carry stable IDs, company identifier, concept, value, unit/currency, period/instant, accession, availability time, retrieval time, source location, accounting basis, extractor version and optional supersedes ID. Facts and forecasts use distinct record kinds.

- [ ] Write fixtures for a standard annual fact, a quarter, a restatement, a custom/dimensional fact, a 1,000x unit error, different share-count bases and an unavailable input. A fact from after the cutoff must not pass merely because its economic period is earlier.
- [ ] Run `pytest -q tests/test_facts.py`; confirm missing typed/chronology checks.
- [ ] Implement schema and semantic validation. Add SEC structured-fact ingestion for supported standard/entity-level cases; retain original accession/context metadata. Add an Arelle adapter only for cases that require filing-level custom/dimensional processing. Use a layout-preserving fallback for unstructured sources, never as an excuse to accept unverified numeric extraction.
- [ ] Extend the public-source discovery policy for relevant amendments/material events with explicit caps and provenance. Preserve current fair-access and public-URL restrictions. For missing exact publication time, record the precision limitation instead of manufacturing a timestamp.
- [ ] Run fixture tests, public-source/evidence tests and full `pytest -q`. Manually verify the source mapping for one real company before enabling it across the watchlist; live retrieval is an implementation-stage test, not performed here.
- [ ] Commit. Keep the original fact when adding a restatement; downstream views choose an as-of policy explicitly.

## Task 6 - Versioned packet export (P1)

**Files:** Create `src/research_packet.py`, `schemas/research_packet_v1.json`, `tests/test_research_packet.py`. Modify `src/controller.py` to add proposed `export-packet <run_dir> --output <file>`.

**Interfaces:** Output follows the shared contract in `reports/04-shared-research-packet.md`. It includes only bounded facts, calculations, claims, source references, versions and limitations. It does not claim dashboard approval.

- [ ] Write producer fixtures for a complete run, an incomplete run, wrong company identity, missing claim reference, unsupported schema and changed exact export bytes. Require explicit audit state on every export.
- [ ] Run `pytest -q tests/test_research_packet.py`; confirm the absent export contract fails.
- [ ] Implement validation and export. Hash the exact immutable UTF-8 file externally rather than introducing a self-referential content hash. Strip unrelated personal data and do not embed full agent transcripts or every raw filing into the reader packet.
- [ ] Run the exported fixture against the dashboard schema and reference consumer cases. Require producer/consumer contract fixtures to agree; code generation alone is not an integration test.
- [ ] Run full `pytest -q`, export one synthetic packet and verify the dashboard can display it as pending without changing a plan.
- [ ] Commit. Manual authenticated review remains required for acceptance; the producer cannot promote its own recommendations into authored plans.

## Task 7 - Durable execution receipts and selective resume (P2)

**Files:** Create `src/execution_store.py`, `tests/test_execution_resume.py`, `schemas/execution_receipt_v1.json`. Modify `src/orchestrator.py`, provider response handling and `src/controller.py` for proposed `resume` behavior.

**Interfaces:** A SQLite-backed store tracks pending/running/returned/validated/failed/unknown_completion states. Execution identity binds actual provider/model, exact prompt, source root, configuration/calculator versions and captured parents. Raw completion is not the same as validated acceptance.

- [ ] Write crash-injection tests after provider return, after receipt persistence and after validation. Test matching-node reuse, changed-evidence invalidation and mismatched prompt/model invalidation. Assert that incomplete/unknown provider completion is not labeled safely reusable.
- [ ] Run `pytest -q tests/test_execution_resume.py` and record the intended failures.
- [ ] Persist bounded response receipts atomically when calls return, including actual provider usage where available. Validate before marking a result reusable. Store raw and accepted checksums distinctly. Use the graph from Task 2 for descendant invalidation.
- [ ] Implement explicit recovery choices for uncertain completions. Explain that retry can duplicate an external paid call when the provider has no applicable idempotency guarantee. Do not advertise exactly-once billing.
- [ ] Run failure-injection tests, full `pytest -q`, and a mock multi-agent kill/resume experiment. Check that a successful sibling survives another sibling's failure and is not called again unnecessarily.
- [ ] Commit. Consider LangGraph only if persistent pauses/forks exceed what this small journal cleanly supports; a framework migration is not required for this task.

## Task 8 - Independent challenge and error-focused evaluation (P2)

**Files:** Create `evals/research_cases.jsonl`, `evals/score_research.py`, `tests/test_eval_scoring.py` and `docs/evaluation-policy.md`. Modify A6 orchestration/contracts behind an experiment flag for an independent first-pass bear view.

**Interfaces:** Each case specifies source pack, cutoff, expected numeric/semantic constraints, permitted missingness and adversarial source text where relevant. Scoring separates unsupported claims, arithmetic, chronology, source references, cost and reviewer effort.

- [ ] Write scorer tests where persuasive but unsupported prose fails, a correct explicit unknown passes its missingness criterion, a numerically wrong formula fails regardless of writing quality, and a model following source-injected instructions fails the relevant safety check.
- [ ] Run `pytest -q tests/test_eval_scoring.py`; observe intended scoring failures before implementing the scorer.
- [ ] Implement deterministic scoring first and human-reviewed qualitative sampling second. Add Inspect AI only as the execution/logging harness when useful; do not replace goldens with a model judge's unexplained overall rating.
- [ ] Run a fixed-budget experiment comparing the existing bull/rebuttal flow with an independent bear first pass plus the existing rebuttal. Preserve both initial and reconciled views. Stop extra debate when no budgeted, decision-relevant evidence gap remains.
- [ ] Run scorer tests and full `pytest -q`; analyze error types and subgroup performance rather than only a mean score. Treat old-date evaluation as evidence-faithfulness testing unless prospective predictive validity is separately established.
- [ ] Commit the result and promote only if material-error detection improves at acceptable cost. A longer report or higher agent agreement is not an acceptance criterion.

## Definition of done

Tasks 1-4 repair the trust foundation; Tasks 5-6 create the research compiler and useful dashboard integration; Tasks 7-8 improve efficiency and challenge quality. Every task requires its own regression evidence and a full non-live suite run in the actual repository. Heavy providers, live source retrieval and real deployment checks require separate explicit execution budgets.
