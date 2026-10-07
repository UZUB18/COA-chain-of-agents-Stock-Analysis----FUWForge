# FUWForge System Architecture & Codebase Context Reference

> **Quick Context for AI Agents & Developers:**
> FUWForge (Fired Up Wealth Forge) is a contract-driven, deterministic, adversarial multi-agent research pipeline for institutional-grade equity analysis. It orchestrates a 7-stage / 9-role pipeline (A1–A7) where every stage exchanges cryptographically-checksummed JSON envelopes, validates upstream lineage, enforces strict mathematical/forensic constraints, and produces institutional-grade research reports with complete cryptographic provenance.

---

## 1. High-Level System Architecture

```
                                 [Input Config (inputs.yaml)]
                                              │
                                              ▼
                                   ┌────────────────────┐
                                   │   A1: GOD Gate     │  (Harvests SEC filings, validates shares,
                                   │  & Data Ingestion  │   calculates market cap, freezes SHA-256 data checksum)
                                   └─────────┬──────────┘
                                             │
                       ┌─────────────────────┼─────────────────────┐
                       │                     │                     │
                       ▼                     ▼                     ▼
             ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
             │   A2: Business,  │  │    A3: Quant,    │  │  A4: Governance, │  │   A5: News,      │
             │  Moat & Peers    │  │ Valuation, Z/M   │  │   Culture & ESG  │  │ Sentiment, Flows │
             └─────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘
                       └─────────────────────┼─────────────────────┘
                                             ▼
                                  ┌────────────────────┐
                                  │   A6B: Bull Case   │  (Quantified bull theses B1-B5 citing A1-A5 artifacts)
                                  └──────────┬─────────┘
                                             ▼
                                  ┌────────────────────┐
                                  │   A6R: Bear Case   │  (Adversarial counter-rebuttals mapped to Bull IDs)
                                  └──────────┬─────────┘
                                             ▼
                                  ┌────────────────────┐
                                  │ A6: CIO Synthesis  │  (Scenario Lab, Danger Zone Dashboard, Headwinds)
                                  └──────────┬─────────┘
                                             ▼
                                  ┌────────────────────┐
                                  │ A7: Final Assembly │  (Scorecard math, SWOT, Glossary, 13-metric vector)
                                  └──────────┬─────────┘
                                             │
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
         ┌──────────────────────────────┐          ┌──────────────────────────────┐
         │  Assembled Research Report   │          │ Real-Time Jev Thesis Sentry  │
         │  (*_report.md, artifacts.json│          │ (Evaluates breaking news/SEC │
         │   & provenance hash chain)   │          │  filings against bull claims)│
         └──────────────────────────────┘          └──────────────────────────────┘
```

---

## 2. Pipeline Execution Phases

The pipeline is coordinated by `FUWForgeOrchestrator` (`src/orchestrator.py`) through 4 distinct phases:

### Phase 1: Intake & Gate Verification (A1)
- **Agent:** `A1` (Harvester-&-Validator)
- **Role:** Extracts primary filing data (10-K, 10-Q, 8-K, price feeds), verifies share counts and market capitalization arithmetic ($|Price \times Shares - MarketCap| / (Price \times Shares) \le 5\%$).
- **Gate Check:** If primary data is incomplete, corrupt, or missing, sets `gates.passed = false`, emits `MISSING_DATA_TABLE`, and immediately halts execution.
- **Output:** Emits the canonical `metrics.data_checksum` that freezes the data snapshot for the remainder of the run.

