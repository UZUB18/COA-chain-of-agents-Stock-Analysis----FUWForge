# Beneish M-Score Calculator Impact Report

This report documents the empirical audit of archived runs in `runs/` following the correction and versioning of the deterministic Beneish M-Score calculator (`beneish_m_score` v2.0.0, documented in [`docs/calculator-versions.md`](calculator-versions.md)).

---

## 1. Classification Categories & Audit Methodology

Each archived directory under `runs/` was inspected for `*_artifacts.json`, `a3_envelope.json`, and `a3_raw.json` to evaluate the state of the A3 `BeneishM` artifact.

Categories evaluated:
- **`changed`**: Runs where the 8 input indices were persisted, and recomputing them under calculator v2.0.0 yields a score differing from the archived record.
- **`unchanged`**: Runs where the 8 input indices were persisted, and recomputing them under calculator v2.0.0 yields the exact same score.
- **`not_using_helper`**: Directories that do not represent full multi-agent pipeline executions or contain no A3 quantitative analysis envelopes (e.g., standalone evidence packs, discovery traces, diagnostic tests).
- **`inputs_unavailable`**: Runs where the 8 required input ratios (`dsri`, `gmi`, `aqi`, `sgi`, `depi`, `sgai`, `lvgi`, `tata`) are absent from the archived envelope or were explicitly reported as unavailable. Because the underlying ratios were not stored, the corrected score cannot be deterministically recomputed or replayed from artifact records.

---

## 2. Summary of Findings

Across 15 directories inspected in `runs/`:

| Category | Count | Description |
|---|---|---|
| **`changed`** | 0 | No legacy run persisted recoverable 8-index inputs to enable re-derivation. |
| **`unchanged`** | 0 | No legacy run persisted recoverable 8-index inputs. |
| **`not_using_helper`** | 6 | Standalone evidence packs, discovery traces, and diagnostic scripts. |
| **`inputs_unavailable`** | 9 | Envelopes with bare scores (no witness / no indices) or explicit unavailable flags. |
| **Total Directories** | **15** | |

---

## 3. Detailed Per-Run Audit

### Category: `inputs_unavailable` (9 runs)

Prior to version 2.0.0, the mock generator and model normalization layer did not persist the 8 individual ratio inputs or emit `CalculationWitness` records. Instead:
- Legacy mock runs emitted hardcoded scalar `-2.82` (with optional delta).
- Legacy model runs either injected a default `{"feasible": true, "score": -2.8, "classification": "Unlikely Manipulator"}` when the model omitted indices, or passed through unverified bare scalars (e.g. `-1.93`, `-1.96`) without input ratios.
- Real-model runs with data gaps explicitly reported the score as unavailable.

Because the 8 ratio inputs were never saved in the archived JSON envelopes, these legacy runs cannot have their corrected scores replayed from stored inputs. They are classified as `inputs_unavailable`:

1. **`runs/eval_agy_live`** (NVDA)
   - `BeneishM`: `{'feasible': True, 'score': -2.8, 'classification': 'Unlikely Manipulator'}`
   - Status: Legacy run with injected normalizer default `-2.8`; no calculation witness or input indices stored.
2. **`runs/eval_mock`** (MSFT)
   - `BeneishM`: `{'feasible': True, 'score': -2.82, 'classification': 'Unlikely Manipulator'}`
   - Status: Legacy mock run with hardcoded `-2.82`; no calculation witness or input indices stored.
3. **`runs/gemini_strict_recheck_20261006_212200`** (AAPL)
   - `BeneishM`: `{'score': None, 'classification': 'Undetermined - Missing Complete FY24 Cash Flow Statement', 'feasible': False}`
   - Status: Explicit unavailable result recorded by agent due to missing filing statement.
4. **`runs/gemini_verified_aapl_20250131_20261006`** (AAPL)
   - `BeneishM`: `{'score': None, 'classification': 'Undetermined - Missing Complete FY24 Cash Flow Statement', 'feasible': False}`
   - Status: Explicit unavailable result recorded by agent due to missing filing statement.
5. **`runs/nvda_opencode_live`** (NVDA)
   - `BeneishM`: `{'score': -1.93, 'classification': 'Grey Zone'}`
   - Status: Model-reported bare scalar without input ratios or calculation witness.
6. **`runs/run_test_20260915_203535`** (TEST)
   - `BeneishM`: `{'feasible': True, 'score': -2.82, 'classification': 'Unlikely Manipulator'}`
   - Status: Legacy mock run with hardcoded `-2.82`; no calculation witness or input indices stored.
7. **`runs/run_tsla_20260915_204109`** (TSLA)
   - `BeneishM`: `{'feasible': True, 'score': -2.82, 'classification': 'Unlikely Manipulator'}`
   - Status: Legacy mock run with hardcoded `-2.82`; no calculation witness or input indices stored.
8. **`runs/runtime_check`** (AAPL)
   - `BeneishM`: `{'feasible': True, 'score': -2.82, 'classification': 'Unlikely Manipulator'}`
   - Status: Legacy mock run with hardcoded `-2.82`; no calculation witness or input indices stored.
9. **`runs/uber_full_01`** (UBER)
   - `BeneishM`: `{'score': -1.96, 'classification': 'Unlikely Manipulator', 'feasible': True}`
   - Status: Model-reported bare scalar without input ratios or calculation witness.

---

### Category: `not_using_helper` (6 directories)

These directories contain non-pipeline artifacts, evidence downloads, or intake traces that do not produce an A3 envelope:

1. **`runs/apple_pdf_pack_20250131_1`**: Cached evidence pack only.
2. **`runs/evidence_aapl_20250131_20261006`**: Cached evidence pack only.
3. **`runs/gemini_live_check_20261006_195444`**: Diagnostic intake check without A3 envelope.
4. **`runs/gemini_strict_recheck_20261006_202229`**: Pre-analysis evidence pack cache.
5. **`runs/gemini_web_smoke_20261006_215653`**: Web intake smoke test directory.
6. **`runs/public_aapl_20250131_auto_1`**: SEC public data collection cache.

---

## 4. Replay and Remediation Protocol

1. **No Global Extrapolation**: Because historical runs did not persist the 8 input ratios, this report does not fabricate hypothetical or simulated impact counts.
2. **Historical Immutability**: Legacy run directories remain unmodified historical evidence.
3. **Future Runs**: All pipeline runs under the current codebase use `beneish_m_score` v2.0.0. When indices are provided, they are deterministically computed and bound to an immutable `CalculationWitness`; when indices are missing, the score is cleanly marked unavailable.
