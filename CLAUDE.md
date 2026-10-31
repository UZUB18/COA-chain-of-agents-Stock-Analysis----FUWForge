# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FUWForge is a contract-driven multi-agent equity-research pipeline. Seven stages / nine roles (A1 → A2–A5 in parallel → A6B bull → A6R bear → A6 CIO synthesis → A7 final assembly) exchange checksummed JSON envelopes. Each envelope is validated against a schema and a machine-checkable contract before the next stage can run. The design rule that matters most: **never fabricate data**. Missing values stay `null` and get a reason in `metrics.missing_data`. Placeholder defaults are recorded in `metrics.injected_defaults`, and `--strict` makes them fatal. Uncomputable calculators return an explicit *unavailable* result.

**`docs/CODEBASE_REFERENCE.md` is the deep reference** (run lifecycle, CLI flags, prompt/normalization rules, contracts per agent, audit levels, execution store, facts/binding/evidence internals, current work in progress, and a list of known bugs and doc drift). Read the relevant section before changing a subsystem. `architecture.md` has the file-by-file map. `docs/contracts/A*_*.md` hold the human-readable per-agent contracts. `docs/audit-levels.md`, `docs/fact-binding-policy.md` and `docs/sec-availability-policy.md` define what verification does and does not prove.

## Commands

Use `.venv/bin/python` (Python 3.12). The system Python may lack pytest. Set up with `uv sync --extra test` or `pip install -e ".[test]"`.

```sh
.venv/bin/python -m pytest -q                                  # offline suite (live tests excluded via addopts)
.venv/bin/python -m pytest tests/test_contracts.py -q          # one file
.venv/bin/python -m pytest tests/test_contracts.py::test_mock_envelopes_satisfy_contracts -q
.venv/bin/python -m pytest -m live                             # opt-in: needs AGY CLI + network (do not run without authorization)
.venv/bin/python -m compileall -q src evals tests              # syntax check

# Deterministic eval scorer (CI requires the clean fixture to pass)
.venv/bin/python evals/score_research.py evals/research_cases.jsonl \
  tests/fixtures/eval/sample_predictions_clean.jsonl --markdown --fail-on-errors

# Offline end-to-end smoke (mock provider is the default; zero API calls)
.venv/bin/python -m src.controller run runs/smoke --ticker AAPL --company "Apple Inc." --depth Quick --provider mock
.venv/bin/python -m src.controller verify-run runs/smoke --strict
.venv/bin/python -m src.controller export-packet runs/smoke --output packet.json
```

The CLI entry point is `src/controller.py:main`. It is also installed as the `fuwforge` console script. Subcommands: `run`, `validate`, `assemble`, `seal`, `verify-run`, `fetch-sources`, `collect-sources`, `screen`, `diff`, `monitor`, `bundle-evidence`, `verify-evidence`, `restore-evidence`, `export-packet` and `wizard`. CI (`.github/workflows/ci.yml`, Python 3.10 and 3.12) also builds a wheel and an sdist and runs the mock smoke from a clean venv outside the repo. Resource loading must keep working from an installed package, not just from the checkout (see below). No linter is configured.

## Architecture (cross-file view)

