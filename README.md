```
 ███████╗██╗   ██╗██╗    ██╗    ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
 ██╔════╝██║   ██║██║    ██║    ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
 █████╗  ██║   ██║██║ █╗ ██║    █████╗  ██║   ██║██████╔╝██║  ███╗█████╗  
 ██╔══╝  ██║   ██║██║███╗██║    ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  
 ██║     ╚██████╔╝╚███╔███╔╝    ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
 ╚═╝      ╚═════╝  ╚══╝╚══╝     ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

# Contract-Driven Multi-Agent Research Pipeline

**A deterministic, adversarial-tested engine for equity analysis that survives audit without sacrificing throughput.**

FUWForge implements a seven-agent pipeline (A1–A7) where each stage operates under explicit contract, writes to cryptographically-checksummed JSON envelopes, and refuses to proceed until the next agent validates its input. The result: financial research that's both repeatable and defensible—no hand-waving, no black boxes.

Built on the filings-first methodology from Fired Up Wealth's COA framework, adapted for academic rigor and production deployment.

---

## Architecture at a glance

```
[Intake] → A1 → A2 → A3 → A4 → A5 → A6 → A7 → [Report]
           ▼    ▼    ▼    ▼    ▼    ▼    ▼
         [Envelope checksum gates at every transition]
```

**Seven stages, nine agent roles, machine-checked contracts, one unbroken chain of provenance.**

| Agent | Domain | Output Artifact |
|-------|--------|-----------------|
| **A1** | Source acquisition & validation | Filing inventory, data integrity checksums |
| **A2** | Market structure & competitive analysis | Peer matrices, moat trend, customer/supplier concentration |
| **A3** | Quantitative valuation & forensics | Altman Z, Beneish M, PEG ratios, technical diagnostics |
| **A4** | Leadership, governance & culture | CEO pay ratio, auditor tenure, Glassdoor, ESG momentum |
| **A5** | News, sentiment & flows | Sentiment split, divergence flags, insider/institutional flows |
| **A6B** | Bull case thesis building | Quantified bull theses citing upstream artifacts |
| **A6R** | Bear case interrogation | Counter-rebuttals with probability and severity ratings |
| **A6** | CIO synthesis & risk audit | Danger-zone dashboard, 3-year scenario lab, catalysts/headwinds |
| **A7** | Final report assembly & ranking | Investor-ready report, scorecard, comparison vector, audit trail |

---

## Why FUWForge exists

Most research pipelines are academic thought experiments or black-box SaaS tools. FUWForge is neither:

- **Deterministic by design**: Same inputs, same outputs. Every time.
- **Contract-enforced**: `src/contracts.py` encodes each agent's artifacts, section coverage, scorecard math, and bull/bear linkage. Violations trigger a model repair retry, then halt the run.
- **Adversarially hardened**: A6 exists solely to attack the thesis built by A1–A5.
- **Filings-first discipline**: Primary sources over aggregated data; provenance over convenience.
- **Audit-native**: Every envelope carries a content checksum and the checksums of the upstream envelopes it consumed; `controller verify-run` re-verifies the whole run.
- **Honest normalization**: Runner-supplied placeholder defaults are recorded in `metrics.injected_defaults` and warnings—never silently presented as model output (`--strict` makes them fatal).
- **Dual-mode operation**: Run it automated via orchestrator or manually through CLI—same contracts, same validation.

This isn't a tool for generating content. It's a framework for *defending* analysis.

---

## What's in the box

```
fuwforge/
├── config/          # depth_modes.yaml, focus_routing.yaml, orchestrator.yaml
├── docs/
│   ├── contracts/                     # Human-readable per-agent contracts (A1–A7)
│   ├── runbooks/                      # Controller-mode runbooks
│   ├── manual_destroyer/              # Agent-by-agent manual prompts
│   ├── manual_kit/                    # Supporting manual material
│   ├── i_am_become_the_orchestrator/  # CLI-only control mode
│   ├── framework_overview.md          # Big-picture architecture
│   └── orchestration_flow.md          # Stage-by-stage data flow
├── schemas/         # JSON Schema validation (envelope_v2_1.json)
├── src/
│   ├── orchestrator.py     # Automated multi-agent coordinator
│   ├── controller.py       # CLI: validate, assemble, run, fetch-sources,
│   │                       #      collect-sources, verify-run, seal, screen, diff, monitor
│   ├── contracts.py        # Machine-checkable artifact/section/semantic contracts
│   ├── validators.py       # Envelope + JSON Schema validation
│   ├── envelope.py         # Envelope construction and content checksums
│   ├── provenance.py       # Hash-chained provenance log
│   ├── run_audit.py        # verify-run: checksums, lineage, provenance chain
│   ├── evidence.py         # Public-document retrieval, hash-checked evidence packs
│   ├── public_data.py      # Date-bounded SEC filing + price-history collection
│   ├── web_research.py     # Bounded Gemini Search / URL Context intake
│   ├── data_gaps.py        # Bounded unavailable-data policy (never fake values)
│   ├── sentry.py           # Jev thesis sentry over incoming filings/news
│   ├── runtime_metrics.py  # Runtime metrics capture
│   ├── agents/             # base, registry, mock, model, output_format, output_schema
│   └── utils/              # checksum, finance (deterministic SMA/RSI/valuation math)
├── templates/       # Intake forms, scorecards, dashboards, evidence manifest
├── tests/           # pytest suite; live/network tests are opt-in
├── runs/            # Run outputs (audit artifacts tracked, bulk caches ignored)
└── .github/workflows/ci.yml
```

---

## Setup

Requires Python 3.10+ (CI runs 3.10 and 3.12). No system packages, no database.

```sh
git clone https://github.com/UZUB18/COA-chain-of-agents-Stock-Analysis----FUWForge.git
cd COA-chain-of-agents-Stock-Analysis----FUWForge