### Phase 2: Parallel Domain Analysis (A2 – A5)
Runs concurrently via `ThreadPoolExecutor` (in automated mode) or sequentially (in controller mode):
- **`A2` (Business-Market-Peers):** Company profile, business model, moat trend trajectory (`Expanding|Stable|Eroding|Unclear`), customer/supplier concentrations, and $\ge 3$ competitors.
- **`A3` (Quant-Valuation-Technicals):** Forensic health checks (Altman Z-Score, Beneish M-Score, OCF vs Net Income accrual alignment, PEG TTM/Forward, Hype Flag, technical indicator set).
- **`A4` (Leadership-Governance-ESG):** Governance structure, CEO pay ratio, auditor tenure, Glassdoor/culture metrics, and ESG momentum.
- **`A5` (News-Sentiment-Flows):** Trailing 90-day sentiment split ($pos + neu + neg \approx 1.0$), insider buying/selling direction, institutional ownership trends, and litigation/regulatory watchlist.

### Phase 3: Adversarial Thesis & Synthesis Loop (A6B $\to$ A6R $\to$ A6)
- **`A6B` (Bull-Case):** Generates 3 to 5 quantified bull theses (e.g., `B1`, `B2`), each explicitly citing upstream artifact keys (e.g., `["A2.PEER_LIST", "A3.AltmanZ"]`).
- **`A6R` (Bear-Case):** Systematically attacks every bull thesis with counter-rebuttals mapped directly to each `bull_id`, scoring each on `probability` and `severity` (`High|Med|Low`).
- **`A6` (CIO-Synthesis):** Resolves the debate, builds the 3-Year Scenario Matrix (Bull, Base, Bear models with Revenue, EPS, FCF/Share), and populates the Danger Zone Dashboard.

### Phase 4: Final Assembly & Vector Scoring (A7)
- **Agent:** `A7` (Final-Assembly)
- **Role:** Compiles executive summary, SWOT matrix, analytical glossary, weighted institutional scorecard (8 categories summing to 1.0, rating band `Buy|Hold|Sell`), and the standardized 13-metric comparison vector.
- **Output Assembly:** Generates `*_report.md`, `*_artifacts.json`, `*_provenance.log`, and `run_summary.json`.

---

## 3. Cryptographic Envelopes & Provenance

### The Envelope Specification (`schemas/envelope_v2_1.json`, `src/envelope.py`)
Every agent communicates exclusively through a standardized JSON envelope containing:
- `schema_version`: `"2.1"`
- `agent`: `{"id": "A1"..."A7", "name": "..."}`
- `timestamp`: UTC ISO-8601 timestamp.
- `params`: Execution configuration (`ticker`, `depth`, `focus`, `weights`).
- `gates`: `{"passed": bool, "notes": [...]}`.
- `sections_md`: Markdown content for specific report sections (`SECTION_0` through `SECTION_18`).
- `artifacts`: Machine-readable structured payload (e.g., `AltmanZ`, `Scorecard`, `BullCase`).
- `sources`: Array of citation objects (`type`, `url`, `doc_date`, `accessed`).
- `warnings`: Warning strings emitted during validation or normalization.
- `metrics`: Runtime telemetry (`data_checksum`, `latency_ms`, `tokens`, `retries`, `injected_defaults`).
- `confidence`: Agent self-assessed confidence score ($0.0 \dots 1.0$).
- `content_checksum`: Deterministic SHA-256 hash of all semantic content (excluding self-referential checksum & telemetry).
- `upstream`: Array of references to previous agent envelopes and their content checksums.

### Provenance Chain (`src/provenance.py`, `src/run_audit.py`)
- Each event is recorded as a line in `*_provenance.log`:
  `TIMESTAMP | ACTOR | MESSAGE | SHA256_HASH`
- The hash of each entry incorporates the hash tail of the entire preceding log:
  $$\text{Payload} = \text{timestamp} \parallel \text{actor} \parallel \text{message} \parallel \text{previous\_tail}$$
- Any tampering with prior entries breaks the hash chain, detected by `verify_chain()` and `controller verify-run`.

---

## 4. File-by-File Codebase Map

### Core Source Directory (`src/`)

