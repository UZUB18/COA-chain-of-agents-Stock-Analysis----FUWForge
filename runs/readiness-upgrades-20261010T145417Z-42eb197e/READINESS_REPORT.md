# FUWForge Pipeline Readiness and Verification Report

**Scratch Directory**: `runs/readiness-upgrades-20261010T145417Z-42eb197e`  
**Fixed Analysis Date**: `2025-06-01`  
**Python Runtime**: `.venv/bin/python` (Python 3.12.13)  
**Execution Timestamp (UTC)**: `2026-10-10T15:01:45Z`

---

## 1. Scratch Path Resolution & Nonexistence Verification

Prior to performing any operations, the scratch path was constructed and verified:
- Construction: `runs/readiness-upgrades-20261010T145417Z-42eb197e`
- Nonexistence verified: `scratch_dir.exists() == False`
- Resolved path: `/home/trbuzu/Documents/COA-chain-of-agents-Stock-Analysis----FUWForge/runs/readiness-upgrades-20261010T145417Z-42eb197e`
- Constraint compliance: No production source, documentation, test files, or pre-existing/archived runs were modified.

---

## 2. CLI Mock Runs & Non-Vacuous Source Verification

### A. Fresh Mock Runs Executed (Quick, Standard, Deep)

1. **Quick Depth**:
   - **Command**: `.venv/bin/python -m src.controller run --provider mock --ticker MOCKQ --company "Mock Quick Inc" --analysis-date 2025-06-01 --depth Quick runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-quick`
   - **Exit Status**: `0`
   - **Output Path**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-quick`
   - **Audit (`verify-run --strict`)**: Passed (`Overall state: verified`, exit code `0`).
   - **Source Gate (`verify-run --require-source-backed`)**: FAILED as expected (exit code `1`), proving that missing sources are not vacuously passed.
   - **Seal (`controller seal`)**: Passed (exit code `0`), sealed 9 envelopes and rebuilt `run_manifest.json`.
   - **Export Packet (`controller export-packet`)**: Passed (exit code `0`), external SHA-256: `sha256:605cadda82e51f02bad7e9d1553a22144ab970e9096ec4ea7b80c0292cbe1f4c`.

2. **Standard Depth**:
   - **Command**: `.venv/bin/python -m src.controller run --provider mock --ticker MOCKS --company "Mock Standard Inc" --analysis-date 2025-06-01 --depth Standard runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-standard`
   - **Exit Status**: `0`
   - **Output Path**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-standard`
   - **Audit (`verify-run --strict`)**: Passed (`Overall state: verified`, exit code `0`).
   - **Source Gate (`verify-run --require-source-backed`)**: FAILED as expected (exit code `1`).
   - **Seal (`controller seal`)**: Passed (exit code `0`), sealed 9 envelopes.
   - **Export Packet (`controller export-packet`)**: Passed (exit code `0`), external SHA-256: `sha256:2561cff605d8bcbc94d8d6ed2033a0149a06d65cb4e42616df53305f0b87876f`.

3. **Deep Depth**:
   - **Command**: `.venv/bin/python -m src.controller run --provider mock --ticker MOCKD --company "Mock Deep Inc" --analysis-date 2025-06-01 --depth Deep runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-deep`
   - **Exit Status**: `0`
   - **Output Path**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-deep`
   - **Audit (`verify-run --strict`)**: Passed (`Overall state: verified`, exit code `0`).
   - **Source Gate (`verify-run --require-source-backed`)**: FAILED as expected (exit code `1`).
   - **Seal (`controller seal`)**: Passed (exit code `0`), sealed 9 envelopes.
   - **Export Packet (`controller export-packet`)**: Passed (exit code `0`), external SHA-256: `sha256:5b36047e746717659fcf48ab36bfa1ebe1576ee5c634cd1d202f33fcbb57b8f9`.

### B. Proving `--require-source-backed` Fails for Unavailable Sources

Running `verify-run --require-source-backed` on unsourced mock runs produced the following failure diagnostics:
```
Overall state: failed
Independent levels:
  - envelope_integrity       passed
  - source_reconstruction    not_applicable
  - graph_and_contracts      passed
  - calculation_replay       failed
  - final_artifacts          passed
  - completeness             passed

