# Gemini web-tool smoke check

Date: 2026-10-06. Model: `gemini-3.8-flash`.

## Observed

- One synchronous Interactions request; no paid retry and no Deep Research.
- API returned `completed` and both Search and URL Context call/result step types.
- Provider usage reported two Google Search queries.
- Two independently downloaded dated source pages were added to the existing six-document pack.
- Four candidates were excluded because publisher publication metadata was missing or ambiguous.
- Cached bytes, extracted text and full research-response attachment passed local evidence validation.
- Provider prose and Search suggestions HTML are excluded from analysis source text; only cached pages and coverage limitations are supplied.

## Final offline verification

- Full suite: **229 passed, 3 live tests deselected**.
- Targeted provider/evidence/public-data/web-intake tests initially passed 105 tests.
- The first full suite found three stale strict-mode CLI fixtures; those fixtures now explicitly disable web intake when isolating strict-mode precedence. The final full rerun passed.
- Strict pipeline replay checks research attachment snapshots/checksums and verifies that attachment tampering fails `verify-run`.
- Python compilation and `git diff --check` passed. Credentials remain local with `.env` mode `600`.

## Scope and limits

This was a web-tool/evidence-merge test, **not a current-date financial analysis**. The reused baseline financial pack is dated 2025-01-31; no nine-agent generation or current investment recommendation was produced. Source downloads and publisher metadata do not certify point-in-time availability or financial truth. One API request may contain multiple billable searches/model steps; configured output limits are not a hard aggregate token/query/dollar cap.

The original smoke trace's `requested_at` field was recorded after the API response; the collector was subsequently corrected to record both request-start and completion timestamps. The smoke trace is preserved unchanged.

See `SMOKE_CHECK.json` for observed usage and `pack/gemini_web_research.json` for the complete provider response, citation/retrieval metadata, accepted sources and exclusions. The response attachment is hash-linked in `pack/evidence.json`.