| File Path | Description & Responsibilities |
|:---|:---|
| [`src/__init__.py`](src/__init__.py) | Package initialization for `fuwforge`. |
| [`src/config.py`](src/config.py) | Configuration loaders for YAML/JSON configs (`depth_modes.yaml`, `focus_routing.yaml`, `orchestrator.yaml`). |
| [`src/contracts.py`](src/contracts.py) | **Core contract enforcement engine**. Encodes machine-checkable constraints: required artifacts, strict data types, section coverage, scorecard math verification, and bull/bear cross-linkages. |
| [`src/controller.py`](src/controller.py) | **Main CLI Entry Point** (`fuwforge` command). Implements subcommands: `run`, `validate`, `assemble`, `seal`, `verify-run`, `screen`, `diff`, and `monitor`. |
| [`src/envelope.py`](src/envelope.py) | Dataclass definitions for `Envelope` and `AgentInfo`, serialization (`from_dict`, `to_dict`), and section merging helpers. |
| [`src/orchestrator.py`](src/orchestrator.py) | Multi-agent execution coordinator (`FUWForgeOrchestrator`). Manages Phase 1-4 lifecycle, thread pooling, checksum propagation, gating, and report assembly. |
| [`src/provenance.py`](src/provenance.py) | Thread-safe cryptographic hash-chained provenance logger (`ProvenanceLog`) and verifier (`verify_chain`). |
| [`src/run_audit.py`](src/run_audit.py) | Full run verification suite (`verify_run`). Validates envelope schemas, content checksums, upstream lineage references, report outputs, and provenance logs. |
| [`src/runtime_metrics.py`](src/runtime_metrics.py) | Telemetry collector tracking latency, token usage, retry counts, confidence, and status per agent. |
| [`src/sentry.py`](src/sentry.py) | **Real-time Thesis Sentry**. Evaluates incoming documents/news against frozen thesis claims using Jev (TypeSafe), OpenCode Go, OpenRouter, or mock evaluators. Flags contradictions and triggers pipeline re-runs. |
| [`src/validators.py`](src/validators.py) | Low-level JSON schema validator (via `jsonschema.Draft7Validator` with graceful fallback), gate checkers, and artifact presence verifiers. |

### Agents Subpackage (`src/agents/`)

| File Path | Description & Responsibilities |
|:---|:---|
| [`src/agents/__init__.py`](src/agents/__init__.py) | Package initialization for agent modules. |
| [`src/agents/base.py`](src/agents/base.py) | Abstract base classes: `BaseAgent`, `AgentContext` (carries upstream envelopes, checksums, params), and `AgentOutput`. |
| [`src/agents/mock.py`](src/agents/mock.py) | Synthetic/mock implementations for all agents (`A1Mock` through `A7Mock`). Generates fully contract-compliant data for fast local offline testing and unit tests. |
| [`src/agents/model.py`](src/agents/model.py) | **Live LLM Agent Runner**. Connects prompts to model backends (`AgyCliBackend`, `OpenCodeGoBackend`, `OpenRouterBackend`, `OpenAICompatibleBackend`, `GeminiApiBackend`). Handles robust JSON extraction, epistemic prompt injection, envelope normalization, and retry loops. |
| [`src/agents/registry.py`](src/agents/registry.py) | Agent registry system (`AgentRegistry`, `default_registry`, `model_registry`, `mock_registry`). Dynamically resolves agent implementations by provider name. |

### Utilities Subpackage (`src/utils/`)

| File Path | Description & Responsibilities |
|:---|:---|
| [`src/utils/__init__.py`](src/utils/__init__.py) | Package initialization for utilities. |
| [`src/utils/checksum.py`](src/utils/checksum.py) | Hashing utilities: canonical JSON normalization (`_normalize_json`), SHA-256 string/file hashing, and envelope content checksum calculation. |
| [`src/utils/finance.py`](src/utils/finance.py) | Institutional financial calculation engine: Altman Z-Score, 8-variable Beneish M-Score, CAGR, FCF conversion, Accruals Ratio, ROIC, 5-Year Return-Driver Stack, scorecard weight normalization, and 13-metric comparison vector generation. |

