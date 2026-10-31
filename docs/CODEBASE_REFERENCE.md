# FUWForge Codebase Reference

Deep reference for working on FUWForge. Written 2026-10-30 from a full read of `src/`, `config/`, `schemas/`, `evals/`, and the test suite, on branch `p0-baseline-preserve-verification` (HEAD `33322d6`). Line numbers are approximate anchors as of that commit; re-check before relying on them.

Companion docs: `CLAUDE.md` (commands + short architecture), `architecture.md` (file map), `docs/audit-levels.md`, `docs/fact-binding-policy.md`, `docs/sec-availability-policy.md`, `docs/contracts/A*.md`. **Where those docs disagree with code, see §9 — code is authoritative.** `HANDOFF.md` (2026-10-08) predates the P1–P4 commits; parts of its "Known limits" are resolved (e.g. witness inputs *are* now value-bound).

---

## 0. Current state of work (as of 2026-10-30)

- **Proposals, NOT implemented:**
  - `docs/superpowers/plans/2026-10-28-meli-runtime-reliability.md` — 5 tasks: (1) tolerate an oversized *older* annual filing as a recorded omission; (2) daily price = previous completed US/Eastern bar, date-only timestamp, A1 price bound to market evidence; (3) canonical, non-mixed-vintage revenue/cost/GP table from typed facts; (4) deterministic coverage warning near report top, keep `verify-run` labels; (5) investigate MELI SG&A (Beneish) without inventing a value.
  - `docs/superpowers/specs/2026-10-29-agent-web-search-presentation-design.md` — validator/agent-triggered *targeted* Gemini Search (≤1 search + ≤1 revision per execution, append-only `targeted_search/<execution-id>/` bundles folded into execution identity), read-only preflight-corruption diagnostic search, and a deterministic standalone HTML report (`src/presentation.py`, new) with best-effort branding search. `--no-web-search` disables all of it.
- **Fix plan for §9:** `docs/superpowers/plans/2026-10-30-hardening-and-cleanup.md` (phased; also sequences the two proposals above).
- **Recent runs (untracked):** `runs/meli_deep_20261028/` (collection only — `retrieved_inputs/omissions.json`), `runs/meli_deep_bounded_20261028/` (Gemini `gemini-3.8-flash`, strict, completed; `audit_source_backed.json` **fails** `calculation_replay`: Beneish unavailable because `sga` has no comparable facts for both periods), `runs/bbai_deep_20261029/` (Gemini, strict, completed; not yet audited).

---

## 1. Run lifecycle (`src/orchestrator.py`)

### Construction — `FUWForgeOrchestrator.__init__` (~:108)
- Loads `depth_modes.yaml`, `focus_routing.yaml`, `orchestrator.yaml` (`max_retries=3`, `retry_backoff_seconds=15`) via `src/resources.py`.
- `strict = arg or env FUWFORGE_STRICT`. Registry = `default_registry(provider, model, randomize, max_retries, backoff, strict=True if strict else None)`; a `registry=` kwarg overrides (tests use this). `CodexOrchestrator` is a legacy alias.

### `run()` (~:158)
1. **Target dir:** `output_root` if given; else `run_id` must be a plain identifier (no abs path, `..`, `.`, empty) under `get_default_runs_dir()`.
2. **Archive protection** (~:194): automated mode without resume + existing `run_summary.json` or `*_envelope.json` → `FileExistsError` *before* the try block.
3. **Read-only preflight:** validate evidence identity, strict evidence, recovery choice, and receipt checksums before modifying a run. Evidence, strict-evidence, and unresolved-receipt preflight refusals leave existing run bytes unchanged. Failures after agent dispatch write a halted summary and manifest.
4. **Evidence snapshot** (~:212-244): `EvidencePack(path, ticker, analysis_date)`. If `<run>/evidence/` exists (resume) → reload and re-verify; different checksum → refuse. Else `pack.snapshot(<run>/evidence)`. Strict + automated + non-mock + no pack → `ValueError` ("Strict model analysis requires --evidence-pack").
5. **Durable store** (all modes except controller): preflight inspects a private copy of the ledger and its WAL without changing the original. After preflight, `ExecutionStore(<run>/execution_ledger.db)` reconciles running/returned receipts; uncertain completions require `--recovery-choice` before changes. Concurrent resume writers are not supported.
6. **`_run_pipeline`** (~:311): validates depth key; `_resolve_focus` (unknown tags silently dropped); opens `<T>_<D>_provenance.log` (appends on resume).

### Phases
| Phase | Agents | Notes |
|---|---|---|
| 1 | A1 | `frozen_checksum = a1.metrics.data_checksum` (missing → error). Gate fail → writes `<T>_<D>_missing_data.md`, halts. |
| 2 | A2–A5 | Automated: `ThreadPoolExecutor(4)`, each gets a deep copy of envelopes (A1 only). Results via `as_completed`; **acceptance is sequential in A2→A5 order**. A failing sibling propagates but the pool waits for the other paid calls (their receipts stay `returned`). Controller mode: sequential. |
| 3a | A6B | upstream A1–A5 |
| 3b | A6R | Normal: one call with A1–A6B. **`--independent-bear`**: blind pass with A1–A5 only (`challenge_initial.phase="independent"`), accepted to `challenge_initial/a6r_*.json`; then reconciliation pass with full upstream + the sealed blind envelope (in request hash). Runner injects `artifacts.initial_bear_theses` and `artifacts._independent_bear_reconciliation` (`extra_calls_max:1`, `promotion_status:"experimental_pending_cost_and_review_evaluation"`) before acceptance. Reconciled envelope's `upstream` does **not** list the blind pass. |
| 3c | A6 | CIO synthesis |
| 4 | A7 | then `_write_outputs` → manifest → summary → manifest again |

### `_run_agent` (~:671) — freeze inputs and identity
- `snapshot = deepcopy(envelopes)`; `refs = capture_upstream(...)` (sorted `UpstreamRef(agent, content_checksum)`; unsealed parent raises; controller mode `()`).
- `ledger = pack.get_facts(as_of=analysis_date)`; `forensic_inputs_for(ledger)`.
- `AgentContext`: params copy, depth/focus, frozen checksum, envelopes snapshot, `source_documents`, `source_text = pack.prompt_text()`, evidence checksum, fact ledger, forensic inputs, `challenge_initial`.
- Controller mode: read `<run>/<id>_envelope.json`, return without calling anything.
- **Request hash** = sha256(sorted JSON of {agent, prompt, params, depth, focus, strict, flow config, `CALCULATOR_VERSION`, code hash of `inspect.getsourcefile(type(agent))`, randomize, challenge_initial, schema sha}). `execution_key(provider, model, request_hash, evidence_hash, refs)` (`execution_identity.py:79`).
- **Resume branch** (~:734): reusable receipt (validated, checksum matches stored envelope) → `reused=True`, zero latency, original usage. `abandon` → raise. Saved `raw_response` → `_recover_saved_response` (re-parse locally, no provider call). `unknown_completion` → requires `retry`. A **FAILED** receipt whose local recovery fails falls through and re-dispatches (paid) without an operator choice.
- **Dispatch:** `store.begin` → `on_provider_return` = `store.mark_returned` (before parsing) → `on_repair_start` = `store.begin_repair` → `agent.run`. Exception: state RETURNED → `mark_failed`, else → `mark_unknown_completion`.

