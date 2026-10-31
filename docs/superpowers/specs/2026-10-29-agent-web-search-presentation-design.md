# Agent Web Search and Report Presentation Design

**Status:** Proposed, not implemented

**Date:** 2026-10-29

**Scope:** Conditional research-time web search and successful stock-report delivery

## Goal

Keep the existing research pipeline as the default. A current agent continues through normal completion and validation without a new financial-verification Search or an extra planning or completion call when its existing validation passes and the agent reports no concrete problem. Passing schema, checksum, contract, citation, and coverage checks establishes that the envelope is valid under those rules; it does not prove that every financial value is factually correct.

When an existing validator identifies missing, conflicting, malformed, or suspect data, or when the current agent identifies a concrete problem that public evidence could resolve, allow one bounded, targeted Gemini Search for that current agent. This applies to any real analysis provider when Gemini credentials are configured and the user did not explicitly pass `--no-web-search`. If credentials are unavailable, record `not_performed`; do not claim the investigation passed. Cache and validate retrieved source bytes before using them. The agent may then receive at most one additional completion using the new evidence. This exception path may improve only the current agent's unaccepted result. It must preserve accepted work, must not rerun A1-A7 automatically, and must stop and show a conflict when the new evidence would change accepted upstream core data.

Evidence-pack hash or parse failures can occur during preflight before an agent exists. The proposed exception may make one bounded diagnostic Gemini Search from safe, known metadata to help explain or locate a source, but it never trusts corrupt content, mutates the existing snapshot, or treats a Search result as a financial fact. The existing integrity failure remains visible and halts until the normal integrity rules are satisfied; an automatic whole-run restart is out of scope.

The existing shared Gemini news/research intake remains unchanged. After a successful report, the requested self-contained HTML presentation also makes a separate, best-effort Gemini branding Search for colors and visual cues. That branding request is a distinct cost even when no financial problem triggered research-time Search.

## Non-goals

- Making financial Search run on every real run or every agent.
- Treating a passing validator as proof that all financial claims are true.
- Treating Search prose, a reachable URL, or a citation as financial truth.
- Replacing existing SEC facts, market data, typed-fact binding, calculation replay, or source-backed gates.
- Adding a planning call before normal agent completion.
- Automatically rewriting accepted envelopes or rerunning the full pipeline after a conflict.
- Adding a browser dependency, vector database, search service, background queue, or new runtime package.
- Assuming changes from the still-pending [MELI reliability plan](../plans/2026-10-28-meli-runtime-reliability.md) are implemented.

## Current Constraints

- The CLI currently makes one shared Gemini Search/URL Context intake before A1 when a Gemini analysis run uses the default web-research setting. It disables that intake on resume and rejects `--web-search` for other analysis providers (`src/controller.py:176-208,1041-1046`).
- The existing intake is pre-A1 and shared, not agent-specific. It accepts only structured citation URLs, downloads and hashes public pages, records the provider interaction, and excludes undated or unsafe news pages (`src/web_research.py:140-263`). Its news path uses HTML and a 90-day window. The conditional path must not remove, replace, or misrepresent this behavior.
- The orchestrator snapshots the evidence pack before A1 (`src/orchestrator.py:212-244`). A1 emits the frozen checksum propagated to later agents (`src/orchestrator.py:344-378`; `src/agents/model.py:1609-1612`). That shared snapshot remains immutable.
- `EvidencePack` loading verifies document and attachment checksums and rejects malformed typed attachments before agents run (`src/evidence.py:479-502`; `src/evidence.py:538-612`). A preflight failure therefore has no current agent, partial draft, or safe corrupt source text to send to a model.
- A2-A5 run in parallel from the same frozen A1 inputs, and lineage is captured before sibling results are accepted (`src/orchestrator.py:384-445`; `src/execution_identity.py:56-76`).
- An execution key binds provider, model, request, shared evidence, and consumed parents (`src/orchestrator.py:718-733`; `src/execution_identity.py:79-124`). Only validated, checksum-matching receipts are reusable (`src/execution_store.py:185-197,484-525`).
- Existing acceptance checks run schema, checksum, required-artifact, gate, and contract validation (`src/orchestrator.py:899-1025`). A parseable response that fails one of these checks is the validation trigger; malformed JSON and transport failures continue through the existing repair/recovery rules and do not become web-searchable by default.
- Markdown and artifacts are written before provisional and final manifests. The manifest binds final output hashes, but its current discovery list does not include HTML (`src/orchestrator.py:1120-1230`; `src/run_semantic_audit.py:274-426`).
- Mock and controller modes make no Gemini Search calls. Other analysis providers continue to reject explicit `--web-search` for the shared pre-A1 intake. The current error claiming those providers "never incur Gemini research charges" must change only to describe that shared-intake rule because an explicitly triggered investigation or branding lookup may use separately configured Gemini access.
- Downloaded web text is not automatically a typed fact, and no existing component proves that arbitrary prose supports, contradicts, or semantically agrees with another statement.

