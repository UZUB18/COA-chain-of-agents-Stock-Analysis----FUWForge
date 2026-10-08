# FUWForge upgrade report

**Repository:** `UZUB18/COA-chain-of-agents-Stock-Analysis----FUWForge`  
**Snapshot:** `5f43c877b864c450f736b64f20b44741bd0ea630`  
**Companion:** [Implementation plan](../plans/03-fuwforge-plan.md)

## 1. Product direction: a research compiler

The project already has enough agent roles. Its next advantage should come from making each important conclusion reproducible from specific facts and calculations, not from generating a longer debate.

The target architecture is:

```text
Retrieved bytes -> typed, dated facts -> deterministic calculations
-> source-linked claims -> independent challenge -> assembled report
-> reviewed dashboard packet
```

A model extracts candidate facts, proposes interpretations and challenges assumptions. Code owns arithmetic, units, dates, required evidence and version identity. A human decides which thesis assumptions to adopt. This division makes provider improvements useful without making correctness depend entirely on the provider.

## 2. What is already implemented

The current orchestration has nine roles: A1, four parallel core analysts A2-A5, A6B, A6R, A6 and A7. It has machine-checkable contracts, strict handling of missing/defaulted information, evidence retrieval, hashes, provenance, persisted summaries, a controller mode, run verification, screening and run diffs. The older improvement backlog explicitly marks its initial eleven items as implemented. Recommending that backlog verbatim would be stale. [FU-01, FU-02, FU-09](../sources.md)

The public-source collector already bounds document counts and sizes, checks public HTTPS destinations, avoids redirects, uses a SEC contact identity and excludes post-cutoff filing dates. It explicitly warns that current ticker mapping and public adjusted history are not a certified point-in-time archive. Preserve those honest limitations. [FU-03, FU-06](../sources.md)

Deterministic orchestration and deterministic arithmetic do not imply that a live external model will generate byte-identical answers on demand. Archive the exact output for replay and separately identify any fresh model recomputation.

## 3. P0: correct the Beneish calculation before expanding research

### Confirmed arithmetic defect

In `src/utils/finance.py`, the eight-variable helper uses:

```python
+ 4.037 * tata
+ 0.0327 * lvgi
```

The published eight-variable formula uses:

```python
+ 4.679 * tata
- 0.327 * lvgi
```

This is not a stylistic preference or a provider disagreement. Both a coefficient and the sign/magnitude of the leverage term differ from the equation. The formula was checked in the ECB's official Occasional Paper 385, Equation 2, printed page 10, including the rendered page. [FU-07](../sources.md#fu-07) [W-01](../sources.md#w-01)

The standalone test gives all seven ratio indices the value 1 and TATA the value 0.1. The existing expression returns -1.7166; the corrected expression returns -2.0121. This crosses the repository's -1.78 screening threshold. It demonstrates a possible classification impact on synthetic inputs, **not** that a particular company was wrongly classified in an inspected live report.

The helper also supplies plausible default inputs. Remove those defaults: a missing input should become an explicit unavailable result, not a neutral ratio fabricated by a function signature. Classify the unrounded score and round only for display. Record the formula variant, classification threshold policy and the source facts from which each index was computed.

The tested replacement arithmetic is [beneish.py](../reference/beneish.py). It validates finite numeric inputs and rejects implicit boolean/string values. It does not choose a universal threshold or assert that a screen proves manipulation. Applicability and threshold interpretation must remain explicit.

### Scope of the finding

The formula defect is confirmed in the helper. This audit did not establish that every A3 output invokes that helper: the inspected model module's import section does not directly import it, and an empty code-search response is not an authoritative call graph. Trace actual use, then replay affected calculations and mark changed outputs with a calculator version. Do not claim all historical reports are wrong without that impact analysis. [FU-10](../sources.md#fu-10)

