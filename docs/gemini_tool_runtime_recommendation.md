# Gemini research runtime recommendation

Checked official Google documentation on 2026-10-06. No paid Gemini calls were made for the documentation review. The subsequent implementation now uses one synchronous, stateless Interactions REST request with Search + URL Context for Gemini CLI intake, retaining the existing generateContent analysis backend. No SDK dependency/migration or Deep Research was added. One subsequent live smoke request verified both tools, two search queries and two independently cached dated pages; see `runs/gemini_web_smoke_20261006_215653/SMOKE_CHECK.json` and README usage.

## Recommended stack

- Keep the documented stable `gemini-3.8-flash` model, already verified for this project's nine-agent structured analysis.
- Use the official Python `google-genai` SDK, version 2.3.0 or later, for the new research/tool integration.
- Prefer the **Interactions API** for new integration. Google's current overview states it is generally available since June 2026 and recommended for new projects; `generateContent` remains fully supported but is described as legacy. Existing working analysis calls do not need an immediate wholesale migration.
- Use stateless `store=false` for ordinary synchronous research/analysis by default. This disables interaction-resource storage/state chaining, not all provider processing. Background agent execution requires storage.

## Tools

1. **Google Search grounding:** discover recent news, issuer releases, competitors and relevant public disclosures. Gemini 3.8 Flash is explicitly listed as supported. Preserve the search-call/result steps, inline URL citation annotations and required Search suggestions/attribution behavior. Search alone is not a reliable stock-price or audited-statement feed.
2. **URL Context:** read specified public pages and PDFs. Gemini 3.8 Flash is explicitly supported. Up to 20 URLs/request and 34 MB/URL; it does not follow nested links automatically and does not support paywalled content. Preserve retrieval results/status and citation annotations. Tool input content is billed as input tokens.
3. **Function calling:** expose controlled local tools for SEC financial facts, prices, derived metrics and cached evidence. The app executes these tools and validates arguments/results; this is how we preserve exact measurements, date boundaries and byte/hash auditability. Interactions currently lacks Python automatic function calling, so implement the loop explicitly.
4. **Code execution:** optional sandboxed Python for exploratory calculations/charts (30-second runtime; no additional tool fee, normal token billing). Keep authoritative financial calculations in deterministic local code with tests rather than relying on generated code as the sole validator.

## Suggested workflow

```text
SEC filings/XBRL + market feeds + Google Search discovery
    -> selected URLs / URL Context + controlled runner downloads
    -> cached source documents and extraction / field-level provenance
    -> existing strict nine-agent analysis
    -> calculation checks and gap-aware report
```

Do not enable repeated broad search separately for every agent. Start with one bounded research/intake step, snapshot its evidence, and share it across the chain. Never send credentials, private local configuration or unrelated private data into search queries. Provider-grounded citations/retrieval status are not equivalent to runner-cached source bytes; retain that distinction.

Gemini 3 supports structured output combined with built-in tools, but the structured-output documentation labels this combination **preview**. Separate grounded intake from strict schema-only analysis initially, then capability-test any combined path before enabling it in production.

## Why not Deep Research by default?

The current Deep Research / Deep Research Max agents are preview, require background execution and storage, do not support structured output and cannot receive custom function-calling tools (remote MCP is supported for these agents). Most tasks complete within 20 minutes; maximum research time is 60 minutes. Google's estimates are approximately $1–$3/task for Deep Research and $3–$7/task for Max, subject to preview-rate changes. Useful as an optional human-reviewed Deep-mode source-discovery supplement, not a drop-in replacement for the contract-bound nine-agent runtime.

## Pricing caveats

The current Gemini 3.8 Flash paid standard tier lists $0.75/M input tokens and $3.75/M output tokens through 2026-12-31, increasing to $1.50/$7.50 from 2027-01-01. Search pricing lists 5,000 included search requests/month shared across Gemini 3.x, then $14/1,000 requests; the Search guide explains that multiple search queries in one model request can be separately billable uses. Availability, free allowances and promotional-credit eligibility must be checked for the actual billing project. Set budgets and retain usage metadata.

## Official sources

- [Interactions API overview](https://ai.google.dev/gemini-api/docs/interactions-overview)
- [Google Search grounding](https://ai.google.dev/gemini-api/docs/google-search)
- [URL Context](https://ai.google.dev/gemini-api/docs/url-context)
- [Function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Code execution](https://ai.google.dev/gemini-api/docs/code-execution)
- [Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Deep Research](https://ai.google.dev/gemini-api/docs/deep-research)
- [Models](https://ai.google.dev/gemini-api/docs/models)
- [SDK libraries](https://ai.google.dev/gemini-api/docs/libraries)
- [Pricing](https://ai.google.dev/gemini-api/docs/pricing)