---

### Configuration (`config/`)

| File Path | Description & Contents |
|:---|:---|
| [`config/depth_modes.yaml`](config/depth_modes.yaml) | Defines lookback horizons and section skipping rules for `Quick` (3yr lookback, skips IPO/Technicals/Lay summary), `Standard` (5yr lookback), and `Deep` (10yr lookback + exhaustive forensics). |
| [`config/focus_routing.yaml`](config/focus_routing.yaml) | Dynamic routing directives applied when specific focus tags are passed (e.g. `tech_growth`, `deep_value`, `dividend_income`, `distressed_turnaround`). |
| [`config/orchestrator.yaml`](config/orchestrator.yaml) | Orchestrator runtime settings, retry limits (`max_retries: 2`), backoff timings, and thread pool settings. |

---

### Schemas & Templates (`schemas/`, `templates/`)

| File Path | Description & Contents |
|:---|:---|
| [`schemas/envelope_v2_1.json`](schemas/envelope_v2_1.json) | Official JSON Schema definition for COA v2.1 envelopes. Validates property presence, types, and envelope layout. |
| [`templates/inputs_blank.yaml`](templates/inputs_blank.yaml) | Clean intake template for new stock analysis runs. |
| [`templates/inputs_example.yaml`](templates/inputs_example.yaml) | Example intake template for quick reference. |
| [`templates/danger_zone_dashboard.md`](templates/danger_zone_dashboard.md) | Markdown template for red-team forensic danger zone alerts. |
| [`templates/missing_data_table.md`](templates/missing_data_table.md) | Remediation table emitted when an A1 GOD Gate check fails. |
| [`templates/scenario_table.md`](templates/scenario_table.md) | Template for 3-Year Bull/Base/Bear forward projections. |
| [`templates/scorecard.md`](templates/scorecard.md) | Template for the institutional quality scorecard. |

---

### Documentation (`docs/`)

| File Path | Description & Purpose |
|:---|:---|
| [`docs/framework_overview.md`](docs/framework_overview.md) | High-level design document and philosophical foundations of the filings-first COA framework. |
| [`docs/orchestration_flow.md`](docs/orchestration_flow.md) | End-to-end execution flow, state transitions, and checkpointing documentation. |
| [`docs/dependencies.md`](docs/dependencies.md) | System dependency specification and packaging rules. |
| [`docs/checklists.md`](docs/checklists.md) | Pre-flight and post-run operational checklists. |
| [`docs/contracts/`](docs/contracts/) | **Authoritative Human-Readable Agent Contracts**: Detailed specifications for `A1` to `A7`. |
| [`docs/manual_destroyer/`](docs/manual_destroyer/) | Prompts and guides for running the pipeline manually in an interactive chat session ("Destroyer Mode"). |
| [`docs/i_am_become_the_orchestrator/`](docs/i_am_become_the_orchestrator/) | Controller-mode prompt bundle and step-by-step CLI execution kit. |
| [`docs/runbooks/controller_mode.md`](docs/runbooks/controller_mode.md) | Runbook for assembling, sealing, and verifying runs executed manually. |

---

### Test Suite (`tests/`)