Findings:
  - require-source-backed: no verified financial inputs found (expected >= 1)
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'dsri' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'gmi' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'aqi' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'sgi' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'depi' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'sgai' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'lvgi' (1.0) cites no source fact; its value is unverified against any typed fact
  - require-source-backed: calculation input is not source-backed: A3 BeneishM witness input 'tata' (0.0) cites no source fact; its value is unverified against any typed fact
```
This confirms that the source-backed gate is actively validating source citations and rejects unsourced mock calculations with a non-zero exit code (`1`).

---

## 3. Opt-in Feature Exercises: `--independent-bear` and `--resume`

### A. Independent Bear Pass (`--independent-bear`)
- **Command**: `.venv/bin/python -m src.controller run --provider mock --ticker MOCKIB --company "Mock IndBear Inc" --analysis-date 2025-06-01 --depth Standard --independent-bear runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-indbear`
- **Exit Status**: `0`
- **Output Path**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-indbear`
- **Behavior Observed**: Orchestrator executed two passes of agent `A6R` (blind bear pass + reconciliation pass):
  ```
  [ORCH] Starting agent A6R (Bear-Case)...
  [ORCH] Agent A6R accepted (3ms, attempt 1).
  [ORCH] Starting agent A6R (Bear-Case)...
  [ORCH] Agent A6R accepted (7ms, attempt 1).
  ```
- **Audit & Export**: `verify-run --strict` passed (exit code `0`), `seal` succeeded (exit code `0`), `export-packet` succeeded (exit code `0`, SHA-256: `sha256:3a0d0d54a49975667de5c197703a65e0d0c40f5aa446414855a509808acf681e`).

### B. Resume Reusing Validated Receipts (`--resume`)
- **Initial Run**: `.venv/bin/python -m src.controller run --provider mock --ticker MOCKRES --company "Mock Resume Inc" --analysis-date 2025-06-01 --depth Quick runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-resume`
- **Resume Run**: `.venv/bin/python -m src.controller run --provider mock --ticker MOCKRES --company "Mock Resume Inc" --analysis-date 2025-06-01 --depth Quick --resume runs/readiness-upgrades-20261010T145417Z-42eb197e/mock-resume`
- **Exit Status**: `0`
- **Receipt Reuse Verification**:
  ```
  [ORCH] Agent A1 accepted (0ms, attempt 1).
  [ORCH] Agent A2 accepted (0ms, attempt 1).
  [ORCH] Agent A3 accepted (0ms, attempt 1).
  [ORCH] Agent A4 accepted (0ms, attempt 1).
  [ORCH] Agent A5 accepted (0ms, attempt 1).
  [ORCH] Agent A6B accepted (0ms, attempt 1).
  [ORCH] Agent A6R accepted (0ms, attempt 1).
  [ORCH] Agent A6 accepted (0ms, attempt 1).
  [ORCH] Agent A7 accepted (0ms, attempt 1).
  ```
  All 9 receipts were reused from `execution_ledger.db` with `0ms` latency and identical checksum `sha256:f1d3ba1cfa24d75e568ecd77996579c53ff5adc1a8128c3ce6e7b9f026a4b041`.
- **Audit & Export**: `verify-run --strict` passed (exit code `0`), `seal` succeeded (exit code `0`), `export-packet` succeeded (exit code `0`, SHA-256: `sha256:b14c0d18ec16b52353bc1532c0a03984a6d9289f04143ec5059491878c7a2292`).

---

## 4. Synthetic Evidence-Backed Fixture & Full Orchestration

