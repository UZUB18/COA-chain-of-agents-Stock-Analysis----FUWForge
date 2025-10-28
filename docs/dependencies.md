# Dependency & Integration Guide

This guide inventories the software libraries, APIs, and third-party data feeds you need to operationalize FUWForge (Fired Up Wealth) on top of the COA v2.1 framework. It distinguishes between **core runtime requirements**, **optional enhancements**, and **data source integrations** aligned with each agent contract.

## 1. Runtime Environment

- **Python**: 3.10 or newer recommended. The scaffolding uses standard library modules plus optional dependencies (see below).
- **Package management**: `pip`, `uv`, or `poetry` for virtual environment isolation.
- **Shell tooling**: GNU core utilities; `make` optional for scripted runs.

## 2. Python Package Dependencies

| Package | Purpose | Mandatory? | Notes |
|---------|---------|------------|-------|
| `pyyaml` | Load YAML configs (`config/*.yaml`). | Recommended | Falls back to JSON parsing if absent, but YAML support is expected. |
| `jsonschema` | Validates envelopes against `schemas/envelope_v2_1.json`. | Recommended | Without it, validation downgrades to basic structural checks. |
| `requests` or `httpx` | Fetch filings, prices, news in agent implementations. | Yes (for production) | Choose one HTTP client and standardize retries, backoff, headers. |
| `pandas` | Tabular manipulation for financial series, peer tables, and QC checks. | Highly recommended | Simplifies 5–10 year reconcilations and technical indicators. |
| `numpy` | Numerical calculations (Altman Z, Beneish, scenarios). | Highly recommended | Can be bundled with pandas. |
| `scipy` (optional) | Advanced statistical tests or optimization in valuation models. | Optional | Useful for DCF sensitivity or Monte Carlo. |
| `tenacity` (optional) | Retry helpers for API calls. | Optional | Helps enforce orchestrator retry discipline. |
| `loguru` / `structlog` (optional) | Structured logging. | Optional | Valuable for audit logging beyond provenance chain. |

When wiring actual agent logic, add the relevant LLM SDK (`openai`, `google-generativeai`, `anthropic`, etc.) and any SDKs provided by your data vendors.

## 3. Large Language Model APIs

The FUWForge orchestrator is agent-agnostic; pick the stack that best matches your compliance and latency requirements. Typical choices:

- **OpenAI** (`gpt-4o`, `o3-mini`, `gpt-4.1`) via the `openai` Python SDK. Supports deterministic JSON responses with function calling.
- **Google Gemini** (`Gemini 1.5 Pro/Flash`) via `google-generativeai` or Vertex AI. Can handle long context filings.
- **Anthropic Claude** (`Haiku`, `Sonnet`, `Opus`) via `anthropic` SDK – strong for structured reasoning.
- **Azure OpenAI** if you require private networking/SOC compliance.
- **Local/self-hosted** (`llama.cpp`, `vLLM`, `Mistral`) for on-prem deployments; integrate via REST or gRPC.

For adversarial loops, ensure the LLM supports parallel or sequential conversations with preserved context. Implement deterministic temperature and system prompts matching the contract files under `docs/contracts/`.

## 4. Financial & Market Data APIs

Agent A1 (GOD Gate):
- **SEC EDGAR API** (https://www.sec.gov/edgar/sec-api-documentation) for 10-K/10-Q/8-K filings. Respect 10 req/sec limit and user-agent policy.
- **Exchange price feeds**: IEX Cloud, Polygon.io, Alpha Vantage, or direct exchange connections for frozen price timestamps and historical OHLC data.
- **Refinitiv / Bloomberg / FactSet** (if licensed) can streamline raw data pack assembly and reconciliation.

Agent A3 (Quant/Valuation/Technicals):
- **Fundamental datasets**: Financial Modeling Prep, Intrinio, Quandl/Zacks for standardized financial statements.
- **Technical indicators**: Use price feeds (above) plus libraries like `ta` or custom calculations in pandas/numpy.
- **Peer multiples**: same providers, or compute manually from raw statement data.

Agent A5 (News/Sentiment/Flows):
- **News APIs**: Benzinga, FactSet StreetAccount, Aylien, AlphaSense, or Bloomberg News. Ensure timestamps and source metadata stored.
- **Social sentiment**: Sprout Social, Reddit/Twitter firehose providers, or vendor APIs like Quiver Quantitative.
- **Insider Form 4**: SEC EDGAR (ownership endpoint) or Quiver Quant. Institutional holdings via 13F aggregators (WhaleWisdom, FactSet).

Agent A4 (Governance/ESG):
- **Proxy Statements (DEF 14A)**: SEC EDGAR.
- **ESG ratings**: MSCI, Sustainalytics, Refinitiv ESG, ISS (license required).
- **Culture data**: Glassdoor, Comparably, LinkedIn insights (public or via APIs). Respect TOS and rate limits.

Agent A6 (Synthesis) & A7 (Final Assembly):
- Consume upstream artifacts—no new external data ideally. If scenario modeling requires macro inputs, draw from trusted macro data APIs (FRED, IMF) and log in provenance.

## 5. Storage & Immutability

- **Object storage** (S3, GCS, Azure Blob) or secure filesystem to persist run artifacts.
- **Database** (Postgres, SQLite) optional for indexing runs, tracking metrics, or orchestrator state.
- **Checksum utilities** rely on SHA-256; no external dependency beyond Python stdlib.

## 6. Observability & Security

- **Monitoring**: Prometheus/Grafana or vendor APM for agent latency, token usage, failure rates.
- **Secrets management**: Vault, AWS Secrets Manager, Doppler, or `.env` files via `python-dotenv`.
- **Identity & access**: Ensure API keys for data/LLM providers stored securely; implement per-agent IAM if running in cloud.

## 7. Optional Enhancements

- **Task queue**: Celery, Dramatiq, or Prefect to distribute agent runs across workers.
- **Feature store**: For reuse of recurrent calculations (e.g., standardized ratios).
- **Document store**: ElasticSearch/OpenSearch or Weaviate for rapid retrieval of filings and transcripts.
- **Vector search**: If you plan retrieval-augmented prompts for filings, integrate with FAISS/unstructured ingestion pipeline.

## 8. Implementation Checklist

1. Stand up Python environment with core dependencies (`pyyaml`, `jsonschema`, HTTP client, pandas/numpy).
2. Integrate chosen LLM SDK(s); confirm JSON or function-call capability for schema compliance.
3. Wire data provider clients for filings, prices, fundamentals, news, sentiment, and ownership flows.
4. Configure credentials/secrets and rate-limit policies per provider.
5. Extend agent stubs in `src/agents` to call APIs, transform data, and populate envelopes with required artifacts.
6. Implement automated tests/mocks to validate agent outputs without hitting live APIs.
7. Instrument provenance logging and monitoring dashboards.