## Alternatives

| Decision | Alternatives | Recommendation and tradeoff |
| --- | --- | --- |
| Research-time Search cadence | Existing shared intake only; an additional financial cross-check on every run; validator/problem-triggered targeted Search | Keep the existing shared intake and add targeted Search only after a concrete trigger. A default extra cross-check spends money without a specific question, while shared-only research cannot investigate a validator or agent finding. |
| Agent integration | Separate issue-planning call; normal current completion with a structured issue signal followed by a bounded Search and optional revision; model-driven tools in the primary completion | Use the current completion and its validation result to decide whether to search. A separate planning call would run even on the clean path. The same conditional runner path works for every real analysis provider; the analysis provider and the separately configured Gemini research backend are distinct. |
| Provider boundary | Restrict triggered Search to Gemini analysis; allow the shared intake for every provider; allow only the triggered/branding Gemini calls for non-Gemini analysis | Keep the shared pre-A1 intake provider-bound and allow the explicitly triggered or branding Gemini call from any real provider when configured. This respects the opt-out without making the analysis backend determine whether a problem can be investigated. |
| New evidence storage | Rewrite the shared evidence pack; mutate evidence during a stage; append-only per-execution bundles | Use append-only per-execution bundles. Rewriting or mutating the pre-A1 pack would invalidate the A1 checksum and accepted receipts, and stage-wide mutation could leak one parallel sibling's evidence into another's supposed input. |
| Preflight corruption | Ignore hash failures; silently replace sources; stop without diagnostics; bounded read-only diagnostic Search | Keep the halt, but allow one safe diagnostic Search that cannot feed or alter the evidence pack. Ignoring or replacing corrupt bytes would undermine every downstream checksum and citation. |
| HTML generation | Model-written freeform HTML; deterministic reusable renderer; browser screenshot | Use a deterministic renderer. Freeform HTML is unsafe and non-repeatable; screenshots need new infrastructure. The renderer is less bespoke but preserves exact report values and can be tested offline. |

## Search Lifecycle

### Existing shared intake

The current shared Gemini news/research intake remains a separate pre-A1 step with its current default, configuration, source limits, attachment trace, and resume behavior. It runs only where current provider rules allow it and supplies cached pages plus coverage limitations to the normal pipeline.

The conditional targeted Search described below is additional only when a trigger occurs. It cannot be counted as part of the shared intake, and the shared intake cannot satisfy a targeted query merely because it previously ran.

### Clean continuation

For each current agent, run its normal completion and the existing schema, checksum, required-artifact, gate, citation, and contract validators.

If validation passes and the current agent reports no concrete problem, accept the result and continue the existing pipeline. Do not make a finance-targeted Gemini Search call, do not add a planning/completion call, and do not insert a new semantic verification step. This is true for every analysis provider: a clean non-Gemini run makes zero additional financial searches. Record enough local status to show that the conditional path was not triggered.

