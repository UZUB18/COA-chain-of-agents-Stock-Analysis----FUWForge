# Public research-data collection check

Automatic collection succeeded for AAPL, analysis date 2025-01-31, Quick depth.

## Retrieved inputs

- 2024 10-K, filed 2024-11-01.
- 2023 10-K, filed 2023-11-03.
- 2022 10-K, filed 2022-10-28.
- Q1 FY2025 10-Q, filed 2025-01-31.
- 2025 proxy, filed 2025-01-10.
- Yahoo daily market history with last retained bar 2025-01-31.

All six documents were loaded through EvidencePack, with cached bytes/text hashes verified and text extraction reproduced. The SEC ticker/CIK directory and submissions responses are saved under discovery/, with their access timestamps and hashes in collection.json. The contact User-Agent is loaded from ignored local configuration and is not placed in evidence records or model prompts.

## Price and derived technicals

- Historical close: **236.00** on 2025-01-31, from the provider's quote.close series.
- SMA(50): **240.2280**.
- SMA(200): **219.6944**.
- Wilder RSI(14): **49.3809**, seeded within the downloaded history.

The current price and other current quote metadata returned alongside Yahoo's historical chart are excluded from the text given to models. Future bars are excluded as well. Historical provider prices may reflect corporate-action adjustments; this is not a certified original point-in-time price archive.

## Honest missing-data policy

Required fields stay present. Allowlisted noncritical data can be explicitly null only with exact field/reason entries in metrics.missing_data. Culture data cannot become a guessed score; unavailable social/flow data cannot become Neutral, Flat, or an invented sentiment distribution. A1 core price/share/cap inputs and A7 scorecard cells have no such exception. Report data-gap tables make limitations visible; gap reasons are checksum-protected semantic content.

## Issuer PDF check

A separate manifest retrieval also downloaded Apple's FY2025 Q1, FY2024 Q4 and FY2023 Q4 statement PDFs. All three produced substantive extracted text with basic/diluted share rows; six release/PDF documents were loaded and re-extracted successfully. The parser initially warned that full CFF-font decoding needs fontTools. That dependency is now installed and recorded; all six cached documents reproduced their extraction with full font decoding. The running Gemini check uses SEC HTML and curated JSON, not these PDFs.

## Limits

This pack is much more complete than the earlier earnings-release-only pack, but it does not contain peer valuation feeds, a culture survey or broad social/news coverage. An assembled output can still be incomplete research and require human review. Cached-byte integrity and structural contracts do not independently verify every generated financial claim, derived calculation or forecast.

Offline safety suite passed **202 tests**, with 3 live tests deselected, including encoded-citation compatibility and multi-agent data-gap assembly guards. All nine native Gemini agents passed strict contracts on their first calls with zero injected defaults. A local missing-data-section collision required report reassembly; no additional paid calls were made. See the final saved-output audit for the completed report, remaining data gaps and provenance limitations rather than treating collection success as independent financial fact verification.