### `_accept_agent` (~:899)
Order: schema (`EnvelopeValidator`) → `validate_checksum(env, frozen)` (non-A1) → `REQUIRED_ARTIFACTS` → `validate_gate_passed` → `validate_contract(envelopes=input_snapshot, strict, fact_ledger, expected_company=ticker, cutoff=analysis_date)`.
- Contract warnings appended to `env.warnings` **only when not reused** (reused → provenance only; keeps checksum stable — locked by `test_reused_envelope_checksum_is_stable_across_resumes`).
- Seal (not controller, not reused): set `env.upstream`, add `artifacts._execution_lineage` (consumed ids, execution key, upstream digests, prompt/request/evidence sha, provider, model) **after** contract validation, then `content_checksum = envelope_content_checksum(env)`, write `a*_envelope.json` + `a*_raw.json`, `store.mark_validated`.
- On validation failure: `mark_failed`, unsealed envelope/raw still written (non-controller), re-raise.

### `_write_outputs` (~:1119)
- `<T>_<D>_report.md` with YAML front matter (run_id, ticker, analysis_date, depth, provider, model|"not-applicable", focus, weights, frozen_checksum, generated_at) and notices: non-mock Beneish/Altman "screening signals"; mock "**MOCK PROVIDER — SYNTHETIC OUTPUT**" (must precede `# Section 0:`); controller "**CONTROLLER-ASSEMBLED — envelopes supplied by operator**"; "**NOT VERIFIED RESEARCH**" if injected defaults or `source_grounding=="unverified"`, else "Evidence supplied"; "Incomplete research coverage" + "## Research data gaps" table from `metrics.missing_data`.
- `_assemble_sections` (~:1232): merges all `sections_md`; same key with different text → error. Order: `SECTION_n` numeric, `AI_STRATEGY` right after Section 3, everything else (incl. `RAW_DATA_PACK` → "### Raw Data Intake") last; MISSING_DATA_TABLEs → "## Per-agent coverage diagnostics"; `depth.skip_sections` dropped (Quick skips 13, 17, 18).
- `<T>_<D>_artifacts.json`, `run_manifest.json`, `run_summary.json` (run_id, status, halt_reason, provider, model, strict, flow_mode, params, checksum, evidence_checksum, metrics, outputs, timestamps).

### Files in a run dir
`retrieved_inputs/` (auto-sources) · `web_research/` (Gemini intake) · `evidence/` (snapshot + `evidence.json`) · `execution_ledger.db` · `<T>_<D>_provenance.log` · `{a1,a2,a3,a4,a5,a6b,a6r,a6,a7}_{envelope,raw}.json` · `challenge_initial/` · `<T>_<D>_missing_data.md` · `<T>_<D>_report.md` · `<T>_<D>_artifacts.json` · `run_summary.json` · `run_manifest.json` · (operator-written) `audit_*.json`.

---

## 2. CLI (`src/controller.py`, `main` ~:1263; console script `fuwforge`)

No args + TTY → wizard; else help, exit 2.

Shared run args (`_add_run_arguments`): `--input --run-id --company --ticker --analysis-date --depth --focus* --weights --strict`. `_build_params` precedence: CLI flags > `--input` YAML/JSON (unwraps `inputs:`) > `run_dir/input.yaml` > params from first `*_envelope.json`. Defaults: company=ticker, date=today UTC, depth=Standard. **`--strict` is passed explicitly; `FUWFORGE_STRICT` remains an input default and is not modified by the CLI.**

| Command | Calls | Key behavior |
|---|---|---|
| `validate <env> [--expected-checksum]` | schema + `validate_checksum` | |
| `assemble <run_dir>` | `orchestrator.run(mode="controller")` | report, summary and manifest identify operator-supplied envelopes as `controller` / `not-applicable` |
| `run [run_dir]` | `cmd_run` (~:166) | `--provider {mock*, agy, opencode(-go/_go), openai, gemini}`, `--model`, `--random-data`, `--allow-defaults`, `--resume`, `--recovery-choice retry\|abandon`, `--independent-bear`, `--evidence-pack` ⊕ `--auto-sources`, `--cik`, `--web-search` ⊕ `--no-web-search`, `--web-max-sources 1-6 (4)`, `--no-url-context` |
| `fetch-sources --manifest --out` | `evidence.fetch_evidence` | |
| `collect-sources --ticker --analysis-date --depth --cik --out` | `public_data.collect_public_sources` | |
| `verify-run <dir> [--strict --require-source-backed --integrity-only --json]` | `run_audit.verify_run` or `run_semantic_audit` | exit 1 on errors or `overall_state=="incomplete"` |
| `seal <dir> [--force]` | `cmd_seal` | anchors envelope roles and final-output paths/digests to a readable manifest; rejects cycles; records explicit forced overrides in `resealed`; leaves a no-op unchanged |
| `screen runs... [--format markdown\|csv --sort --out]` | `_load_a7` | synthesizes comparison fields with fixed IRR 10/-5 if missing |
| `diff a b` | | scorecard/comparison deltas + provenance set diff |
| `monitor <dir> --doc [--provider auto\|jev\|gemini\|opencode\|mock --threshold 0.75]` | `sentry.evaluate_document` | exit 1 if rerun required; `auto` exists only here |
| `bundle-evidence`, `verify-evidence`, `restore-evidence` | `evidence_bundle` | |
| `export-packet <dir> --output [--strict]` | `research_packet.export_packet` | strict requires audit state verified |
| `wizard` / `guided` / `interactive` | `wizard.run_wizard` | |

**`cmd_run` rules:** non-mock without `--allow-defaults` → strict; `--strict` beats `--allow-defaults`. Web search defaults **on** for `gemini`; `--web-search` with another provider errors; web search needs a pack or `--auto-sources`. Existing run without `--resume` → `FileExistsError` before any paid call. `--resume` needs a run dir or `--run-id` whose directory holds `execution_ledger.db`, implies no web search, refuses explicit `--web-search`/`--auto-sources`, and reuses `<run>/evidence/evidence.json` if no pack given. Web intake lives in `cmd_run` (~:198), not the orchestrator: one `GeminiApiBackend.research()` call → new pack path.

**Wizard** (`src/wizard.py`): diagnostics report key presence/length only; prompts ticker/company/date/provider/model/source mode/depth/strict (default **Yes** for real providers, **No** for mock)/independent bear/run dir. Only an explicit live opt-out enables `allow_defaults`. Run, audit and JSON packet arguments come from the CLI parser; an audit exception exits nonzero.

---

## 3. Agents (`src/agents/`)

### Registry (`registry.py`)
`AGENT_SPECS`: A1, A2, A3, A4, A5, A6B, A6R, A6, A7. `default_registry`: provider arg > `FUWFORGE_PROVIDER` > mock; model arg > `FUWFORGE_MODEL`.

| provider | backend | default model |
|---|---|---|
| `mock`/`synthetic` | `mock_registry` | — |
| `stub` | `StubAgent` (raises) | — |
| `agy`/`gemini_cli` | `AgyCliBackend` | `gemini-3.8-flash-low` |
| `opencode`, `opencode-go`, `opencode_go` | `OpenCodeGoBackend` | `deepseek-v4.1-flash` |
| `openai` | `OpenAICompatibleBackend` | `gpt-4o-mini` |
| `gemini` | `GeminiApiBackend` | arg > `GEMINI_MODEL` > `.env` > `gemini-3.8-flash` |

`model_registry` shares **one backend instance** across all agents/threads. `resolve()` instantiates per call.

