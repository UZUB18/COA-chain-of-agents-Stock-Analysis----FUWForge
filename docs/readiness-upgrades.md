# Readiness upgrades — 2026-10-10

## Result and scope

The confirmed offline provenance, audit, packet, and packaging gaps have been hardened. This is not a claim of perfect research or investment correctness. No live provider acceptance test or paid research call was authorized.

This report supplements the historical [verification report](<verification-report.md>). The [audit-level guide](<audit-levels.md>) describes the current acceptance policy.

## Changes

- **SEC availability:** unknown or malformed filing dates are excluded before supersession. Valid date-only observations use conservative UTC end-of-day availability. Evidence snapshots retain the research cutoff.
- **Derived inputs:** bounded AST arithmetic replays every operand. Binding checks cover values, company, currency, scale, periods, accounting basis, filing identity, and cutoff. The replay rejects unsupported syntax, unused operands, zero division, and nonfinite results.
- **Evidence prompts:** deterministic selection reserves current and prior core financial observations before filling the prompt budget. This improves coverage; it does not guarantee every requested ratio is available. The audit ledger remains untruncated.
- **Runtime contracts:** the orchestrator and ModelAgent receive the eligible fact ledger, ticker, and cutoff. Normalization preserves witness source metadata. Malformed model-reported results still fail non-strict validation.
- **Independent audit:** verified evidence snapshots reconstruct typed facts. Conflicting model facts cannot override them. Derived-input replay cannot substitute for replaying an unknown output formula. Nested witnesses receive independent checks across mappings and nested lists. Exceeding the scan depth limit produces an explicit error instead of silently skipping calculations. Direct witnesses cannot evade discovery by omitting one metadata field.
- **Source coverage:** `--require-source-backed` requires verified evidence bytes, actual A3 Beneish replay, all eight core inputs verified, and no disclosed calculation gaps. Unrelated identity calculations cannot replace the core forensics.
- **Packets:** construction and contextual validation use one canonical fact projection. Validation rejects invented, omitted, duplicated, and altered facts. Validation without a run directory remains structural-only.
- **Installed distributions:** resource files live only inside the package namespace. Wheel and sdist smoke tests run outside the checkout and resist generic config/schema shadowing. Financial-fact and execution-receipt schemas use the shared resource resolver. Installed default output goes to the working directory, not site-packages.

## Acceptance command

```bash
.venv/bin/python -m src.controller verify-run <run_dir> \
  --strict --require-source-backed --json
```

Use this gate for complete source-backed core-forensics acceptance. Ordinary `--strict` still permits honest missing-source disclosures and cannot replace this gate. Inspect both coverage and the independent levels.

## Offline validation

The final parent regression passed: **692 passed, 3 deselected in 26.03 seconds**, exit code `0`.

```bash
.venv/bin/python -m pytest -q --tb=short
```

The three deselected tests are live tests. The final parent recheck also passed the seven-run ordinary/source-gate matrix and contextual packet validation for all six non-adversarial runs. Adversarial regressions cover nested lists, scan-depth exhaustion, omitted direct-witness metadata, source-free packet forgery, and exact finite fact-value reconciliation. `git diff --check` passed; tracked archived runs had no diff.

Actual wheel and sdist builds, installations, CLI runs, strict audits, exports, and valid/invalid receipt checks passed locally. These environments reused host dependency packages through a `.pth` file. They isolated the FUWForge distribution and working directory, but did not independently install every dependency.

The [CI configuration](<../.github/workflows/ci.yml>) adds independently resolved wheel/sdist installs on Python 3.10 and 3.12. Remote CI was not run in this session.

Seven fresh offline runs exercised the following matrix. Each fixture used an explicit analysis date of `2025-06-01`. The [offline matrix report](<../runs/readiness-upgrades-20261010T145417Z-42eb197e/READINESS_REPORT.md>) records the commands and outcomes.

| Run | Ordinary strict audit | Source-backed gate | Purpose |
| --- | --- | --- | --- |
| Mock Quick, Standard, Deep | Pass | Expected fail | Depth modes; audit, sealing, export |
| Mock independent bear | Pass | Expected fail | Blind and reconciliation passes |
| Mock resume | Pass | Expected fail | Reuse all nine validated receipts |
| Synthetic eight-input evidence | Pass | Pass | Runtime ledger wiring, reconstruction, replay, packet facts |
| Re-sealed synthetic tampering | Fail | Fail | Changed DSRI remains rejected after checksum repair |

The source-backed positive case used **synthetic ratio facts**. It proves wiring and replay, not SEC-derived economics. The tampered copy changed DSRI to `999.0` while the cited fact remained `1.0`; runtime validation and independent semantic audit rejected it. Fresh runs used a new scratch directory. No tracked archived run was changed.

## Remaining acceptance work

1. **Real financial derivations:** exercise actual SEC statements across multiple issuers. Verify every Beneish component, fiscal period, and accounting choice against analyst expectations.
2. **Bounded live-provider smoke:** obtain an explicit provider/model and cost cap. Check model output quality, source intake, retries, and rate limits without treating mock results as live evidence.
3. **Independent dependencies and CI:** run the remote matrix and a fully independent dependency install before release.
4. **Research quality:** evaluate conclusions and economic appropriateness separately from provenance. Verified bytes do not prove issuer truth, extraction accuracy, or investment value.
5. **LoopX automation:** project state and manual CLI receipts do not prove unattended DSH scheduling. Verify the native driver separately before relying on autonomous operation.

The changes remain uncommitted. The offline implementation can be reviewed without spending on providers or changing historical artifacts.