This clean path means that no specific issue was identified under the current checks and available context. It must not be reported as proof that every value is factually correct.

### Trigger

A targeted Search may be considered only when at least one of these provides a concrete, searchable issue:

1. An existing validator reports missing, conflicting, malformed, or suspect data tied to a named field, period, entity, unit, or citation. A malformed-JSON or transport error remains on the existing repair path; it qualifies here only when a parseable data-quality defect could plausibly be resolved with an external source.
2. The current agent returns a structured `search_request` alongside its draft instead of silently guessing. The request names the concrete problem, the fields it could change, the source type or authority sought, and one or more bounded query intents. Free-text uncertainty without a specific issue does not trigger Search.
3. The runner detects a new comparison failure while validating the current result against already cached, mechanically comparable evidence. Unresolved or noncomparable differences remain visible and continue through existing gates.

The trigger is scoped to the current agent's unaccepted result. A previously accepted upstream issue remains governed by existing gates and immutable lineage. The runner may decide not to call Search when the issue is not plausibly searchable, search is disabled, the evidence pack is full, or the request exceeds the one-round limit; it records the reason without claiming that verification occurred.

The trigger is not limited to a Gemini analysis backend. For any real provider, the runner may call the separately configured Gemini research backend when a concrete issue exists. Missing Gemini credentials produce `not_performed`; the current agent's existing validation and gates still decide whether it can continue. The shared pre-A1 intake is not started for a non-Gemini analysis run.

### Preflight evidence corruption

If evidence loading fails a document or attachment hash check, malformed typed-attachment check, or another integrity check before A1, normal preflight halts under the existing rules. A proposed read-only diagnostic exception may make at most one targeted Gemini Search when:

- Gemini credentials are configured and the user did not pass `--no-web-search`;
- the run is real rather than mock or controller mode;
- safe metadata is available, such as user-supplied ticker, company, and analysis date or independently trusted run metadata;
- the diagnostic query can avoid publishing or relying on corrupt content.

The diagnostic bundle records the failure category, safe query metadata, provider response, accepted public candidates, hashes, and coverage gaps. It must not send unchecked text from a corrupt document or attachment to Gemini, update `evidence/evidence.json`, repair bytes in place, accept a replacement source, or create a revised agent completion. Diagnostic Search prose and candidates are not financial evidence.

A candidate may be described as byte-identical to a trusted recorded digest only when the digest comes from trusted metadata and the downloaded bytes independently reproduce it. Even then, the design does not replace the existing snapshot or resume the run automatically. If corruption remains, halt/show it. Recovery requires the existing integrity rules plus an explicit operator action, a fresh verified evidence pack, or a deliberately requested fresh run.

### Targeted exchange

When a valid trigger occurs:

1. Preserve the normal completion as a draft; do not accept or seal it.
2. Make at most one targeted Gemini Search interaction using the separately configured Gemini backend and no automatic paid retries, regardless of which real provider produced the current draft. If Gemini credentials are unavailable, write `not_performed` and return to existing validation without pretending a search occurred.
3. Resolve only citation URLs returned by the interaction. Download public source bytes through the existing safety and extraction path. Write an append-only execution bundle containing the query rationale, provider/model, retrieval timestamps, usage when returned, accepted and excluded sources, raw/text hashes, and validation outcomes.
4. Validate URL, public-host, content type, size, availability on or before the analysis cutoff, raw and text hashes, and any exact quote used for a comparison. Search prose is not supplied as financial evidence.
5. If a relevant source passes validation, make at most one additional completion of the same current agent using the draft and the validated source text.
6. Run the same existing validators on the revised result. If the agent does not need a revision because no usable or relevant source was found, return the draft to the existing validation path with a visible coverage gap.

This exception spends at most one targeted Search interaction and at most one additional current-agent completion per triggered execution. A second request from the same execution is refused as `round_limit_reached`. Existing ordinary repair or recovery behavior remains visible in usage and provenance; the targeted limit does not conceal those calls.