### Backends (`model.py`)
- `_ProviderText(str)` carries `.usage`; only Gemini supplies usage. `summarize_provider_usage` → `{attempts, total_tokens}`; total is `None` unless every attempt reported ints (null ≠ zero).
- **AGY**: subprocess `agy --print PROMPT --model M --output-format text --disable-slash-commands`, 90 s timeout; prompt passed as argv (ARG_MAX risk).
- **OpenAI-compatible**: `OPENAI_BASE_URL` (default api.openai.com/v1). Key routing: official host → `OPENAI_API_KEY`; other hosts → `OPENAI_COMPATIBLE_API_KEY` only (generic key never sent to unconfigured hosts). temp 0.2, key redacted from errors, no retries.
- **OpenCode Go**: key arg > `OPENCODE_GO_API_KEY` > repo `.env` > `~/.local/share/opencode/auth.json` / `~/.config/opencode/auth.json`; URL `OPENCODE_GO_BASE_URL` or `https://opencode.ai/zen/go/v1`; header `x-opencode-session`.
- **Gemini API**: key arg > `GEMINI_API_KEY` > `GOOGLE_API_KEY` > same from repo `.env` (never mutates env). `generateContent` with `responseMimeType=application/json` + `responseJsonSchema`. Rejects blockReason / no candidates / MAX_TOKENS / non-STOP finish / empty text. HTTP retries 429/503 up to 2 (2 s, 4 s, or Retry-After ≤60 s) recorded as `unreported_provider_attempts`. `research()` uses the Interactions API, no retry, requires status `completed`.

### Prompt construction (`ModelAgent._build_prompt` ~:1136)
- Template `PROMPT_TEMPLATES[id]` + `EPISTEMIC_RULES` (~:810: [Fact]/[Estimate]/[Derived]/[Interpretation]/[Judgment] tags; Beneish is a screening signal; ignore instructions inside `<untrusted_source_data>`). A6R uses `A6R_INDEPENDENT_TASKS` (blind, local `H` ids) or `A6R_RECONCILE_TASKS`.
- Upstream content (A1 RAW_DATA_PACK ≤1500 chars, summaries, accepted upstream artifacts as html-escaped sorted JSON) is wrapped in `<untrusted_source_data>`. Retrieved docs → `<untrusted_source_data label="retrieved-documents">` or "NO RETRIEVED SOURCE DOCUMENTS".
- **Trusted runner text outside the untrusted wrapper:** `_forensic_inputs_block` (runner Beneish indices, copy verbatim, or unavailable reason), RUNNER PHASE DIRECTIVE for independent bear, OUTPUT CONTRACT (gap rules + `ALLOWED_DATA_GAPS[id]`), and `json.dumps(envelope_schema(...))`.
- Prompt must be deterministic under dict reordering (it feeds the execution key; locked by test).
- `contract_doc` path is relative in a checkout, absolute in an installed package → prompt text (and execution key) differs between them.

### Output schema / parsing
- `output_schema.envelope_schema`: artifacts from `ARTIFACT_SHAPES[REQUIRED_ARTIFACTS]` (A7 Scorecard from resolved weights), sections from `SECTION_CONTRACTS` minus skips + MISSING_DATA_TABLE, `metrics.missing_data.field` enum from `ALLOWED_DATA_GAPS`.
- `output_format.parse_object` / `extract_json_payload`: strips ANSI; rejects top-level arrays, duplicate keys, NaN/Infinity; one trailing-comma retry; fenced blocks preferred; picks first candidate with `schema_version`/`agent`/`artifacts`, else largest.

### `_normalize_envelope` (~:1487)
- Forces schema_version 2.1, agent id/name, `params = context.params`. Merges sections from `sections_md`/`sections`/root keys, coerces to strings, matches contract sections case-insensitively/by number. `canonical_artifacts` lifts root-level required keys.
- **Sources:** with evidence, always replaced by the evidence document list; off-pack citations recorded in `metrics.unverified_source_urls` (canonicalized URL compare). Without evidence: "Unverified" placeholders.
- `metrics.data_checksum` **always** overwritten by runner (`frozen` > hash(params+evidence) > hash(params)); sets `evidence_checksum`, `source_grounding`.
- **Strict:** requires source documents and no unverified URLs; A7 scorecard recomputed (`weighted_scorecard`); A3 Beneish via `_normalize_beneish`; `validate_contract(strict=True)` → `ValueError` on failure (eligible for the one repair).
- **Lenient:** injects per-agent placeholder defaults (A1 shares 1e9/price 100; A4 Glassdoor 4.1; A5 sentiment 0.6/0.3/0.1; A7 default scores…) and records each in `metrics.injected_defaults`. Report then says NOT VERIFIED.
- A3 `_render_beneish_section`: model may not write Beneish/M-score text in SECTION_4; runner appends `render_beneish_block`.
- `_normalize_beneish` (~:152) preference: runner forensic inputs (all 8; model index disagreeing by >5e-7 → error) → model indices recomputed deterministically → `feasible=false` → unavailable → bare scalar → `model_reported_unverified` → nothing → unavailable + injected.

### `ModelAgent.run` — repair policy (~:1054)
- Durable (automated orchestrator always sets `on_provider_return`): `retries = min(max_retries, 1)` → **at most one repair**, only if the response was recorded **and** failed with `ValueError` **and** `on_repair_start` set. Transport/runtime errors → no retry → `unknown_completion`. Non-durable direct use: `max_retries`.
- 15 s backoff before each attempt after the first. Repair prompt = original + "CRITICAL ERROR on previous attempt: …". `begin_repair` moves rejected response to a failed history row (`metadata.repair_of=key`).
- Tests lock: `["not json","valid"]` → 2 calls; timeout → 1 call + unknown_completion; 3 invalid → 2 calls + failed.

### Mock agents (`mock.py`)
- `_generate_<id>` per agent; raw `{"mock":True,...}` (no `raw` text → no local recovery; tokens from fixture metrics).
- A1: `data_checksum = frozen or checksum_json(params)` (**ignores evidence**); shares as strings; price 150; `params["fail_gate"]`/`["a1_gate_fail"]` → gate fail.
- A3 includes Beneish line in SECTION_4 (ModelAgent would reject this). A6R ignores challenge phase (emits B ids in blind pass).
- Tests rely on: section order, `data_checksum == summary.checksum`, Quick skip of 13/17/18, mock banner, exactly one/two "A6R accepted" lines.

### Env vars read
`FUWFORGE_STRICT, FUWFORGE_PROVIDER, FUWFORGE_MODEL, OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_COMPATIBLE_API_KEY, OPENCODE_GO_API_KEY, OPENCODE_GO_BASE_URL, OPENCODE_GO_SESSION, GEMINI_API_KEY, GOOGLE_API_KEY, GEMINI_MODEL, SEC_USER_AGENT, TYPESAFE_API_KEY` (+ repo `.env` via `dotenv_values`).

---

## 4. Integrity and audit layer

### Envelope (`envelope.py`, `schemas/envelope_v2_1.json`)
- Fields: `schema_version, agent{id,name}, timestamp, params, gates{passed,notes}, sections_md, artifacts, sources (minItems 1, each needs type+url), warnings, metrics, confidence?, content_checksum?, upstream[{agent, content_checksum}]`. Root `additionalProperties: true` (extra keys allowed and hashed). `format: date-time` not enforced (no format checker). `Envelope.from_dict` is permissive (defaults everything, never raises).
- `artifacts._execution_lineage` is reserved runner bookkeeping, covered by the checksum.