# Option A — uv (recommended; creates .venv and installs the package + test extra)
uv sync --extra test

# Option B — plain pip
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[test]"

# Option C — requirements only, no editable install
pip install -r requirements.txt
```

Sanity-check the install and the pipeline end to end with zero API calls:

```sh
.venv/bin/python -m src.controller --help          # every subcommand is listed
.venv/bin/python -m src.controller run runs/smoke \
  --ticker AAPL --company "Apple Inc." --depth Quick --provider mock
.venv/bin/python -m src.controller verify-run runs/smoke
```

`--provider mock` is the default and runs the full A1–A7 contract/checksum path with deterministic fixtures. That is the right check after cloning: if `verify-run` passes on the mock run, the clone is sound and any later failure is a provider/credential or data-coverage issue, not a broken checkout.

Installed as a package you also get the `fuwforge` console script (`fuwforge run ...`); otherwise call `python -m src.controller`.

### Configuration

Copy the example and fill in only what you use — every key is optional, and unconfigured providers stay disabled rather than silently falling back to mock:

```sh
cp .env.example .env && chmod 600 .env
```

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Google AI Studio key for `--provider gemini` |
| `GEMINI_MODEL` | Default Gemini model; `--model` and `FUWFORGE_MODEL` override it |
| `SEC_USER_AGENT` | `Organization your-real-email`, required for SEC filing collection |

Shell environment variables override `.env`. Never commit real keys — `.env` is git-ignored and `.env.example` is the only env file tracked. Use [`python-dotenv`](https://pypi.org/project/python-dotenv/) semantics: the file is read from the project root, no export needed.

### Tests

```sh
.venv/bin/python -m pytest -q           # offline suite; live tests excluded by default
.venv/bin/python -m pytest -m live      # opt-in: needs the AGY CLI + network
```

The default `addopts = -m 'not live'` in `pyproject.toml` keeps CI offline and free. The same command runs on every push via `.github/workflows/ci.yml` (Python 3.10 and 3.12).

### What is committed, and what is not

Runs are reproducible from their inputs, so the repo tracks the audit trail and ignores bulk caches:

- **Tracked**: `runs/**/a*_envelope.json`, `a*_raw.json`, `run_summary.json`, `*_provenance.log`, reports, `evidence.json` manifests, and the extracted summary artifacts that give `verify-run` something to check.
- **Ignored**: cached response bodies (`*.bytes`), text extracted from them, virtualenvs, and local agent scratch (`.pi/`, `.gemini/`, `graphify-out/`, `.playwright-mcp/`).

The SHA-256 of every cached document lives in the committed `evidence.json` and the A1 envelope, so dropping the bodies costs disk, not verifiability. See `.gitignore` for the full ruleset.

---

## Operating modes

### Mode 1: Automated orchestrator

For production runs where you need throughput without sacrificing determinism.

1. Fill `templates/inputs_blank.yaml` with ticker, focus, analysis date, depth mode, and optional scorecard weight overrides.
2. Execute the run: `python -m src.controller run runs/<run_id> --input inputs.yaml --provider mock` (swap `mock` for `agy`, `opencode`, `openrouter`, `openai`, or `gemini` when endpoints are configured). Add `--strict` to fail on injected defaults.
3. Each agent writes its envelope, validates schema/contract/checksum, and hands off. A halted run still writes `run_summary.json` with the reason.
4. Verify the whole run: `python -m src.controller verify-run runs/<run_id>` (schemas, content checksums, upstream lineage, provenance chain).
5. Rank or compare completed runs: `python -m src.controller screen runs/*`, `python -m src.controller diff runs/<a> runs/<b>`.

### Google AI Studio / Gemini API

Use `--provider gemini` for direct [Gemini Developer API](https://ai.google.dev/gemini-api/docs) access. This is separate from the `agy` CLI and from Gemini routed through OpenRouter; requests go directly to Google.

1. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey) for the project with your billing/eligible credits.
2. Set `GEMINI_API_KEY` in the shell that runs FUWForge (`GOOGLE_API_KEY` is also accepted), or copy `.env.example` to `.env` in the project root and put your key there. Gemini automatically reads that project's `.env`, without changing other providers or the process environment. Explicit arguments and shell environment variables override local values. Keep keys out of prompts, run inputs and Git; `.env` is ignored. On Linux/macOS, restrict it with `chmod 600 .env`. Install dependencies with `pip install -r requirements.txt` (or `uv sync --extra test`).
3. Retrieve an evidence pack as described below, then select Gemini explicitly for analysis or thesis monitoring:

```sh
# bash / zsh
export GEMINI_API_KEY="YOUR_AI_STUDIO_KEY"
# fish: set -gx GEMINI_API_KEY "YOUR_AI_STUDIO_KEY"

python -m src.controller run runs/gemini_trial --input inputs.yaml \
  --provider gemini --model gemini-3.8-flash \
  --evidence-pack runs/apple_evidence/evidence.json

python -m src.controller monitor runs/gemini_trial --doc news.txt \
  --provider gemini --model gemini-3.8-flash
```

The default is `gemini-3.8-flash`. To try Pro for deeper analysis, pass `--model gemini-3.1-pro-preview` if available to your project. Use direct API model IDs from the [Google model catalog](https://ai.google.dev/gemini-api/docs/models), not OpenRouter names such as `google/...`; `models/` prefixes are accepted. `GEMINI_MODEL` sets the Gemini default for both commands; explicit `--model` takes precedence. For analysis runs, the existing `FUWFORGE_MODEL` override also takes precedence over `GEMINI_MODEL`.

The backend uses the model's default generation temperature and sends a per-agent canonical JSON schema to Gemini. All model prompts also include the schema and complete accepted upstream artifacts. Blocked, empty, truncated and malformed outer responses are rejected; nested fragments are never accepted as replacement envelopes. Known alternative artifact layouts are mapped without replacing supplied measurements. Real-provider analysis CLI runs (`run`) default to strict mode: they reject missing required measurements and uncached citations instead of filling them with plausible values. `--allow-defaults` is an explicit legacy diagnostic opt-in; `--strict` takes precedence. Explicit missing-data gates halt without inventing numbers. Legacy lenient mode remains for compatibility, but its report warns about runner placeholders and unverified claims. Programmatic callers should also pass `strict=True`.

### Gemini web research (enabled by default for Gemini runs)

`run --provider gemini` now makes **one shared, synchronous Interactions API request** with **Google Search grounding and URL Context** before the nine strict agents. It requires `--auto-sources` or `--evidence-pack`: web search supplements financial feeds; it does not replace dated prices, share counts or SEC statements. Deep Research is **not enabled**, and the intake has no automatic paid retry. Monitoring and other providers are unchanged.

```sh
# Today's analysis: shared web intake + public financial sources
.venv/bin/python -m src.controller run runs/aapl_current_web \
  --ticker AAPL --company "Apple Inc." --depth Quick \
  --provider gemini --model gemini-3.8-flash --auto-sources --strict

# Existing behavior / no additional web-tool calls:
# Add --no-web-search to a Gemini run.
# Search without URL Context: add --no-url-context.
# Accept at most two additional pages: add --web-max-sources 2.
```

The runner considers at most six API citation URLs and accepts at most four additional pages by default (1–6 configurable, subject to the 16-document total). It independently downloads each accepted page, checks public HTTPS/DNS, and derives its publication date from publisher HTML metadata. Pages without clear dates, future publication/modification dates, publications outside the 90-day window, unsupported formats, download failures and pages over 512 KiB remain coverage gaps. Only Google's grounding redirect wrapper gets a bounded, unauthenticated public-URL resolution probe; ordinary source redirects remain refused. Search cannot access paywalled data for us. Financial PDFs still use the existing financial-evidence path.

The original provider response—including citation annotations, search queries/suggestions, URL retrieval metadata and token usage when returned—is retained in a checksum-protected evidence attachment. Every run snapshots and verifies this attachment. Provider research prose is **not** fed to the agents as financial evidence: only independently cached source bodies and coverage limitations are supplied. If an application displays the provider's grounded response, it must preserve citation/Search attribution requirements; stored Search suggestions HTML is not automatically executed or inserted into the report.

**Cost limits:** one intake request, `max_output_tokens=4096` configured and no paid retries. The provider's tool loop may perform multiple model steps; this is not a guaranteed aggregate output-token ceiling. `--web-max-sources` caps cached pages, **not Google's autonomous search-query count or a hard dollar budget**. Search uses the billing project's allowance/rates; URL Context also consumes input tokens. Use Google project billing controls and `--no-web-search` for strict cost avoidance. API/tool failures halt rather than silently substituting ungrounded research. Historical retrieval remains **not a certified point-in-time archive** even when publisher dates satisfy the cutoff. No autonomous function execution or model-generated code execution is enabled; tested local price/technical calculations remain authoritative.

### Retrieved source evidence

The runner can discover SEC filings and collect public price history automatically. Set a real SEC fair-access contact in the ignored project `.env` (or shell), then collect inputs before any paid analysis:

```dotenv
SEC_USER_AGENT="Your organization your-real-email"
```

```sh
python -m src.controller collect-sources --ticker AAPL --analysis-date 2025-01-31 \
  --depth Quick --out runs/apple_public

python -m src.controller run runs/apple_grounded --ticker AAPL --company "Apple Inc." \
  --analysis-date 2025-01-31 --depth Quick --provider gemini --model gemini-3.8-flash \
  --evidence-pack runs/apple_public/pack/evidence.json

# Or collect and analyze in one explicit operation:
python -m src.controller run runs/apple_auto --ticker AAPL --company "Apple Inc." \
  --analysis-date 2025-01-31 --depth Quick --provider gemini --auto-sources
```

`collect-sources` verifies the ticker/CIK pairing, selects annual filings (3/5/10 for Quick/Standard/Deep), the latest available quarter and proxy filed no later than the analysis date, and caches discovery-response hashes. `--cik` overrides identifier discovery but still requires the ticker pairing to match. Daily market histories use Yahoo chart data, falling back to Stooq only if valid; current quote metadata and future bars are excluded from model context. SMA/RSI values are derived from retrieved closes, not model guesses. Price-provider corporate-action adjustments apply: this is **not a certified point-in-time backtest dataset**. Missing critical price/share/statement coverage remains a reason to halt. Noncritical gaps use explicit nulls and a structured `metrics.missing_data` ledger: for example, no culture survey means `GlassdoorScore=null`, not a guessed 4.1; no flow dataset means a null artifact, not `Neutral`/`Flat`. Each allowed null needs a precise field/reason entry and appears in the report's data-gap table. Core A1 market/share inputs and A7 scorecard cells have no exception. Gap reasons are semantic, checksum-protected content, not disposable telemetry. These incomplete-coverage reports still require human review.

Alternatively, retrieve explicit documents from a manifest:

```sh
python -m src.controller fetch-sources \
  --manifest templates/evidence_manifest_example.json --out runs/apple_evidence

python -m src.controller run runs/apple_grounded \
  --input templates/inputs_example.yaml --provider gemini --model gemini-3.8-flash \
  --evidence-pack runs/apple_evidence/evidence.json --strict

python -m src.controller verify-run runs/apple_grounded --strict
```

A manifest specifies the ticker and a list of public HTTPS document URLs, titles, types and publication dates. The Apple example downloads published financial results; **it is not a complete research pack**. Add date-appropriate market prices, filing/proxy documents, peer data, governance/culture sources and relevant news to cover the required artifacts. If those inputs are missing, a strict run may halt with a missing-data gate: that is intentional, not permission to fabricate them.

`fetch-sources` caches original response bytes and extracted text with actual retrieval timestamps and SHA-256 hashes. A run verifies and snapshots the pack under its own `evidence/` directory, anchors the pack in its A1 checksum/provenance, and supplies the text to every agent as untrusted data. `verify-run` detects changed cached bytes, source metadata or research attachments. Model-supplied access timestamps are ignored. Without a pack, sources are explicitly unverified (never a synthetic SEC access claim); strict model runs require a pack before making paid API calls.

Retrieval supports HTML, text, CSV, JSON, XML and text-bearing PDFs, with at most 16 documents and 8 MiB per document. PDF extraction rejects encrypted/image-only documents and files over 300 pages; it does not invent OCR content. Redirects, credential-bearing URLs and private-address targets are refused. SEC URLs require `SEC_USER_AGENT` in your shell or ignored project `.env` with your organization/contact information; the contact header is sent only to SEC hosts, never to Google or other data providers. Long documents are explicitly excerpted in model context, prioritizing statement/share/pay-ratio sections over introductions. Publication dates come from the supplied manifest (or SEC filing metadata for automatic collection); documents dated after the analysis date are rejected to reduce look-ahead risk. Hashes prove which bytes were supplied, **not** that every document or generated claim is true. Review citations, data coverage, calculations and assumptions; incomplete inputs must remain visible.

**Billing:** Verify your credit eligibility on the [AI Studio billing page](https://aistudio.google.com/billing). Google's [billing guide](https://ai.google.dev/gemini-api/docs/billing) excludes newer $300 Google Cloud welcome/free-trial credits from Gemini API / AI Studio usage (accounts opened after March 2, 2026). Other promotional credits have their own terms. This integration is not Vertex AI. A full pipeline makes multiple API calls, including retries; monitor usage before running Deep mode. Merely setting a key does not enable paid analysis: `run` still defaults to `mock`, and `monitor --provider auto` retains its Jev/mock behavior. Explicit Gemini errors are not silently replaced by mock results.

### “Destroyer Mode” manual cockpit
1. Open `docs/manual_destroyer/prompts.md` (or the CLI-only bundle in `docs/i_am_become_the_orchestrator/`).
2. March through A1–A7 prompts, pasting responses into the provided markdown shells.
3. Validate each JSON envelope with `python -m src.controller validate <envelope> --expected-checksum <A1 checksum>`; the same contract checks run when you assemble.
4. Seal the run for tamper evidence (`python -m src.controller seal <run_dir>`), assemble, then verify (`python -m src.controller verify-run <run_dir> --strict`).
5. Drop artifacts into the Danger-Zone dashboard (`templates/danger_zone_dashboard.md`) if you need a red-team snapshot.

## Agent roster (A1–A7, 9 roles)

| Agent | Mandate | Key Artifacts |
|-------|---------|---------------|
| A1 | Harvest filings + sanity-check source data | Raw doc inventory, intake checksum, market cap reconciliation |
| A2 | Business, market, peer synthesis | Peer list (≥3), moat trend, concentration metrics |
| A3 | Quant valuation + forensic technicals | Altman Z, Beneish M, PEG ratios, technical diagnostics |
| A4 | Leadership, governance & culture | CEO pay ratio, auditor tenure, Glassdoor scores, ESG momentum |
| A5 | News, sentiment & flows | Sentiment distribution, divergence checks, insider transactions |
| A6B | Bull case thesis building | 3–5 quantified bull theses mapped to upstream artifacts |
| A6R | Bear case interrogation | Probability/severity-rated counter-rebuttals mapped to bull IDs |
| A6 | CIO synthesis & stress testing | DangerZoneDashboard, 3-year ScenarioTable, catalysts, headwinds |
| A7 | Final report assembly & ranking | Weighted Scorecard, SWOT, Glossary, 13-metric comparison vector |

## Quickstart checklist

1. Clone the repo, follow **Setup** above, and confirm the mock smoke run passes `verify-run`.
2. Skim `docs/framework_overview.md` for the big picture and `architecture.md` for the codebase map.
3. Set up credentials if you want a real provider: `cp .env.example .env`, add `GEMINI_API_KEY`, and add `SEC_USER_AGENT` if you plan to collect SEC filings.
4. Pick a depth mode in `config/depth_modes.yaml` (`Quick` / `Standard` / `Deep`) and adjust if needed.
5. Fill out `templates/inputs_blank.yaml` with your run details.
6. Choose automated vs. manual mode and follow the matching playbook above.
7. Verify, then commit the assembled report, JSON artifacts, and provenance log to lock in the audit trail (bulk caches stay local; see `.gitignore`).

## Safety rails & provenance

- Every envelope is validated against `schemas/envelope_v2_1.json` and against the machine-checkable contract in `src/contracts.py` (required artifacts, types, section coverage, scorecard arithmetic, bull/bear linkage).
- Hard gates: any agent returning `gates.passed=false` halts the run; A1's halt emits the Missing Data Table.
- Content integrity: each envelope stores a `content_checksum` over its sections/artifacts/sources plus the checksums of the upstream envelopes it consumed. `controller verify-run` recomputes all of it and re-verifies the provenance hash chain.
- Honest defaults: normalization placeholders are recorded (`metrics.injected_defaults`) and penalize `data_completeness_score_10`; `--strict` / `FUWFORGE_STRICT=1` turns them into failures.
- Every run writes `run_summary.json` (status, metrics, checksum, halt reason) and a report header with run metadata.
- Manual controllers can seal legacy envelopes (`controller seal`) before verifying; tampered envelopes are rejected rather than silently accepted.

## Gratitude

This stack exists because of Eric and the Fired Up Wealth community—YouTube, Patreon, Discord. The filings-first discipline, adversarial loops, and quant-backed synthesis you see here are his fingerprints. FUWForge just adds the rails so teams can run the playbook at scale without losing the heat.