**Exit criterion:** independent golden fixtures reproduce the published coefficients, missing inputs cannot manufacture a result, and an impact report identifies which archived outputs actually used the old calculation.

## 4. P0: record the parents an agent consumed, not the parents that happen to exist later

### Confirmed lineage mismatch

The parallel A2-A5 calls receive an envelope snapshot containing A1. All four results are collected before canonical acceptance. During acceptance, however, `env.upstream` is built from the growing `envelopes` dictionary. A3 can therefore record A2 as a parent even though A2 was not part of A3's input; A4 and A5 can gain further false dependencies. [FU-02](../sources.md#fu-02)

This matters beyond documentation. Incremental recomputation, audit reconstruction and prompt debugging will all be wrong if the recorded graph differs from the executed graph.

Add `consumed_upstream` to `AgentExecution`. At execution start, deep-copy or serialize the exact input envelopes, validate their recorded hashes, capture immutable reference values, and construct the actual prompt from that snapshot. At acceptance, seal those captured references rather than rereading the shared state.

```python
# Integration sketch: adapt to the repository's AgentExecution/Envelope types.
input_snapshot = copy.deepcopy(envelopes or {})
consumed = capture_upstream({
    key: value.to_dict() for key, value in input_snapshot.items()
})
# Build AgentContext and the prompt from input_snapshot.
# Persist consumed on AgentExecution before accepting the result.
```

The pure capture/hash utilities are in [execution_identity.py](../reference/execution_identity.py). Capturing metadata alone is not enough: the payload used to construct the prompt must match it, and the prompt hash should reflect the exact rendered request.

Keep canonical acceptance ordering for stable output files. It is a presentation/storage choice, not a reason to invent causal dependencies. Controller-ingested historical envelopes should retain their historical identity; flag legacy lineage rather than silently resealing everything under a new graph.

**Exit criterion:** an orchestration test proves that A2, A3, A4 and A5 each record only the inputs actually supplied, even after the shared accepted-results dictionary changes.

## 5. P0: make evidence portability real

`EvidencePack` requires the raw and extracted source files to exist, verifies their hashes and re-derives the text from the bytes. `verify_run` loads that pack. The repository ignore rules omit cached source bodies and extracted texts from newly tracked run artifacts, while documentation suggests their hashes preserve verifiability without those bodies. For any distributed run whose source files are omitted, the manifest alone cannot satisfy the current verifier. [FU-03, FU-04, FU-05](../sources.md)

A hash can verify supplied bytes. It cannot recreate missing bytes. Refetching a dynamic web page also does not guarantee the same historic response.

Create a portable evidence bundle with an explicit manifest, immutable raw objects, extraction version, extracted text and any attachments required to reproduce the run. Store the bundle outside ordinary source history when size or redistribution restrictions make committing it inappropriate. A private content-addressed store or controlled release artifact is sufficient; a new database is not required.

Give audit results distinct states: `source_bytes_verified`, `source_bytes_unavailable`, `extraction_mismatch`, `semantic_failure` and `untrusted_root`, rather than a single reassuring word. Missing source bytes should remain a verification failure at the source-reconstruction level, not be downgraded into success because an envelope hash still matches.

The reference [evidence_bundle.py](../reference/evidence_bundle.py) verifies a bounded local directory against an **independently trusted** manifest hash. It rejects missing, modified, duplicated, unlisted, escaping or symlinked entries. It is not a secure archive extractor, a signature verifier, or a defense against a hostile process mutating the filesystem concurrently. Those boundaries are stated in the module.

A self-consistent rewritten bundle can carry its own rewritten hashes. Authenticity therefore requires a trusted root outside the attacker-controlled bundle: a previously recorded manifest hash, a verified release record, or a signature whose key is trusted independently. Do not describe a self-hash alone as proof of authorship.

**Exit criterion:** a clean environment can obtain the approved bundle and reconstruct the original source evidence. A missing bundle produces a precise limitation, not an assertion of complete auditability.