| File Path | What It Tests |
|:---|:---|
| [`tests/test_automated_pipeline.py`](tests/test_automated_pipeline.py) | Full automated end-to-end runs across all depth modes and provider fallbacks. |
| [`tests/test_cli_operations.py`](tests/test_cli_operations.py) | Tests CLI subcommands (`run`, `assemble`, `seal`, `verify-run`, `screen`, `diff`). |
| [`tests/test_contracts.py`](tests/test_contracts.py) | Validates contract rules, required artifacts, scorecard math errors, and section skipping. |
| [`tests/test_controller_assembly.py`](tests/test_controller_assembly.py) | Tests assembling controller mode runs from manual JSON envelopes. |
| [`tests/test_finance_and_upgrades.py`](tests/test_finance_and_upgrades.py) | Verifies financial equations (Altman Z, Beneish M, IRR return-driver stack, weight resolver). |
| [`tests/test_model_agents.py`](tests/test_model_agents.py) | Tests model backends, JSON extraction under noisy markdown, prompt builders, and retry mechanics. |
| [`tests/test_run_integrity.py`](tests/test_run_integrity.py) | Tests cryptographic tampering detection, content checksum validation, and hash chain audits. |
| [`tests/test_sentry.py`](tests/test_sentry.py) | Tests Jev Thesis Sentry, contradiction scoring, materiality judgment, and automated re-run triggers. |

---

## 5. CLI Commands Reference

All operations are exposed via `src.controller` (or the `fuwforge` command):

```bash
# 1. Run an automated pipeline with mock provider (instant verification)
python -m src.controller run runs/run_nvda --ticker NVDA --provider mock

# 2. Run with live LLM backends (OpenCode Go, OpenRouter, AGY CLI, or OpenAI)
python -m src.controller run runs/run_nvda --ticker NVDA --provider opencode
python -m src.controller run runs/run_nvda --ticker NVDA --provider openrouter --model google/gemini-2.0-flash-001
python -m src.controller run runs/run_nvda --ticker NVDA --provider agy --model gemini-3.8-flash-low

# 3. Assemble and validate manual "Destroyer Mode" envelopes
python -m src.controller assemble runs/manual_run --ticker AAPL

# 4. Seal envelopes with content checksums
python -m src.controller seal runs/manual_run

# 5. Cryptographically verify an entire run directory
python -m src.controller verify-run runs/run_nvda --strict

# 6. Multi-stock screening and comparative diffing
python -m src.controller screen runs/* --sort quality_score_10 --format markdown
python -m src.controller diff runs/run_nvda runs/run_aapl

# 7. Real-Time Thesis Sentry (monitoring breaking news against frozen thesis)
python -m src.controller monitor runs/run_nvda --doc "NVIDIA reported 15% customer cancellation in datacenter segment" --threshold 0.75
```

---

## 6. Key Data Contracts & Formulas

1. **Altman Z-Score** (`src/utils/finance.py`):
   $$Z = 1.2 X_1 + 1.4 X_2 + 3.3 X_3 + 0.6 X_4 + 0.999 X_5$$
   - $> 2.99 \implies \text{Safe Zone}$, $1.81 \dots 2.99 \implies \text{Grey Zone}$, $< 1.81 \implies \text{Distress Zone}$.

2. **Beneish M-Score** (`src/utils/finance.py`):
   $$M = -4.84 + 0.920 \text{DSRI} + 0.528 \text{GMI} + 0.404 \text{AQI} + 0.892 \text{SGI} + 0.115 \text{DEPI} - 0.172 \text{SGAI} + 4.037 \text{TATA} + 0.0327 \text{LVGI}$$
   - $M > -1.78 \implies \text{High Manipulation Risk}$.

3. **5-Year Forward Expected IRR Stack** (`src/utils/finance.py`):
   $$\text{Expected IRR} \approx \text{Starting FCF Yield} + \text{Fundamental Growth} + \text{Multiple Expansion/Compression} \pm \text{Capital Allocation Tailwind}$$

4. **13-Metric Standardized Comparison Vector** (`comparison_fields` in `A7`):
   `quality_score_10`, `valuation_score_10`, `expected_5y_irr_base_pct`, `downside_case_5y_irr_pct`, `fcf_yield_ttm_pct`, `roic_ttm_pct`, `net_debt_to_ebitda`, `moat_score_10`, `reinvestment_runway_score_10`, `balance_sheet_score_10`, `fragility_score_10`, `evidence_quality_score_10`, `data_completeness_score_10`.