An agent can use new evidence only in its own unaccepted result. Accepted A1-A7 envelopes remain immutable. No triggered search automatically starts another agent, invalidates siblings, or reruns A1-A7.

## Comparison and Authority

Source-bound typed values use the existing exact comparison policy, with no blanket numeric tolerance. Compare only identical entity or security, concept, period, units and currency scale, accounting basis, and compatible date or price adjustment.

For this design, a **core mismatch** is a conflicting matched value for company/ticker identity, price date or value, share count, revenue, income, or operating cash flow. Other differences are visibly proposed concerns, not automatically certified facts.

Prefer original SEC filings and issuer disclosures for reported financials. Price checks require the existing retrieved market-price evidence with a comparable date and basis plus an independently sourced comparable observation. A secondary-source disagreement is an investigation candidate, not proof that SEC or issuer data is wrong. Differences may reflect timestamps, restatements, adjustments, or accounting scope. An unclear or model-proposed mismatch remains `unresolved`.

Only a verified, applicable source can sustain a proposed contradiction. A model verdict alone never establishes financial truth or chooses a winner. If verified evidence would change an already accepted upstream core input, stop with `search_conflict` and show the source, target field, quote, period, unit, currency, and basis. Do not silently rewrite prior work.

## Downstream Visibility

An accepted envelope records the checksum of the execution-local search bundle it actually consumed in its existing extensible metadata or artifact area. Future snapshots include accepted upstream search source text and a compact status summary. Downstream agents can therefore inspect accepted upstream search evidence.

A2-A5 are parallel siblings. They may all start from accepted A1, but one sibling's search must not appear as another sibling's parent or prompt input during that parallel phase. Accepted search evidence becomes visible only to later executions that actually consume that envelope.

Strict citation validation uses an execution-local allowlist made from the frozen shared pack, the current execution's accepted search bundle, and accepted upstream search bundles. All web content remains inside the existing untrusted-source boundary.

## Evidence Storage and Identity

Use a small contained directory rather than a new database:

```text
runs/<run>/
  web_research/             # existing shared intake
  targeted_search/
    <execution-id>/
      search_manifest.json
      S001.bytes
      S001.txt
  corruption_diagnostics/
    <diagnostic-id>/
      search_manifest.json
      S001.bytes
      S001.txt
  evidence/                 # existing frozen shared pack
  TICKER_DATE_report.md
  TICKER_DATE_report.html
```

The existing shared pack and its attachments remain unchanged after its pre-A1 snapshot. A triggered agent uses its own append-only bundle. A preflight-corruption diagnostic uses a separate append-only directory and never enters the execution-local citation allowlist. Reuse the existing evidence record fields for source ID, URL, title, publication date and basis, retrieval time, content type, raw/text SHA-256, relative paths, and verification state (`src/evidence.py:260-268`). The manifest also records trigger evidence, provider/model, query rationale and intents, timestamps, request count, usage as returned, accepted and excluded sources, comparison outcomes, and the optional revision. It contains no credentials, headers, arbitrary exception bodies, corrupt source text, or executable Search HTML.

Financial sources may use the existing bounded HTML/text/XML/JSON/PDF extraction in `src/evidence.py:199-224`; do not add another PDF dependency. The existing shared news window applies to the shared intake. A targeted financial source is not limited to that news window, but it must have defensible availability on or before the analysis cutoff. A future publication date, ambiguous date, unsupported format, unsafe host, failed extraction, or failed hash check is `unresolved`.

The exact targeted-bundle SHA-256 must affect the current execution identity. A compatible minimal implementation can combine the frozen shared-pack checksum and the bundle checksum in the existing `evidence_sha256` slot. This preserves:

```text
same prompt + same parents + same shared evidence + same targeted bundle
    => same execution key
```

A builder may add explicit fields if the current schema already supports them. Legacy no-search receipts must remain verifiable under their existing behavior. Persist the targeted digest in receipt metadata or another existing compatible receipt field.