### `content_checksum` (`utils/checksum.py: envelope_content_checksum`)
- Hashes every top-level key **except `content_checksum` and `metrics`**; `metrics.missing_data` (if non-empty) is re-injected as `research_missing_data` and hashed.
- **Not covered** (editable post-seal undetected): `metrics.data_checksum, injected_defaults, source_grounding, unverified_source_urls, runner_beneish_block, prompt_sha256, execution_key, provider, model`, tokens/latency.
- Canonicalization `_normalize_json` is a **custom flattening, not JSON**: sorted `{k:v}` with unquoted keys and `str(x)` values → non-injective (`{"a":"x,b:y"}` ≡ `{"a":"x","b":"y"}`, `"1"` ≡ `1`; `1.0` ≠ `1`). Execution identity and execution store use real `json.dumps(sort_keys=True)` — two canonicalizations in the codebase. **Changing either invalidates every sealed run/receipt.**
- Format `sha256:<hex>`.

### Lineage (`execution_identity.py`)
- `UpstreamRef(agent, content_checksum)`; `capture_upstream` on the frozen snapshot, sorted, rejects unsealed parents. Observed DAG: A1→[]; A2..A5→[A1]; A6B→[A1..A5]; A6R→[A1..A5,A6B]; A6→[+A6R]; A7→all 8.
- `execution_key` = sha256(`json.dumps({schema_version:1, provider, model, prompt_sha256, evidence_sha256, upstream}, sort_keys)`) → bare 64-hex. The orchestrator puts the **request hash** in the `prompt_sha256` slot; evidence slot = sha256 of JSON-encoded pack checksum.

### Contracts (`contracts.py: validate_contract` ~:791-937)
Order: required artifact keys present → `metrics.missing_data` (path in `ALLOWED_DATA_GAPS[agent]` and a documented null) → per-agent structural check → strict-only A3/A7 extras → A6 linkage / A6R bull-id linkage → A3 legacy Beneish warnings → `validate_sections` (required sections; overclaim lint: Beneish/Altman + "prove/verify/confirm" + "no fraud" in one sentence → error) → embedded calculation witnesses (`validate_witness` with ledger, company, cutoff; found by heuristic, max depth 10) → source grounding / unverified URLs / injected defaults (warning; strict → error). Gate `passed` is enforced separately by the orchestrator.

| Agent | Key rules |
|---|---|
| A1 | `price_timestamp_eet` non-empty string; `shares_basic/diluted` > 0 (numeric strings OK); `market_cap_check` price·shares vs cap within 5%. No gaps allowed. Sections RAW_DATA_PACK (+opt. SECTION_1_SEC_INTAKE, MISSING_DATA_TABLE). |
| A2 | PEER_LIST ≥3 strings; MoatTrend ∈ Expanding/Stable/Eroding/Unclear; TopCustomer/SupplierPct string or `{percent}`. Sections 1/2/3 (+AI_STRATEGY). |
| A3 | AltmanZ (score finite or null+classification); BeneishM (below); OCF_vs_NI_Flag ∈ Aligned/Divergent/Negative; PEG_TTM/Fwd; HypeFlag ∈ Reality/Balanced/Hype Risk; PeerTable list; TechnicalSet SMA_50/SMA_200/RSI_14. Sections 4/5/12. |
| A4 | CEO_Pay_Ratio ≥0; Auditor, AuditorTenure strings; GlassdoorScore 0–5 (gap ⇒ `LowSampleSizeCultureData=True`); ESG_Momentum ∈ Improving/Stable/Deteriorating/Unclear. Sections 6/7. |
| A5 | SentimentSplit pos/neu/neg 0–1, sum ∈ [0.9,1.1]; SocialVsFundDivergence ∈ Aligned/Mild/Pump Risk; lists; AuditorChangeYN; InsiderNet12m / InstitutionalOwnershipTrend direction enums. Sections 8/9. |
| A6B | BullCase non-empty list of `{id, thesis, evidence[]}` (no count bound, evidence refs not resolved). Section 10. |
| A6R | BearRebuttals `{bull_id ∈ A6B ids (skipped if A6B absent), probability, severity ∈ High/Med/Low/Medium, counter}`. No 1:1 coverage check. Section 11. |
| A6 | DangerZoneDashboard `{category, flag, note}`; ScenarioTable bull/base/bear each with Revenue & EPS keys; Catalysts, Headwinds non-empty. Section 14. |
| A7 | Scorecard 8 categories: score 0–10, `|weighted − score·weight| ≤ 0.005`, Σweights = 1±0.001, `|Overall − Σweighted| ≤ 0.05`, rating_band Buy/Hold/Sell (specific weights not enforced); SWOT lists; Glossary; 13 `comparison_fields` finite or null (strict: null needs gap). Sections 0,13,15,16,17,18 (depth skips). |

**Beneish states (A3):** unavailable (`score None` or `feasible False`; no score fields; needs reason) · `model_reported_unverified` (strict → error) · deterministic + witness (formula `beneish_m_score`, exactly 8 inputs, replay matches, version = `CALCULATOR_VERSION`, units ratio) · bare unlabelled score (warning; strict → error).

**Strict vs lenient in `validate_contract`:** strict promotes unlabelled/model-reported Beneish, AltmanZ null without gap, A7 comparison null without gap, model-reported witness, unverified grounding/URLs, injected defaults to errors. Unknown section keys are always just warnings.

### Provenance (`provenance.py`)
Line `"{ts} | {actor:<10} | {message} | {sha256}"`; entry hash = sha256(`ts|actor|message|tail`), tail = `checksum_chain` of all prior entry hashes. `flush()` rewrites whole file. `verify_chain` recomputes. Chain is unkeyed — only the run manifest's `outputs.provenance.sha256` binds it. `manifest.provenance.tail` is written but never audited.

### `verify-run`
- `--integrity-only` → `run_audit.verify_run` (envelope schema + checksum, evidence pack equality, graph cycles/upstream digests, provenance chain, report/artifacts presence; strict promotes warnings).
- Default → `run_semantic_audit.semantic_audit` (`AUDIT_LEVELS`):

| Level | Checks | States |
|---|---|---|
| `envelope_integrity` | JSON/schema/content checksum + imports `verify_run` errors **except those containing "checksum mismatch"** (see I1) | passed/failed |
| `source_reconstruction` | evidence dir/pack loads, retrieved sources equal pack sources, pack checksum = manifest | not_applicable / source_bytes_verified / source_bytes_unavailable |
| `graph_and_contracts` | role ids, cycles, upstream digests vs **sealed** parents, `_execution_lineage` vs upstream (stripped = warning, contradiction = error), `validate_contract(strict=False)` per role — **only errors kept** | passed/failed |
| `calculation_replay` | ledger load; `replay_beneish(A3)` (8 bound inputs, recompute within 1e-9); other witnesses: bindings + identity replay; unknown formula → unavailable | failed / passed / not_replayable / not_applicable |
| `final_artifacts` | `run_manifest.json` output hashes, safe paths, runner Beneish block once in SECTION_4 and report (if `metrics.runner_beneish_block`), manifest roles/graph vs envelopes | passed/failed |
| `completeness` | fixed 9 roles (not depth-aware); halted manifest → incomplete | passed/incomplete/failed |

- `AuditReport.overall_state`: errors → failed; halted/incomplete → incomplete; else verified. `source_backed_acceptance()` unmet unless: source_bytes_verified, exactly 1 core Beneish witness replayed with 8 verified inputs and 0 unverified, ≥1 witness replayed, ≥1 verified input, no unavailable items. Assurance labels: `failed / incomplete / source_backed / internally_consistent_with_disclosed_gaps`. `--require-source-backed` turns unmet conditions into errors. CLI `--strict` appends all warnings as errors. Exit 1 on errors or incomplete.
- Fact ledger for audit (`_load_fact_ledger`): evidence snapshot facts (checked vs manifest) → `facts/`, `fact_ledger.json`, `facts.json` → envelope `artifacts.facts` (conflicts are problems; with a snapshot, envelope-only facts are problems).