### A. Fixture Construction
- **Source Directory**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-source-evidence`
- **Evidence Documents**: SEC Form 10-K filing document with hash verification.
- **Eight Beneish Input Facts**:
  1. `fact_synth_dsri`: `1.0` (`ratio`, period `2024-01-01` to `2024-12-31`, accession `0000320193-25-000001`, basis `US-GAAP`)
  2. `fact_synth_gmi`: `1.0`
  3. `fact_synth_aqi`: `1.0`
  4. `fact_synth_sgi`: `1.0`
  5. `fact_synth_depi`: `1.0`
  6. `fact_synth_sgai`: `1.0`
  7. `fact_synth_lvgi`: `1.0`
  8. `fact_synth_tata`: `0.0`
  All facts availability time: `2025-01-15T00:00:00Z` (before analysis cutoff `2025-06-01`).

### B. End-to-End Orchestrator Execution
- **Run Directory**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-run`
- **Execution Mechanism**:
  - `FUWForgeOrchestrator` instantiated with `strict=True` and `evidence_pack=source_ev_json`.
  - EvidencePack snapshot created under `synthetic-run/evidence/`.
  - Offline `SyntheticBoundA3` agent registered on `orchestrator.registry`.
  - When A3 executed, `context.fact_ledger` was verified to contain all 8 facts.
  - Agent constructed `beneish_witness` with `fact_ids_map`, `indices`, and `result` computed via `beneish_score`.
  - Orchestrator executed runtime contract validation via `validate_contract(..., fact_ledger=context.fact_ledger, strict=True)`.
  - All 9 agents completed successfully (`status: completed`).

### C. Strict Source-Backed Audit & Packet Export
- **Audit Command**: `.venv/bin/python -m src.controller verify-run --strict --require-source-backed runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-run`
  - **Exit Status**: `0`
  - **Overall State**: `verified`
  - **Verification Levels**:
    - `envelope_integrity`: passed
    - `source_reconstruction`: source_bytes_verified
    - `graph_and_contracts`: passed
    - `calculation_replay`: passed
    - `final_artifacts`: passed
    - `completeness`: passed
  - **Counters**:
    - `witnesses_attempted`: 1
    - `witnesses_replayed`: 1
    - `inputs_verified`: 8
    - `inputs_unverified`: 0
    - Disclosed gaps / unavailable inputs: `0`
- **Seal Command**: `.venv/bin/python -m src.controller seal runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-run` (exit code `0`)
- **Export Packet**: `.venv/bin/python -m src.controller export-packet --strict --output runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-run/packet.json runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-run`
  - **Exit Status**: `0`
  - **External SHA-256**: `sha256:1d8a72e6aea6b9804ce7aa4843af668234c03bc368121f36ea9932ed17b1da06`
  - **Packet Facts Verified**: Exactly 8 bounded facts from the evidence snapshot were included in `packet.json`.

---

## 5. Adversarial Copy, Integrity Repair, and Multi-Layered Rejection

### A. Adversarial Fixture Creation
- **Directory**: `runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-adversarial` (fresh copy of `synthetic-run`; never modifying archives).
- **Tampering**:
  - In `a3_envelope.json`, modified `witness["inputs"]["dsri"] = 999.0` while maintaining citation to `fact_synth_dsri` (which has recorded value `1.0`).
  - Recomputed witness arithmetic result to match `999.0`, isolating the semantic source-binding failure from raw arithmetic discrepancy.

### B. Runtime Contract Rejection
1. **Direct `validate_contract` Rejection**:
   - Running `validate_contract(a3_env, fact_ledger=fact_ledger, strict=True)` returned `valid == False` with:
     ```
     - BeneishM.calculation_witness: witness input 'dsri' claimed value 999.0 does not equal the referenced fact's value 1.0 (absolute difference 998.0, relative 998.0); policy=exact-float-equality-v1 (no tolerance) (fact 'fact_synth_dsri', policy=exact-float-equality-v1)
     ```
2. **`ModelAgent` Runtime Rejection**:
   - `ModelAgent("A3", ...)` was run with `dummy_ctx` containing `fact_ledger`.
   - The agent caught the contract violation and raised `RuntimeError` on attempt 1 without retries:
     ```
     RuntimeError: Agent A3 failed after 1 attempts: Contract violations: BeneishM.calculation_witness: witness input 'dsri' claimed value 999.0 does not equal the referenced fact's value 1.0 (absolute difference 998.0, relative 998.0); policy=exact-float-equality-v1 (no tolerance) (fact 'fact_synth_dsri', policy=exact-float-equality-v1)
     ```