On resume:

- reuse a validated receipt without repeating Search or analysis;
- reuse a completed, verified targeted bundle when only the current agent's revision remains;
- never automatically repeat Search with uncertain completion; use the existing explicit recovery choice;
- preserve `disabled_by_user`, `not_performed`, `no_accepted_sources`, and unresolved states instead of converting them into success;
- reuse a completed corruption diagnostic for reporting and operator review, never as accepted financial evidence or an implicit integrity repair;
- if the shared intake is disabled on resume, retain the already snapshotted shared evidence and do not make a new default Search call.

## Conflict and Failure Behavior

- Clean validation plus no concrete issue: accept and continue without new financial Search or an extra planning/completion call.
- Search disabled: make no shared, targeted, or branding Gemini Search call. Record `disabled_by_user`; existing SEC and market auto-source collection remains available.
- Mock or controller mode: preserve zero Gemini Search calls.
- Gemini credentials unavailable for a triggered current-agent investigation: record `not_performed`; do not claim verification. Existing validation and gates decide whether the current result can continue.
- Evidence preflight corruption: make no automatic repair or whole-run restart. A bounded diagnostic may use only safe metadata and must leave the snapshot untouched; otherwise or when its budget/credentials are unavailable, halt/show the existing integrity failure.
- Targeted Search provider/API failure: do not automatically retry. Keep the failure visible, preserve the draft and prior accepted work, and return to existing validation. A specific gate or comparison failure remains unresolved and subject to that gate.
- Search completed with no usable source: continue only when existing validation passes and the coverage gap remains visible. Search availability or absence never fabricates, certifies, or silently drops the issue.
- A second request in the same execution: refuse with `round_limit_reached`.
- A concrete issue in the current agent's own unaccepted detail: the optional revision may resolve it using newly validated evidence.
- A verified comparable mismatch that would change accepted upstream core data: halt with `search_conflict`, preserve both envelopes and the evidence bundle, and do not restart automatically.
- A noncomparable difference: classify as `unresolved`; it cannot overwrite accepted data or halt as a confirmed contradiction.
- A statement not bound to a compatible typed fact or exact source comparison remains model-proposed and must be disclosed as such.

An operator review path may later record that a conflict was noncomparable or invalid, bind that decision to the conflicting source hashes, and resume only the interrupted current execution from cached evidence. That command does not exist today. If a valid correction would change earlier accepted data, the user decides whether to start a fresh run.

## Presentation

After A7 succeeds and the existing Markdown report and artifact bundle are assembled:

1. Build a presentation model from the exact report text and accepted artifacts.
2. Make one best-effort Gemini brand-search request whenever report HTML generation is requested, Gemini is configured, and the user did not explicitly pass `--no-web-search`. This lookup can run after a clean non-Gemini analysis and occurs independently of whether a financial problem triggered agent-time Search.
3. Cache any usable citation/source metadata and select colors.
4. Validate CSS color syntax and derive readable foregrounds.
5. Render one standalone HTML file with inline CSS and no required remote assets.
6. Include it in final artifact discovery and the final manifest. Keep the existing provisional/final manifest sequence; do not reseal the Markdown report merely to add presentation.

Branding Search is a distinct post-report cost, including for non-Gemini analysis runs. It cannot alter financial text. Missing credentials, `--no-web-search`, mock, or controller mode uses local fallback styling. The HTML should include:

- ticker, company, analysis date, report status, and review warning;
- recommendation and overall scorecard score when present;
- current price and date, market capitalization, and selected sealed comparison fields;
- concise thesis and bounded bull, bear, catalyst, headwind, and risk summaries;
- data gaps, unverified claims, search coverage, and unperformed checks;
- supplied sources with titles, dates, and URLs;
- a visible label for searched, approximate, or fallback styling.