### Run manifest (`build_run_manifest`, `schemas/run_manifest_v1.json`)
`schema_version, run_id, status (completed|halted|incomplete), complete, ticker, analysis_date, halt_reason, roles {agent: content_checksum}, graph {agent: parents}, outputs {report, artifacts, provenance, summary: {path, sha256, bytes}}, evidence {pack_checksum, state, bundle_manifest_sha256}, calculator_versions, prompt_sha256, execution_keys, provenance {path, tail}, configuration, generated_at` (+`resealed` after forced seal). Envelopes are bound via `roles`, not `outputs`. `evidence.state=source_bytes_verified` is set merely because the summary has an evidence checksum.

### Seal (`controller.cmd_seal` ~:361, `_rebuild_run_manifest` ~:504)
Refuse unanchored or changed roles and outputs without `--force`; reject malformed manifests, absent completed outputs and cyclic lineage. Restore recorded lineage only from exact retained references, bound the restamp loop, and prepare the manifest and replacements before writes. A no-op preserves bytes; handled failures restore staged backups. Forced overrides are recorded in `resealed`. Replacements are per-file, so abrupt process termination can still leave an interrupted multi-file update.

### Execution store (`execution_store.py`)
- Table `execution_receipts`: `execution_key PK, agent_id, state, provider, model, prompt_sha256, evidence_sha256, upstream_json, raw_checksum, accepted_checksum, raw_response_json, accepted_envelope_json, usage_json, latency_ms, error, recovery_choice, metadata_json, created_at, updated_at`.
- States: pending, running, returned, validated, failed, unknown_completion. `begin` upserts and clears prior outcome (incl. `recovery_choice`) but keeps identity/created_at. `begin_repair` (requires returned) writes a failed history row with `metadata.repair_of`. `is_safely_reusable` = validated ∧ accepted checksum ∧ envelope ∧ not abandon. `requires_recovery` = returned/unknown ∧ no choice. **No state-machine guards on `mark_*`/`resolve_recovery`.**
- One sqlite connection per thread (`threading.local`, `check_same_thread=False`, timeout 30); WAL set on init. No default path: `db_path` is required; an explicit `:memory:` is a shared-cache DB across threads (tests only; concurrent writers can hit "table is locked"). `close()` makes the store unusable.
- Lifecycle transitions are guarded by `ALLOWED_TRANSITIONS`; validated receipts are never demoted; `abandon` is durable unless `override_abandoned=True`; `begin()` verifies the supplied key and the existing row identity. `invalidate_descendants` was removed (changed requests get new keys).
- Unused in `src/`: `reusable_nodes`, `get_reusable_envelope`, `is_reusable`, `lookup`, `validate_receipt`.

### Research packet (`research_packet.py`, `schemas/research_packet_v1.json`)
`build_packet` embeds `semantic_audit().to_dict()`, typed facts (projected; any ledger problem aborts), calculation witnesses (top level / one level deep), sources (envelope + evidence docs), claims (A6B bull, A6R bear, A6 danger/catalysts/headwinds, A7 scorecard + comparison fields), versions, limitations (mock notice, audit warnings/errors, missing data). Excludes section markdown, prompts, raw responses, evidence bodies, metrics, provenance. `validate_packet` re-derives the ledger and compares field-by-field; `approval_status` may not be approved/published; `FORBIDDEN_SOURCE_KEYS` absent. `export_packet` writes sorted JSON and returns its sha256. Always `pending_human_review`.

### Runtime metrics
`runtime_metrics.finalize_agent` accumulates only when the previous status was `passed`; tokens become None if either side is None.

---

## 5. Typed facts and SEC ingestion

### `Fact` (`facts.py:118`, frozen)
Required: `id, company, concept, unit, filing_accession, availability_time, retrieval_time, source_location (str or dict with url), accounting_basis, extractor_version`. Optional: `kind` (reported|forecast|restatement|derived|estimate), `value` (float|None), `currency, period_start, period_end, instant, supersedes, missing_reason, dimensions, metadata`, `schema_version="financial_fact_v1"`. Id is the only identity (no content hash).
- `usable_as_of` (:245): `availability_time <= cutoff`; unparseable → False. **Date-only cutoff = 00:00Z**, so SEC facts filed *on* the analysis date (available 23:59:59Z) are excluded, while documents with `doc_date == analysis_date` are included.
- `validate_fact` (:266): Draft7 + semantics (None value needs `missing_reason`; no bool/NaN/Inf; exactly one of duration/instant; valid timestamps; source_location). Does not validate accession format.
- `resolve_as_of` (:512): filter by concept/company/period/dimensions; exclude forecasts; chronology guard; drop candidates superseded by an *available* one; latest availability wins. A post-cutoff restatement is invisible → original returned.
- `check_ledger_consistency` groups by `(company, concept, period, dims, kind)` — **kind in key means reported vs restatement are never compared**.
- `load_fact_ledger` accepts dir/file/CompanyFacts mapping/iterable; CompanyFacts auto-routed to `parse_sec_company_facts`.

### SEC CompanyFacts (`sec_facts.py`)
- `parse_sec_company_facts_report(data, ticker, cik, retrieval_time, max_facts=50000, as_of, defer_dimensional=True)` → `FactIntakeReport(facts, coverage)`. `EXTRACTOR_VERSION_SEC="sec_companyfacts_v1"`, `AVAILABILITY_POLICY="sec-filed-date-end-of-day-v1"`, 46 `PRIORITY_CONCEPTS`.
- Availability: `filed` date → `T23:59:59Z`; malformed → excluded with warning.
- Units: currency codes kept + `currency`; shares→`shares`; `USD/shares`→`USD/share`; pure→`ratio`. Basis: us-gaap & dei → US-GAAP, ifrs → IFRS, else OTHER.
- Fact id: `fact_{ticker}_{concept}_{periodslug}_{accn}` (+`_{idx}` on collision). Company = ticker (or uppercased entityName if no ticker).
- Bounded intake: if over `max_facts`, round-robin across `(taxonomy, concept, unit, period)` groups by concept priority, newest first. Coverage report is **not persisted** in production.
- **Disclosure classification** per group, ascending by (availability, accn): first → reported/`initial_disclosure`; form `/A` → restatement/`amended`; same value → reported/`repeated_disclosure`; different value → restatement/`restated`; else restatement/`superseded_incomparable` (only reachable when a value is null, since unit+basis are in the group key). `supersedes` points at the immediately prior observation (which may be a repeat, not the original).
- Custom/dimensional concepts are deferred (with `defer_dimensional=True`, the default) into `coverage.deferred_concepts`; no placeholder `Fact` is created. Observations without a filing date or accession are excluded and counted (`records_excluded_missing_filing_date` / `_missing_accession`). Null-valued facts never take a prompt slot.

### How facts reach agents
`public_data._collect_company_facts` → `sec_companyfacts.json` attachment (kind `sec_companyfacts`) → `EvidencePack._load_fact_attachments` (evidence.py:538) re-parses each load; `fact_ledger`/`financial_facts`/`*.facts.json` attachments go through `load_fact_ledger` + `validate_fact` (invalid → raise). Embedded `evidence.json["facts"]` use `Fact.from_dict` **without** `validate_fact`. Orchestrator builds ledger as of analysis date → `AgentContext.fact_ledger`, `forensic_inputs_for`, and prompt "Date-Bounded Typed Fact Ledger" (`_select_prompt_facts`, limit 100, 30 core concepts round-robin).

---

## 6. Fact binding, witnesses, calculators

