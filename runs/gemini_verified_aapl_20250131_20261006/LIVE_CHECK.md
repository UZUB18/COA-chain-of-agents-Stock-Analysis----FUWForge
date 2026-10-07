# Final Gemini verification — AAPL as of 2025-01-31

## Result

- Direct Google AI Studio / Gemini Developer API, model `gemini-3.8-flash`.
- Quick mode; at most one retry allowed per agent. All nine agents succeeded on their **first** requests.
- **9 paid generation calls**, **324.44 seconds**. Report repair/revalidation made **0 additional calls**.
- All nine saved envelopes passed strict contracts with **zero injected defaults**.
- Final report, artifacts, source snapshot, lineage and local replay provenance passed `verify-run --strict`, with no errors or warnings.
- Reassembled artifacts and semantic envelope checksums were compared with the original live outputs and remained unchanged.
- Offline suite: **202 passed, 3 live tests deselected**. Compilation and whitespace checks passed.

## Actual source collection

Automatic public retrieval collected three annual SEC filings, the date-appropriate Q1 FY2025 quarter, the proxy, and Yahoo daily history through the analysis cutoff. Original bytes and extracted/curated text are cached, hash-checked and snapshotted into the run. SEC contact configuration is local and ignored by Git; it is not included in model prompts or sent to other providers.

The pipeline saw historical close **236.00**, not Yahoo's current quote metadata. Source-derived SMA(50), SMA(200) and RSI(14) matched the saved A3 artifacts. The proxy-supported CEO pay ratio was **650**, with Ernst & Young identified as auditor. Unavailable culture data remained `GlassdoorScore=null`; FCF yield remained null rather than a fallback number. The model's comparison values and original scores were preserved; weighted overall arithmetic gives **7.46 / 10, Hold**. Ratings/returns are model judgments and estimates, not independently verified investment advice.

Text-bearing Apple financial PDFs were separately downloaded and reproduced successfully. FontTools is now installed for complete CFF font decoding; the existing six-document PDF/release cache still reproduced its text hashes after installation.

## Local assembly recovery and audit limitation

The original generation completed all nine agents but its local assembler failed because several agents emitted different `MISSING_DATA_TABLE` sections. The assembler now aggregates those diagnostics by agent while retaining conflict rejection for ordinary numbered sections. An offline regression covers both behaviors.

The first in-place recovery helper replaced the original generation provenance log because it did not load existing entries before flushing. That helper is corrected, but the original event hash chain was not recovered. Original accepted envelopes, raw responses, original generation status and console evidence remain available under `runs/gemini_strict_recheck_20261006_212200/` and the ignored task output.

This final directory was therefore built through a **fresh controller replay of the saved live outputs**, recording actual local revalidation/assembly events. It did not generate mock replacements or call Gemini again. Its provenance is a valid local-replay trail, **not a reconstruction of the original generation trail**. The replay summary's 1ms acceptance timings refer to local file ingestion, not paid inference. `FINAL_VERIFICATION.json` records this distinction explicitly.

## Remaining research limitations

- Explicit gaps include forward consensus/PEG, peer valuation data, culture/attrition, social sentiment, insider/institutional flows and complete litigation/regulatory coverage.
- Statement excerpt limits left annual cash-flow inputs unavailable to the models, despite complete filings being cached. Beneish and TTM FCF yield were withheld, not guessed. Better table/section extraction is still needed for complete quantitative coverage.
- Historical market data may reflect provider corporate-action adjustments; current ticker/CIK mapping and filing-date cutoffs are not a certified intraday point-in-time backtest dataset.
- Byte integrity, source inventory and structural contracts **do not independently establish that every generated factual claim, ratio or forecast is correct**. The report visibly lists data gaps and requires human financial review.

## Artifacts

- `AAPL_2025-01-31_report.md`
- `AAPL_2025-01-31_artifacts.json`
- `AAPL_2025-01-31_provenance.log`
- `FINAL_VERIFICATION.json`
- `run_summary.json`
- Nine accepted agent envelopes/raw outputs and the `evidence/` snapshot.