## 6. P1: extend verify-run from integrity to recomputation

The current run verifier checks schemas, envelope content checksums, source packs, upstream references and provenance chains. It checks that final reports and artifact bundles exist, but the inspected function does not rerun `validate_contract` or bind their final bytes into a trusted run root. The orchestrator does perform semantic contracts at acceptance; this proposal strengthens the independent audit path, rather than claiming those checks do not exist anywhere. [FU-02, FU-04, FU-08](../sources.md)

Define separate levels:

| Level | What it establishes | What it does not establish |
|---|---|---|
| Envelope integrity | Stored bytes match their recorded hashes | Facts are true |
| Source reconstruction | Recorded raw/text sources can be restored and checked | The model correctly interpreted them |
| Contract and graph validity | Required roles, shapes, consumed parents and gates are coherent | Financial arithmetic is correct unless recomputed |
| Calculation replay | Reported numeric outputs match pinned formulas and inputs | Forward assumptions will occur |
| Claim review | Important assertions have appropriate source support | Guaranteed investment performance |

For a completed run, require the exact expected role set and reject duplicate agent IDs. For a halted run, validate the completed subgraph and explicitly report incompleteness rather than pretending it is a complete research report. Bind the final report, artifact bundle, evidence root, calculator versions, configuration and prompt hashes into a run manifest.

Recompute quantitative artifacts from their typed inputs. Do not validate a score solely by checking that it is a finite number in a permissible range. Store each calculation's formula ID, input fact IDs, parameter values, result and units: a small **calculation witness**.

**Exit criterion:** altering a final report, swapping a period, omitting a completed role, changing a consumed parent or changing a calculator input is caught by the corresponding audit level.

## 7. P1: typed financial facts before better retrieval prose