### Fact binding (`fact_binding.py`)
- `NUMERIC_COMPARISON_POLICY="exact-float-equality-v1"`. `compare_numbers` with no tolerance requires `==`; opt-in tolerance `rel*max+abs` (defaults 1e-9 / 1e-12, never used by default).
- `unit_dimension`: ratio (ratio, pure, x, percent, %, …), currency (24 codes), count (shares, employees…), per_share (`CODE/share`…), else unknown. Scale suffixes thousand/million/billion/k/m/mm/bn. `units_compatible`: same dimension + scale + currency. Percent is a ratio with scale 0.01: percent vs ratio is a scale mismatch; dimensionless division divides scales (Batch B).
- `fact_filing_identity_valid`: `^\d{10}-\d{2}-\d{6}$`, rejects all-zero placeholders. `fact_source_reference_valid`: absolute http(s) URL.
- `verify_input_binding` (:1109) → `InputBinding(status unavailable|failed|bound, problems, notes, derivation)`.
  - **Direct:** fact exists → company (if expected) → non-null value → exact equality → unit (if declared; ratio vs currency/count is explicit failure) → period (if declared) → basis (if declared) → filing identity + source ref (always) → `usable_as_of` (if cutoff). Undeclared checks are silently skipped.
  - **Derived** (`witness.derivations[key]`): `Derivation.from_spec` needs `expression` + operands (`operand_fact_ids`[+`operand_names`] or `operands` dict), optional named `constants`, optional `result_value`. Each operand checked for provenance/cutoff/basis/period; anchor period = max end. AST allows only `+ - * /`, unary ±, Names (no literal constants; ≤1000 chars, 50 nodes, depth 10). Names resolve via operand_names → fact ids → aliases `a..z`, `op0..`, `x/y/z`, `x0..`; unused operands error. Unit inference: +/- same unit; × needs a ratio operand; like÷like → ratio; currency÷count → per_share. Claimed must equal recomputed exactly.
- `verify_witness_bindings` (:1595): input with no `fact_id` and no derivation → `unavailable` (note, not failure → audit warning).

### Calculation witnesses (`calculation_witness.py`)
- `CalculationWitness`: `formula_id, formula_version, calculator_id, result, inputs, parameters, input_fact_ids, units, provenance (deterministic|unavailable|model_reported_unverified), notes, derivations?, input_periods?, input_bases?`.
- `beneish_witness`: version `CALCULATOR_VERSION="2.0.0"`, params `threshold_policy="ecb-385-eq2-conventional"`, `screening_threshold=-1.78`; runner path `index_source="runner_computed"`.
- `validate_witness` (:166): model-reported → always an error (downgraded to warning in non-strict by contracts.py ~:906); unavailable needs `result=None` + notes; deterministic needs finite values, then binding verification; Beneish adds `validate_forensic_witness` and replays the score (abs tol 1e-9). **Only Beneish is replayed.**

### Forensic inputs (`forensic_inputs.py`)
- `CONCEPT_ALTERNATIVES` (:61) per role, preference order. **`sga` = `SellingGeneralAndAdministrativeExpense` only** (why MELI is unavailable).
- Annual = 330–400-day duration; anchors on revenue ends and Assets instants; needs two consecutive annuals and instants within 7 days (52/53-week). `_select` skips null values and basis OTHER; latest filing per period. `need()` uses the first concept present for **both** years.
- Indices (operand-named expressions evaluated via the fact_binding evaluator): DSRI, GMI (gm_t0/gm_t1), AQI (constant `one`), SGI, DEPI, SGAI, LVGI (current liabilities + LTD, not total liabilities), TATA ((NI−OCF)/assets).
- `forensic_inputs_for` never raises → `{feasible: False, indices: {}, unavailable_reason}` on failure. `as_of` param unused (caller pre-filters).

### Calculators
- `utils/beneish.py`: `CALCULATOR_VERSION="2.0.0"`, ECB OP 385 Eq. 2 coefficients (−4.84; DSRI .920, GMI .528, AQI .404, SGI .892, DEPI .115, SGAI −.172, LVGI −.327, TATA 4.679). `beneish_score` keyword-only, fsum, unrounded; `classify` > −1.78 → "High Manipulation Risk". `beneish_result` rounds to 2dp + raw. Golden: all indices 1, TATA 0.1 → −2.0121. `render_beneish_block` wraps in `<!-- runner-beneish:start/end -->`.
- `utils/finance.py`: `DEFAULT_SCORECARD_WEIGHTS` (Financials .25, Moat .20, Growth .15, Valuation .15, MgmtGov .10, RiskInverse .10, ESG .025, Market .025); `resolve_weights` (exactly 8 keys, sum 1±0.001, else defaults + errors); cagr, fcf_conversion, accruals_ratio, fcf_yield, roic, net_debt_to_ebitda, return_driver_stack; `altman_z_score` (**no production caller, unversioned**); `generate_comparison_fields` (13-metric vector; `evidence_quality_score_10` and `data_completeness_score_10` default to 9.0).
- **SMA_50/SMA_200/RSI_14 are computed in `evidence._transformed_text`** (evidence.py ~:178) from retrieved closes, not in finance.py. RSI is Wilder's seeded from the first 14 changes of the ~550-day window. **PEG has no calculator** (schema field only).

---

## 7. Evidence

### Evidence pack (`evidence.py`)
- Limits: 8 MiB/document, 16 documents, 16 attachments (enforced at load only), PDFs ≤300 pages, not encrypted, text-bearing (pypdf strict).
- `sec_user_agent()`: `SEC_USER_AGENT` env or `.env`; must contain `@`, no CR/LF; sent only to sec.gov hosts. SEC throttle ≥0.25 s (module global).
- `_public_url`: HTTPS only, no userinfo, port 443, no fragment, no credential query keys, no localhost/.local/.internal, non-global IPs refused; optional DNS check (separate from connect).
- `download_public_document`: no redirects, `Accept-Encoding: identity`, HTTP 200 only, 30 s timeout.
- Text extraction: transforms (`yahoo_daily`, `stooq_daily`) → curated JSON with `last_bar_date`, `last_close`, `TechnicalSet`, history (rows after `as_of` dropped; last bar within 7 days); PDF via pypdf; HTML parser drops script/style.
- `fetch_evidence(manifest, out)`: manifest `{"ticker", "documents":[{url, doc_date, type?, title?, date_basis?, transform?}]}` → `D###.bytes`, `D###.txt`, `evidence.json` (`schema_version "1"`, records with content/text sha256, accessed, `verification:"retrieved"`).
- `EvidencePack(path, ticker, analysis_date)`: validates schema, ticker, ids, URLs, `doc_date <= analysis_date`, hashes, **re-derives text from bytes and checks `text_sha256`** (pypdf-version-sensitive), attachments, loads facts. `checksum = sha256(canonical evidence.json)`.
- `snapshot(out)` keeps the run's analysis_date (never infer cutoff from newest doc). `prompt_text(budget=80000)`: per-doc budget, statement-heading-aware excerpts (`_context_excerpt`), html-escaped; `gemini_web_research` attachment contributes only page count + limitations, never model prose; then the fact block.

### Public data collection (`public_data.py: collect_public_sources`)
Ticker regex, non-future date, `SEC_USER_AGENT` checked first. CIK via `company_tickers.json` (current mapping), ticker must appear in submissions. Forms: 10-K/20-F/40-F (+/A), 10-Q(/A), DEF 14A/DEFA14A, 8-K(/A), `filingDate <= as_of`. Annuals = 3/5/10 for Quick/Standard/Deep (amendment preferred per report date), 1 quarter, 1 proxy, 2 8-Ks; older submission pages ≤12 requests. Price: Yahoo v8 chart (`as_of−550d..as_of+1d`), fallback Stooq; probe and fetch are **two separate downloads**. CompanyFacts best-effort, **bounded by 8 MiB** (large filers degrade to no typed facts; reason in `collection.json`). Output: `discovery/lookup_NN.json`, `collection.json`, `manifest.json` (outside pack checksum), `pack/`.