Official colors should be used when a reliable public result identifies them. Because brand selection is explicitly best-effort, approximate colors are acceptable when official values are not reliably available. Label them as approximate and never describe them as a verified official palette. Missing citations, no usable colors, low contrast, provider failure, no Search, or mock/controller mode must fall back to a local accessible palette without failing report delivery.

The HTML must escape all report/model/source text, use semantic landmarks and headings, preserve meaning without color, provide print styles, include a restrictive Content Security Policy, and require no network access. Values must match the sealed report artifacts; absent values remain visibly unavailable.

Brand lookup failure is nonfatal because fallback styling can still produce the presentation. If HTML rendering still fails, leave the Markdown report intact, record and surface `presentation_unavailable`, and do not claim that both files were delivered. A final-manifest failure retains the existing halted or unverified behavior.

## Provider, Opt-out, and Cost Reporting

Preserve current provider behavior:

- Gemini analysis runs keep the existing default shared intake.
- Explicit `--web-search` remains invalid for non-Gemini analysis because it requests the shared pre-A1 intake. The design must revise the old "never incur Gemini research charges" error wording so it states that rule without denying the separately requested triggered or branding calls.
- A concrete current-agent problem may trigger the additional Gemini call for any real analysis provider when separately configured Gemini credentials exist. The conditional path does not start the shared intake for a non-Gemini provider.
- A clean non-Gemini run makes no additional financial Search. Its separate best-effort branding lookup may still run when configured.
- `--no-web-search` disables the existing shared intake, conditional targeted financial Search, preflight corruption diagnostics, and post-report branding Search, and records `disabled_by_user`.
- Mock and controller modes make no Gemini Search calls; explicitly requested SEC or market auto-source collection remains separate.
- `--no-url-context`, `--web-max-sources`, resume restrictions, and the existing Gemini credential/model configuration remain in force unless a later explicit implementation request changes them.

Expected Search call counts are:

| Path | Existing shared intake | Conditional targeted Search | Post-report branding Search |
| --- | ---: | ---: | ---: |
| Gemini run, no financial trigger | Existing current behavior | 0 | At most 1 when HTML output is requested |
| Non-Gemini real run, no financial trigger | 0 | 0 | At most 1 when HTML output is requested |
| Any real provider, concrete searchable trigger with Gemini configured | Current shared-intake rule for that provider | At most 1 per triggered current execution | At most 1 when HTML output is requested |
| Any real provider, concrete trigger with Gemini key unavailable | Current shared-intake rule for that provider | 0; record `not_performed` | 0; styled fallback |
| Preflight evidence corruption with safe metadata | Current shared-intake rule for that provider | At most 1 diagnostic call, never fed to agents | At most 1 only if report generation is otherwise reached |
| `--no-web-search`, mock, or controller | 0 | 0 | 0 |

Show targeted Search request count, trigger, provider/model, actual query/status metadata when returned, accepted and excluded source counts, token usage when supplied, additional completion count, and paid retry count. Use `null` for unknown usage rather than zero. Do not claim a trusted dollar cost unless a versioned pricing table is deliberately introduced. Disclose that accepted-page limits do not hard-cap the provider's autonomous query count.

No new user-facing budget knobs are proposed. The additional cost exists only when a concrete trigger occurs, except for the explicitly requested, separately reported best-effort branding Search. Existing `--no-web-search` remains the strict cost-avoidance choice.

## Audit and Implementation Scope

The final manifest and audit should verify:

- the existing shared intake still follows its current default, opt-out, provider, cutoff, source, attachment, and resume rules;
- no new financial Search or additional completion occurs on the clean path;
- every triggered search has a concrete recorded trigger and stays within the one-Search and one-revision limits;
- every preflight-corruption diagnostic uses only safe metadata, leaves the evidence snapshot untouched, and remains separate from agent evidence;
- each targeted manifest and source byte/text file exists and matches its hashes;
- URL, date, publication metadata, containment, source IDs, cutoff availability, and extraction are valid;
- the execution identity covers the exact frozen-shared-plus-targeted evidence digest;
- citations resolve only to allowed shared, current, or accepted-upstream sources;
- A2-A5 isolation is preserved and no fabricated sibling parent appears;
- conflicts, search failures, and unperformed checks remain visible;
- the HTML, brand record, report, artifacts, and summary match final manifest hashes, and post-seal edits fail.