The evidence excerpt selector already tries to find dense statements rather than sending only filing introductions. That is a useful heuristic, but flattened HTML and generated prose still leave the model responsible for period alignment, units and accounting labels. [FU-03](../sources.md#fu-03)

Introduce a fact ledger whose minimum record includes company identifier, metric/concept, value, unit, currency, period start/end or instant, filing accession, publication/acceptance time, retrieval time, source location, basis, extraction version and supersession relationship. Forecasts must never occupy the same factual category as reported results.

Prefer SEC structured data for suitable standard entity-level facts. The official APIs distinguish submissions and XBRL facts; not every custom-tagged or dimensional fact appears in the convenient company-facts output. Use Arelle for filing-level XBRL/iXBRL processing where the simpler source is insufficient. For unstructured documents, a layout-aware tool such as Docling can preserve useful table structure, but extraction still needs numeric and source validation. [W-08, W-09, W-10](../sources.md)

The collector currently selects annuals, the latest quarter and proxy-type filings from a limited form allowlist. Add a deliberate material-event/amendment policy rather than fetching every possible form indiscriminately. Preserve filing acceptance timestamps for intraday questions; a date-only filter cannot distinguish a morning decision from an evening filing. The current collector already discloses that limitation. [FU-06](../sources.md#fu-06)

For every model-produced quantitative claim, require a fact ID or a calculation witness ID. Generated citations to a whole filing are too coarse for arithmetic that depends on one table cell and one period. An agent should be able to say "unknown" when a necessary input is absent without losing a contest to a more confident hallucination.

**Exit criterion:** a human can click from a ratio to its formula, then to the exact dated inputs and source locations. A unit or period mismatch cannot pass silently.

## 8. P2: checkpoint execution and selective recomputation

In the current core phase, futures are collected before canonical acceptance writes the accepted outputs. A failed sibling can therefore prevent the normal acceptance path from preserving useful completed work. Add an execution receipt for each completed provider call before the overall phase is accepted; distinguish raw completion from validated acceptance. [FU-02](../sources.md#fu-02)

Start with the existing orchestrator plus a local durable journal or SQLite ledger. An execution key should bind provider, exact requested model, prompt content hash, evidence hash, calculator/config versions and consumed-parent hashes. Persist provider usage from actual provider metadata when available; do not treat model-reported token counts as measurement.

A retry can repeat a paid external call if the previous call succeeded but its receipt was not stored. Local idempotency avoids reusing the wrong result but cannot guarantee exactly-once billing by an external service. Record uncertain completions and reconcile them instead of promising impossible execution semantics.

Use explicit states such as `pending`, `running`, `returned`, `validated`, `failed` and `unknown_completion`. Resume validated matching nodes without another provider call. A new fact or prompt version invalidates the affected node and its true descendants, not every role merely because acceptance order changed.

LangGraph is a reasonable later option when persistent human pauses, branching and replay become central. Its checkpointer is not magic determinism; in-memory checkpoints do not survive a process restart, and external effects still need disciplined handling. Adopt it only when these requirements justify replacing the smaller journal. [W-13](../sources.md#w-13)

**Exit criterion:** killing the process after one successful core agent and resuming preserves that matching accepted result without paying for it again, while changed evidence correctly invalidates dependent work.

## 9. P2: independent challenge before rebuttal

The existing bull-then-bear sequence is suitable for explicit rebuttals, but the bear role is necessarily exposed to the bull framing. Test an independent bear hypothesis pass before the existing rebuttal stage. Preserve both the initial disagreement and the later reconciled answer.

Each side should name the fact or assumption whose change would most alter its conclusion. The synthesizer should resolve source contradictions and arithmetic disagreements before summarizing sentiment. Shared use of the same erroneous source is not independent confirmation merely because two roles agree.

Do not add an unlimited debate loop. Give challenge a fixed evidence and cost budget, and compare it with the existing flow on a frozen set of cases. Promote it only if it catches additional material errors at acceptable cost. Agreement rate alone is not the metric.

## 10. P2: evaluate the things that would actually mislead the user

Build a frozen offline evaluation set with amended filings, conflicting units, quarter-versus-year substitutions, share-count basis mismatches, missing sources, injected instructions inside evidence, calculator edge cases and a plausible but unsupported forward claim. Score deterministic facts and calculations against curated answers. Human review should handle a sampled set of qualitative source-support questions; a model judge should not be the only ground truth.

Inspect AI provides a task/solver/scorer structure and evaluation logs. It is a useful optional harness once the cases and scoring rules exist, not a replacement for defining them. Property-based tests can extend the deterministic numeric and schema boundary checks. [W-14, W-05](../sources.md)

Historical evaluation needs another warning: filtering retrieved documents to an old cutoff does not erase later events that a model may remember from training. Treat historical research replay primarily as a source-faithfulness test; do not infer investable predictive performance merely from accurate retrospective narratives. Entity-masked cases and evidence-only checks can expose some leakage, but prospective archived decisions are the cleaner test of a forward-looking claim. This is a methodological inference, not an accusation about a particular model run.

For research budget allocation, begin with a transparent heuristic: rank missing inputs by whether they can change a thesis rule or scenario, then by retrieval cost. Call it "review priority" until probabilities and utilities are calibrated; do not relabel a hand-tuned score as formal expected value of information.

**Exit criterion:** the evaluation report separates unsupported-claim rate, arithmetic correctness, missingness handling, cost, latency and reviewer effort. It does not hide a numerical failure inside a high overall writing-quality score.

## 11. Release sequence

Correct the financial formula and consumed-input lineage first. Add portable evidence and independent semantic/calculation auditing next. Then introduce typed facts and the reviewed packet export. Only after those foundations are stable should checkpointing, selective reruns, independent challenge and research-priority routing change the default workflow.

Keep old artifacts immutable and identifiable. Method corrections should create new runs with clear links to prior versions. The goal is not an audit log that looks impressive; it is the ability to explain, reproduce and challenge every material step from a source document to a decision-facing claim.