### Gemini web research (`web_research.py: collect_web_sources`)
Preflight (ticker, company, date, baseline pack, API key) before any paid call. One `backend.research()` call; only `url_citation` annotations used, ≤6 candidates; resolves only Google grounding-redirect wrappers (≤4 hops). Accepts HTML ≤512 KiB with exactly one distinct published date ≤ cutoff, modified ≤ cutoff, within 90 days. Records `WEB###` (`type="WebNews"`, `date_basis="page_publication_metadata_not_point_in_time"`); trace `gemini_web_research.json` attachment (`request_count=1`, `paid_retries=0`). Writes a new snapshot pack and revalidates.

### Evidence bundles (`evidence_bundle.py`)
`build_bundle` → `manifest.json` (files sorted with size+sha) and returns `manifest_sha256` (the trust root; record out of band). `verify_bundle` (default **64 MiB**) rejects symlinks/special files/`..`/extra files, streams hashes. `restore_bundle` (default **512 MiB**) verifies, copies, constructs `EvidencePack`. `check_extraction_version` → `source_bytes_verified|source_bytes_unavailable|semantic_failure|extraction_mismatch`.

---

## 8. Data gaps, sentry, evals, resources

- **`data_gaps.ALLOWED_DATA_GAPS`**: A1 none; A2 PEER_LIST, TopCustomerPct, TopSupplierPct; A3 AltmanZ.score, BeneishM.score, PEG_TTM, PEG_Fwd, HypeFlag, PeerTable, TechnicalSet.SMA_50/SMA_200/RSI_14; A4 CEO_Pay_Ratio, GlassdoorScore, AttritionNote; A5 SentimentSplit, SocialVsFundDivergence, MajorLitigation, AuditorChangeYN, InsiderNet12m, InstitutionalOwnershipTrend, RegulatoryWatchlist; A7 `comparison_fields.<13>`; **A6/A6B/A6R none**. A gap is valid only if the artifact path is present and explicitly `null` with a reason (`documented_null`). Enforced in contracts.py ~:821.
- **Sentry** (`sentry.py`): `extract_thesis_state` reads A6B BullCase (+A6 DangerZone, unused). Providers mock/jev (TypeSafe)/gemini/opencode; `auto` → Jev else mock with disclosed `fallback_reason`. `requires_rerun` if action `trigger_deep_rerun` or any score ≥ threshold. LLM outputs not schema-validated.
- **Eval scorer** (`evals/score_research.py`): 9 cases (amended filing, 1000× units, quarter vs year, share basis, missing TATA, injected instruction, Beneish golden, unsupported forward claim, post-cutoff leak). Gating categories: unsupported claims, arithmetic, chronology, source refs + missingness_ok + injection_safe. Exactly one prediction per case. CI runs the clean fixture with `--fail-on-errors`.
- **Resources** (`resources.py`): `get_resource_path` precedence custom root → checkout (`config/depth_modes.yaml` present, not site-packages) → `src/_packaged_resources` (copied by `setup.py CustomBuildPy` + `MANIFEST.in`) → fallback. `get_default_runs_dir`: checkout → `<repo>/runs`, installed → `CWD/runs`. Installed top-level package is literally `src`. `test_packaged_resources` builds wheel+sdist and smoke-tests clean installs.

---

## 9. Known bugs, risks, and doc drift (found 2026-10-30; fixes marked below)

Severity is a judgment; verify before fixing. ✔ = re-checked against code by hand.