Likely files are `src/web_research.py`, `src/evidence.py`, `src/agents/model.py`, `src/orchestrator.py`, `src/execution_identity.py`, `src/run_semantic_audit.py`, `src/controller.py`, and a new focused `src/presentation.py`. Existing schemas can be extended only where necessary; no identity or manifest schema replacement is required by this design.

## Non-browser Verification

Use offline fakes and synthetic packs:

- existing shared intake: retain the current default, `--no-url-context`, source caps, attachment hashes, `--no-web-search`, explicit other-provider rejection, and resume behavior;
- clean path: valid result with no issue causes zero targeted Search calls and zero extra completions;
- provider boundary: a clean non-Gemini run makes zero financial searches; a triggered non-Gemini run may make exactly one separately configured Gemini call; a missing key records `not_performed`, makes no paid attempt, and preserves the existing validation/gate outcome; explicit `--no-web-search` and mock/controller remain zero-call paths;
- triggers: validator-detected missing or conflicting data, parseable malformed or suspect data, a current-agent concrete issue, and an unresolved comparison each produce one bounded attempt only when searchable;
- current-agent flow: at most one targeted Search plus at most one revision, second-round refusal, and no A1-A7 rerun;
- provenance: altered raw/text/manifest bytes, unsafe URL, future or ambiguous date, non-allowlisted citation, changed targeted digest, and legacy no-search resume;
- preflight corruption: a hash-mismatched evidence byte triggers at most one diagnostic query containing only safe metadata, never the corrupt bytes; the source and snapshot bytes remain unchanged, no agent/revision starts, and the run halts unless the existing integrity rules are explicitly satisfied by a separate recovery path;
- parallelism: A2-A5 isolation, downstream visibility after acceptance, and immutable accepted upstream envelopes;
- conflicts: current-detail revision allowed, accepted-core change halts visibly, and noncomparable evidence remains unresolved;
- comparisons: exact matching across entity, concept, period, unit, basis, date, and adjustment, with no default tolerance;
- failures: Search unavailable or failed, no accepted source, disabled Search, uncertain completion, reviewed-conflict resume, and preservation of prior accepted work;
- presentation: branding Search called independently of a financial trigger and available after a clean non-Gemini run when configured, deterministic HTML, escaped hostile content, no external runtime assets, CSP, report-value equality, approximate-brand label, fallback behavior, and Markdown preservation when HTML fails;
- final integrity: manifest verification before and after mutation of Markdown, HTML, artifacts, summary, branding records, and targeted-search files.

Suggested focused commands after implementation:

```text
.venv/bin/python -m pytest -q tests/test_web_research.py tests/test_execution_identity.py tests/test_execution_resume.py
.venv/bin/python -m pytest -q tests/test_automated_pipeline.py tests/test_run_semantic_audit.py tests/test_cli_smoke_end_to_end.py tests/test_presentation.py
git diff --check
```

No browser test is included or proposed as complete verification for visual readability.

## Current Limitations

- The existing shared Search is one provider-coupled pre-A1 intake, not a financial cross-check and not a guarantee that every value is correct.
- There is currently no validator/problem-triggered agent Search, optional evidence-driven revision, targeted-search bundle, or HTML presentation.
- There is currently no preflight corruption diagnostic; integrity failures halt before an agent exists, as they should.
- Downloading source bytes does not prove arbitrary prose support or contradiction and does not create typed facts.
- Search cannot provide a certified point-in-time archive, prove issuer truth, or access paywalled/private sources.
- Static layout tests leave a visual rendering and accessibility review gap under the no-browser rule.