### C. Integrity Repair via CLI (`seal --force`)
- **Command**: `.venv/bin/python -m src.controller seal --force runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-adversarial`
- **Exit Status**: `0`
- **Output**:
  ```
  Sealed 9 envelope(s) in .../synthetic-adversarial. Wrote 9 envelope file(s). Recomputed parent digests for 4 descendant envelope(s) because a parent's bytes changed; this run now certifies the edited bytes, not the original execution. Rebuilt run_manifest.json.
  ```
- **Integrity Status**: Byte integrity and graph hashes are now 100% consistent. `envelope_integrity` passes.

### D. Semantic Audit Rejection of Repaired Adversarial Run
- **Audit Command**: `.venv/bin/python -m src.controller verify-run --strict runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-adversarial`
- **Exit Status**: `1`
- **Audit Results**:
  ```
  Overall state: failed

  Independent levels:
    - envelope_integrity       passed
    - source_reconstruction    source_bytes_verified
    - graph_and_contracts      failed
    - calculation_replay       failed
    - final_artifacts          passed
    - completeness             passed

  Findings:
    - A3 BeneishM witness input 'dsri' claimed value 999.0 does not equal the referenced fact's value 1.0 (absolute difference 998.0, relative 998.0); policy=exact-float-equality-v1 (no tolerance) (fact 'fact_synth_dsri', policy=exact-float-equality-v1)
  ```
- **Source-Backed Audit Command**: `.venv/bin/python -m src.controller verify-run --strict --require-source-backed runs/readiness-upgrades-20261010T145417Z-42eb197e/synthetic-adversarial`
  - **Exit Status**: `1`
  - Correctly failed at `calculation_replay` because the tampered input is not verified against the source fact.

---

## 6. Summary Table of Operations

| Operation / Run | Command / Step | Exit Status | Verification Status | Key Verification Output |
| :--- | :--- | :---: | :---: | :--- |
| **Mock Quick** | `run --depth Quick` | 0 | completed | run_summary.json written |
| | `verify-run --strict` | 0 | verified | 6 levels passed, 8 honest gaps disclosed |
| | `verify-run --require-source-backed` | 1 | failed | non-vacuous failure on unsourced inputs |
| | `seal` | 0 | sealed | 9 envelopes sealed |
| | `export-packet` | 0 | verified | packet.json exported |
| **Mock Standard** | `run --depth Standard` | 0 | completed | run_summary.json written |
| | `verify-run --strict` | 0 | verified | 6 levels passed |
| | `verify-run --require-source-backed` | 1 | failed | non-vacuous failure on unsourced inputs |
| | `seal` & `export-packet` | 0, 0 | verified | packet.json exported |
| **Mock Deep** | `run --depth Deep` | 0 | completed | run_summary.json written |
| | `verify-run --strict` | 0 | verified | 6 levels passed |
| | `verify-run --require-source-backed` | 1 | failed | non-vacuous failure on unsourced inputs |
| | `seal` & `export-packet` | 0, 0 | verified | packet.json exported |
| **Independent Bear** | `run --independent-bear` | 0 | completed | 2x A6R passes (blind + reconcile) |
| | `verify-run --strict` | 0 | verified | sealed and packet exported |
| **Resume Run** | `run --resume` | 0 | completed | all 9 receipts reused at 0ms latency |
| | `verify-run --strict` | 0 | verified | sealed and packet exported |
| **Synthetic Run** | `EvidencePack` + `Orchestrator` | 0 | completed | 8 Beneish facts bound via `fact_ledger` |
| | `verify-run --strict --require-source-backed` | 0 | verified | 8 inputs verified, 0 unavailable |
| | `export-packet --strict` | 0 | verified | packet contains all 8 snapshot facts |
| **Adversarial Copy** | `validate_contract` & `ModelAgent.run` | N/A | rejected | runtime rejection on mismatch (999.0 vs 1.0) |
| | `seal --force` | 0 | resealed | fixpoint seal repaired, 4 descendants updated |
| | `verify-run --strict` | 1 | failed | caught at calculation_replay level |
| | `verify-run --require-source-backed` | 1 | failed | caught at calculation_replay level |