- **Orchestration**: `src/orchestrator.py` (`FUWForgeOrchestrator`) runs the phases. A1 is the gate: `gates.passed=false` halts the run and emits the Missing Data Table. A2–A5 run in a thread pool, then the A6B→A6R→A6→A7 chain runs. A run that halts after agent dispatch writes `run_summary.json`; preflight refusals leave existing runs unchanged.
- **Agents**: `src/agents/registry.py` resolves implementations by provider. `mock.py` produces contract-compliant fixtures for all roles and is what most tests use. `model.py` holds the live backends (`agy`, `opencode`, `openai`/OpenAI-compatible, `gemini`), prompt building, JSON extraction, envelope normalization and a single repair call. `output_schema.py`/`output_format.py` define the per-agent canonical JSON schema sent to providers.
- **Envelope integrity**: `envelope.py` + `utils/checksum.py` compute `content_checksum` over every top-level key except `metrics` and the checksum itself (`metrics.missing_data` is re-injected and hashed; all other metrics, including `injected_defaults` and `source_grounding`, are not covered). Each envelope records the checksums of the upstream envelopes it consumed. `provenance.py` keeps a hash-chained `*_provenance.log`. If you change what goes into a checksum, existing runs and receipts become invalid.
- **Validation layers**: `validators.py` handles JSON Schema (`schemas/envelope_v2_1.json`). `contracts.py` handles required artifacts, types, section coverage, scorecard arithmetic and bull/bear id linkage. `run_audit.py` (byte and hash verification) and `run_semantic_audit.py` (calculation replay, fact binding, completeness) back `verify-run`. Their audit levels are reported **independently** and must never be collapsed into one "verified" claim.
- **Facts and calculations**: `facts.py` holds the typed fact ledger with anti-look-ahead and restatement tracking. `sec_facts.py` ingests SEC CompanyFacts/XBRL. `calculation_witness.py` records the formula, version and inputs for every reported number. `fact_binding.py` accepts a cited input only if it **exactly equals** the fact's value, with matching unit, period, basis and availability; there is no blanket tolerance. `forensic_inputs.py` computes Beneish indices and `altman_inputs.py` the Altman Z from bound facts at runner level (Altman market equity uses the versioned completed-day close; unavailable with a documented gap otherwise). A date-only cutoff means end of that UTC day (`facts.cutoff_instant`). Deterministic math lives in `utils/finance.py` and `utils/beneish.py`.
- **Evidence**: `evidence.py` (fetch-sources, hash-checked packs), `public_data.py` (date-bounded SEC filings and Yahoo/Stooq prices) and `web_research.py` (one bounded Gemini Search/URL Context intake per Gemini run). A run snapshots its pack under `<run>/evidence/`. Source text goes to agents wrapped as untrusted data. Documents dated after the analysis date are rejected. New packs record checksum-covered `intake_coverage` (SEC coverage, omitted oversized older annuals) and a versioned daily-bar market transform that excludes the collection-day bar. `evidence_bundle.py` handles portable bundles.
- **Durable resume**: `execution_store.py` keeps a SQLite receipt ledger (per-thread connections plus WAL; do not switch to a shared connection). `execution_identity.py` handles content-addressed request identity. `--resume` reuses only validated receipts with an unchanged identity, and reuse must stay read-only, never mutating the sealed envelope. Reuse never rewrites `*_raw.json`. An uncertain provider outcome becomes `unknown_completion` and needs `--recovery-choice retry|abandon`; so does a failed receipt with a saved response once zero-cost local recovery has failed (the choice applies per receipt; no paid re-dispatch without it). The store enforces `ALLOWED_TRANSITIONS`; validated receipts are never demoted. The execution key (identity v2) includes a digest of the output-shaping code. Durable mode (always on for automated runs) allows at most one repair call, and only after a provider response was recorded and failed parsing/validation (`ValueError`); transport errors become `unknown_completion` instead of retrying (`src/agents/model.py` `ModelAgent.run`).
- **Outputs**: `research_packet.py` handles `export-packet`, which is always `pending_human_review`. `sentry.py` monitors theses (Jev/TypeSafe, Gemini or mock).
- **Resources**: `src/resources.py` resolves `config/`, `schemas/`, `templates/` and contract docs from either the checkout or `src/_packaged_resources` in installed builds (populated by `setup.py`/`MANIFEST.in`). Load non-code files through it, not through hardcoded repo-relative paths.
- **Config**: `config/depth_modes.yaml` (Quick/Standard/Deep lookbacks and section skipping), `focus_routing.yaml` and `orchestrator.yaml`.

## Conventions and constraints

- Real-provider `run` defaults to strict mode. Provider errors halt; they never fall back to mock silently. Keep that behavior.
- Tests are offline and deterministic. `tests/conftest.py` provides AMD evidence fixtures decompressed from `tests/fixtures/amd/`. Mark anything needing network or credentials `@pytest.mark.live`.
- Do not make paid or live provider calls, commit, or alter archived runs under `runs/` without explicit authorization. `.env` holds real keys: never print it.
- `runs/`: audit artifacts (envelopes, `run_summary.json`, provenance logs, `evidence.json`) are tracked; cached bodies (`*.bytes`, extracted text) are git-ignored.
- In docs and reports, do not overstate guarantees. Evidence is hashed, not signed. Local idempotency is not exactly-once billing. `--independent-bear` is experimental and off by default. The Beneish −1.78 threshold is a screening policy, not a fraud finding. `HANDOFF.md` (2026-10-08) is partly outdated; §9 of `docs/CODEBASE_REFERENCE.md` has the current list of limits and bugs.
- `--strict` is passed explicitly to the orchestrator; `FUWFORGE_STRICT` remains an input default and the CLI does not modify it.
- `seal` checks manifest role and output anchors, records forced overrides, and leaves no-op runs unchanged. Abrupt termination during a multi-file seal can require recovery.
- `COA-chain-of-agents-Stock-Analysis----FUWForge/` (a nested directory) contains reference copies and upgrade plans; it is not the live source.