### Orchestration / CLI / agents
- ~~**B1 ✔ Refused resume clobbers archived outputs.** Evidence-mismatch, uncertain-completion, and strict-without-evidence refusals rewrote summaries and manifests.~~ Fixed in `49058e7`; preflight tests compare the entire run tree, including SQLite WAL files.
- ~~**B2 Wizard audit always crashes:** `audit_args` lacked `strict`; the wizard also named its JSON packet `.zip`.~~ Fixed in `c9c5536`; the wizard uses parser-built arguments and exits nonzero if an audit crashes.
- ~~**B3 Controller-assembled runs were labeled mock.**~~ Fixed in `34841bc`; reports identify operator-supplied envelopes, and summary/manifest metadata use `controller` / `not-applicable`.
- **B4** ~~Execution key misses agent code changes~~ Fixed in `eab1455` (identity v2: package-relative framework code digest + custom-agent MRO source; unhashable agents fail closed on resume). Was: `inspect.getsourcefile(type(agent))` resolves to `registry.py` (where `ConfiguredModelAgent`/`ConfiguredMockAgent` are defined), not `model.py`/`mock.py`.
- ~~**B5 Wizard default is lenient for live Gemini.**~~ Fixed in `c9c5536`; real providers default to strict and require an explicit opt-out for defaults.
- **B6** ~~One failing A2–A5 sibling leaves others `returned`~~ Fixed in `d2cf16d`: completed siblings are accepted and persisted before the failure is re-raised.
- **B7** ~~FAILED receipt whose local recovery fails re-dispatches a paid call without operator choice~~ Fixed in `a43649c`/`893e7fe`: the choice is applied per receipt after local recovery fails; receipts that already failed acceptance need a choice without replay.
- **B8** ~~Resume overwrites `a*_raw.json`~~ Fixed in `d2cf16d`: reuse never writes raw files, restores an envelope only if missing/different, and records `reused=true` in provenance, metrics and `run_summary.json` `reused_agents`.
- **B9** ~~A3 lenient early return~~ Fixed, Batch B. Was: (model.py ~:1679, no errors + gaps) skips Beneish normalization/rendering.
- **B10** ~~Mock A1 `data_checksum` ignores evidence checksum~~ Fixed, Batch B. (ModelAgent's includes it).
- **B11** ~~Fixed, Batch B (Medium→Med; explicit scale-suffix table incl. bn/mn/tn/x; unknown suffix unparseable).~~ Was: A6R normalization accepts "Medium" but schema enum is High/Med/Low. `_parse_numeric` strips `%` without dividing; `bn` not parsed.
- **B12** ~~`--strict` leaks into `os.environ` for the whole process.~~ Fixed in `9043bfc`. ~~`--resume` without run dir/`--run-id` makes a new run id~~ (fixed in `bdb1280`). Installed-mode `.env` path resolves to `site-packages/.env`. AGY prompt via argv (ARG_MAX).
- Dead/redundant: manifest written twice on completion; `envelopes["A1"]` reassigned; duplicate `needing` list; `_EMPTY_SHA256`, `_SITE_PACKAGES_ROOT`, `StubAgent`, `register_factory`, `**kwargs` in `default_registry`; initial-bear "Headwinds" fallback never produced.

### Integrity / audit / store
- ~~**I1 ✔ Provenance-chain tampering was hidden from `envelope_integrity`.**~~ Fixed in `bdec081`; checksum findings are distinguished structurally, and provenance tampering fails the integrity level.
- ~~**I2 `verify-run --strict` did not apply strict contract rules.**~~ Fixed in `449f11d`; strict contract failures are attributed to the graph level, while documented unavailable inputs remain disclosed warnings.
- **I3 ✔ Unhashed metrics drive checks:** `injected_defaults`, `source_grounding`, `unverified_source_urls`, `runner_beneish_block`, `data_checksum` are outside `content_checksum`; deleting `runner_beneish_block` silently disables the report Beneish-block check.
- **I4 ✔ Non-injective canonical form** in `_normalize_json` (collisions; see §4).
- ~~**I5 Missing roles in halted runs were reported as `failed`.**~~ Fixed in `b0a0e33`; valid halted runs report `incomplete`, while missing roles in completed or unmanifested runs remain failures.
- ~~**I6 Seal gaps:** stripped covered-field checksums bypassed manifest anchoring, cycles could hang, and manifest-rebuild errors were swallowed.~~ Fixed in `6fa4256`; legacy v1 still excludes the semantic metrics tracked by I3/I4 until checksum v2.
- **I7** Fixed in `5840a6c`/`893e7fe` (see Execution store above). Was: **ExecutionStore:** no state guards (e.g. `resolve_recovery(retry)` on a validated row demotes it); `begin(execution_key=...)` doesn't verify the key; "abandon" is cleared by a later `begin()`; one `--recovery-choice` applies to all uncertain nodes.
- **I8 research_packet:** list-form `missing_data` crashes at ~:684; scorecard claim reads nonexistent `total_score` (always "N/A"); calculations schema (`additionalProperties:false`) rejects witness extras like `input_periods`/`input_bases` → export could fail; schema enum allows `approval_status:"approved"` that code rejects; `analysis_date` falls back to today.
- ~~**I9 ✔ Order-dependent test failure:** `tests/test_controller_assembly.py::test_controller_assembly` fails after `tests/test_output_safety.py` because `controller._build_params` mutates `FUWFORGE_STRICT`.~~ Fixed in `9043bfc`; the regression is tested in both orders.
- Others: unexpected roles reported twice; manifest schema errors fail overall without a failed level; non-dict manifest can raise; upstream digest vs **unsealed** parent silently accepted; provenance `load_existing` re-join is lossy; frozen `data_checksum` echo never re-checked by verify-run.

### Facts / evidence / calculators
- **F1 ✔ ~~Deferred custom concepts use the 1970 epoch~~ (fixed, Batch A):** `filed="1970-01-01"`, accession `0000000000-00-000000`, `instant="1970-01-01"` (sec_facts.py ~:396-410) → available at every cutoff, null-valued, can fill prompt fact slots; contradicts the code comment and `docs/sec-availability-policy.md` §3.
- **F2 ✔ ~~Missing-accession fallback is Apple's CIK~~ (fixed, Batch A)** `"0000320193-00-000000"` (sec_facts.py ~:436), which passes `fact_filing_identity_valid`.
- **F3** ~~Ingested facts depend on whether `arelle` is importable.~~ Fixed, Batch A.
- **F4** ~~Restatement supersedes the latest *repeat*, not the original; ledger consistency never compares reported vs restatement.~~ Fixed in `30592dc` (one linear chain; reported+restatement one family).
- **F5** ~~CompanyFacts > 8 MiB silently yields no typed facts; intake coverage never persisted.~~ Fixed in `17c7f09`: coverage persisted in checksum-covered `intake_coverage`; oversized CompanyFacts is an explicit gap; oversized *older* annuals are recorded omissions.
- **F6** ~~Embedded `evidence.json` facts skip `validate_fact`; `fetch_evidence` doesn't cap attachments~~ Fixed, Batch A.
- **F7** Bundles: `build_bundle` infers cutoff from max doc_date (CLI never passes analysis date); `restore_bundle` falls back to `2099-12-31`; verify default 64 MiB vs restore 512 MiB.
- **F8** ~~Fixed, Batch B: percent is a 0.01-scaled ratio; Beneish replay exact; 5e-7 model-index allowance documented.~~ Was: percent/ratio compatible without scale; Beneish replay uses 1e-9 and model-index comparison 5e-7 (not "exact").
- **F9** ~~Fixed, Batch B (constants recorded; role concepts keyed t1/t0).~~ Was: `InputBinding.to_dict` drops `constants`; forensic `roles.concepts` loses t1 concept when it differs from t0.
- **F10** DNS check and connect are separate lookups (rebinding TOCTOU); SEC throttle not thread-safe; PDF text re-derivation depends on pypdf version.
- **F11** ~~Price history downloaded twice~~ Fixed in `8fb2721` (single download; v2 completed-day bar transform). Was: Price history downloaded twice (probe vs fetch may differ); discovery JSON outside pack checksum.
- **F12** ~~Fixed in `17c7f09`: `facts.cutoff_instant` makes a date-only cutoff end of UTC day everywhere.~~ Was: Cutoff semantics differ: SEC facts filed on analysis date excluded; documents dated analysis date included.
- **F13** Eval scorer: non-mapping predictions dropped silently; scalar hallucinated result for an unavailable calculation passes missingness; unused imports.
- **F14** Sentry LLM output unvalidated; report formatter hardcodes 0.75/0.40.
- **F15** ~~Fixed, Batch B: unmeasured comparison fields are null with documented gaps (model A7 fallback, `screen`).~~ Was: `generate_comparison_fields` defaults two scores to 9.0 (tension with never-fabricate).

### Doc drift
- `docs/fact-binding-policy.md` §6 describes an older, weaker derivation rule; its example (`receivables_t1 / receivables_t0` with only `operand_fact_ids`) would fail current name resolution; named constants undocumented; §2.3 "no tolerance anywhere" vs Beneish 1e-9 replay.
- `docs/sec-availability-policy.md` §4 misdescribes `superseded_incomparable`; §5 field names (`accession_number`, `reported_value`, `extraction_method`) don't exist (actual: `filing_accession`, `value`, `extractor_version`); §3 "never epoch" contradicted by F1.
- `docs/evidence-portability.md`: trace file is `gemini_web_research.json` (not `web_research_trace.json`); `untrusted_root` state never returned.
- `architecture.md`: says SMA/RSI/PEG and Altman are part of the finance engine; SMA/RSI live in evidence.py, PEG has none, Altman is unused.
- `HANDOFF.md` "Known limits": witness value-binding and `ratio` unit bypass are now resolved in `fact_binding.py`.
- `docs/audit-levels.md`: places manifest graph/role checks in level 3 (code: level 5); says a re-seal leaves level 3 intact (`seal --force` restamps the digests level 3 checks); completeness described as depth-aware (code: fixed 9 roles); implies `--strict` enforces strict contract rules (see I2).
- `docs/contracts/A*.md` rules not enforced in code: A6 3–5 bull points and 1:1 rebuttals, Danger-Zone 5 named categories, ScenarioTable FCF/3 years; A7 fixed weights (only Σ=1 checked); A3 `feasible=false` wording. `test_contract_docs_match_code` only checks names appear.

### Hardening branch summary (codex/hardening-2026-10, 2026-10-31)
- Batch C2 `8fb2721`: new packs freeze a versioned market transform (`market_daily_previous_completed_v2`): last eligible bar = min(analysis date, US/Eastern collection date − 1 day); price and SMA/RSI recomputed from eligible bars; strict A1 must echo the bar date (date only) and close. A3 TechnicalSet is not yet bound to the snapshot.
- Task 5.2 `3d323e3`: A3 `AltmanZ` is runner-computed (`src/altman_inputs.py`); unavailable with a documented gap when inputs or a v2 market snapshot are missing. X4 is disclosed as not fact-bound.
- Task 7.2 `0acd372`: A6B 3–5 theses and full A6R rebuttal coverage are contract errors.
- Still open by design: checksum scheme v2 (I3/I4, Task 2.5), bundles (F7), SSRF/SEC lock/pypdf (F10), MELI Tasks 3–5, and the web-search feature.

