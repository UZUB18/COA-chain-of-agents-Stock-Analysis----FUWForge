# Verification report — making financial evidence and arithmetic genuinely verifiable

Branch: `p0-baseline-preserve-verification`
Base commit: `28c17b4` (baseline checkpoint taken before any implementation change)
Report date: 2026-10-08

**Historical report:** see the [2026-10-10 readiness update](<readiness-upgrades.md>)
for subsequent hardening, installed-distribution validation, and current acceptance limits.

---

## 1. Headline result

> **A fabricated financial input can no longer pass merely because it cites a real fact.**

Demonstrated directly, on a real run whose entire integrity chain was repaired:

```
WITNESS A (fact value = 100, input used = 100)
  -> calculation_replay = passed    [BOUND]

WITNESS B (fact value = 100, input used = 150)
  -> calculation_replay = failed    [REJECTED]
  -> A3 BeneishM witness input 'dsri' claimed value 150.0 does not equal the
     referenced fact's value 100.0 (absolute difference 50.0, relative 0.5);
     policy=exact-float-equality-v1 (no tolerance) (fact 'fact_demo', ...)
```

Before this work, Witness B passed every existing check: a real fact ID, a
matching unit, a period already known, a correctly dated filing. It was caught
only because the *value* is now compared to the fact's actual value.

---

## 2. Test results, as actually run

All commands from the repository root, offline, with the mock provider.

### Baseline reproduced before any implementation change

```
$ git checkout 28c17b4
$ .venv/bin/python -m pytest -q --tb=short
410 passed, 3 deselected in 6.75s
```

This matches the reported 410-test baseline exactly.

### Final state

```
$ .venv/bin/python -m pytest -q --tb=short
512 passed, 3 deselected in 14.84s
```

Run three consecutive times to confirm determinism:

```
512 passed, 3 deselected in 38.42s
512 passed, 3 deselected in 30.86s
512 passed, 3 deselected in 18.69s
```

| Metric | Baseline | Final | Change |
|---|---|---|---|
| Tests passing | 410 | 512 | **+102** |
| Pre-existing tests broken | — | **0** | — |
| Flaky tests | — | **0** | verified 10/10 on the one that was |

### New tests added

97 tests in five new files, plus 4 added to `tests/test_independent_bear.py`:

| File | Tests | Covers |
|---|---|---|
| `tests/test_fact_binding.py` | 37 | Numeric policy, units, periods, basis, filing identity, PIT, derivations, mandatory A/B pair, audit-level integration |
| `tests/test_adversarial_audit.py` | 19 | Adversarial fixtures at every audit level |
| `tests/test_sec_facts_intake.py` | 14 | Point-in-time availability, disclosure classification, provenance |
| `tests/test_cli_smoke_end_to_end.py` | 14 | End-to-end CLI workflow, offline |
| `tests/test_evidence_attachments.py` | 13 | Typed attachment loading and SEC routing |
| `tests/test_independent_bear.py` | +4 | Experimental-feature guarantees (was 4, now 8) |

No historical archive was reset, cleaned, rewritten, or modified. The baseline
commit is intact and re-verifiable at any time.

---

## 3. Vacuity proofs — the tests are not decorative

A new test that passes before *and* after the fix proves nothing. Each fix was
therefore disabled at its real enforcement point and the suite re-run.

| Proof | Fix disabled | Result |
|---|---|---|
| A | `evidence.py`: attachments silently treated as absent | **10 of 13 failed** |
| B | `fact_binding.py`: numeric equality removed | **5 failed** (incl. `test_fabricated_input_with_a_valid_citation_fails`) |
| C | `fact_binding.py`: unit/ratio enforcement block disabled | **6 failed** |

Each proof was repeated only after confirming the patch actually modified the
source — an initial attempt used a `replace()` target that did not exist, was a
no-op, and "passed" vacuously. The zero-patch result was treated as a failed
proof and redone against a verified target. **This is worth stating plainly:
the first two vacuity attempts proved nothing until the patch was verified to
apply.**

After every proof the source was restored and `git diff` confirmed empty, then
the full suite re-run green.

---

## 4. What changed, by requirement

### P0 — preserve existing work

Working tree preserved on a dedicated branch. Baseline checkpoint committed
before any implementation change and re-verified at the end (§2). No resets,
no clean, no rewriting of historical archives, no modification of accepted
historical research results.

### P1 — no silent failures in typed evidence loading

The `except Exception: pass` in `src/evidence.py` is gone. Attachment handling
now distinguishes five outcomes that were previously collapsed into "absent":

| Situation | Behaviour |
|---|---|
| Optional attachment absent | Explicitly unavailable — not an error, not silent |
| Valid typed fact ledger | Loads |
| Malformed / incompatible ledger | **Raises** a meaningful validation error naming the fact and cause |
| Corrupted attachment | **Raises** — never silently treated as missing |
| Raw SEC `CompanyFacts` JSON | Routed to the SEC parser by declared kind, never to the typed-ledger loader |

A payload declared `sec_companyfacts` that is not a CompanyFacts document is
rejected rather than mis-parsed. An unreadable ledger is reported as unreadable —
an unreadable ledger must never be treated as an absent one.

The prohibited pattern was avoided: nothing here catches a broad exception and
appends a warning that later code ignores. Each failure either raises or is
explicitly classified as unavailable.

### P2 — calculation inputs bound to source facts

New module `src/fact_binding.py` (947 lines) is the single shared policy, used
by both the runtime validator and the independent auditor so they cannot drift.

For every sourced input, nine properties are verified:

1. the referenced fact exists
2. it belongs to the run's company
3. **the supplied value equals the fact's recorded value**
4. units share a compatible dimension, scale and currency
5. period and period type match
6. accounting basis is compatible
7. filing identity and source reference are well-formed
8. the information was available by the cutoff
9. a derived ratio has an inspectable derivation over existing facts

**Numeric comparison policy — `exact-float-equality-v1`.** Exact float equality
by default. No blanket tolerance.

*Why not a tolerance:* any tolerance wide enough to excuse a rounding difference
on a `383,285,000,000` currency figure also excuses inventing a number off by
50% — which is precisely Witness B. Values that survive a JSON round trip are
bit-identical, so exactness is meaningful even at that magnitude. An opt-in
scaled comparison exists, is never used on the binding path, and **reports the
bound it applied** rather than hiding it inside the verdict.

Schema extension: derived inputs may declare `expression`, `operand_fact_ids`
and `result_value`. An unsourced input is reported as `unavailable` rather than
failed, so a legitimate facts-free run is not made to fail and operators are not
pushed toward inventing citations.

### P3 — ratio-unit validation bypass closed

The bypass was structural:

```python
expected_unit != "ratio" and fact_unit != expected_unit
```

Whenever the expected unit **was** `"ratio"`, the condition short-circuited and
validation was skipped entirely — a ratio input could cite a dollar-denominated
or share-count fact silently.

There is no short-circuit now. Units are compared on **dimension** (`ratio`,
`currency`, `count`, `per_share`, `unknown`), then on scale and currency:

- ratio vs currency → rejected, by name
- ratio vs count → rejected, by name
- compatible normalised units (synonyms, `USD`/`USD`) → accepted
- scale mismatch (`USD` vs `USD thousand`) → rejected with the factor named
- currency mismatch (`USD` vs `EUR`) → rejected
- **incompatible dimensions are rejected, never implicitly converted**

The one legitimate escape is the derived-input path, where the cited fact is an
operand rather than the value source.

### P4 — SEC CompanyFacts integration, point-in-time honest

Every accepted fact retains full provenance: filing, accession, form, observation
period, availability time, reported value, unit, extraction method, and
disclosure relation.

**Availability policy — `sec-filed-date-end-of-day-v1`.** The SEC `filed` field
carries calendar-day precision with no intraday publication time. Availability is
therefore set to the **end** of the filed day (`23:59:59Z`), never the start.

The error is deliberately one-directional. This policy may withhold a fact that
was in fact public earlier in the day; it can never admit one that was not yet
public. Assuming a midnight timestamp would let a run with a noon cutoff accept
a filing that appeared that evening — a look-ahead bias, the exact failure a
point-in-time system exists to prevent.

**Disclosure relation** distinguishes four cases that were previously conflated:

| Case | Treatment |
|---|---|
| Initial disclosure | Reported; supersedes nothing |
| Same value re-disclosed | **Reported; supersedes nothing** |
| Changed reported value | Restatement; supersedes prior |
| Amended form (`/A`) | Restatement — the *act* is the signal, even if the value is unchanged |

A later disclosure of the same period is **not** labelled a genuine restatement.
Filing variants are handled by declared attachment kind, and unavailable external
data is recorded explicitly ("Typed financial facts are unavailable for this
run; they must not be guessed or substituted") rather than silently missing.

All tests use deterministic local fixtures. **Network access is not required for
the offline suite.**

### P5 — independent audit strengthened, level separation preserved

Adversarial fixtures now exist for every level:

| Adversary | Caught at |
|---|---|
| Fabricated numeric input with a valid citation | `calculation_replay` |
| Wrong period / wrong period type | `calculation_replay` |
| Wrong unit (ratio backed by currency) | `calculation_replay` |
| Future-dated fact | `calculation_replay` |
| Swapped source identity / placeholder accession | `calculation_replay` |
| Altered calculation witness | `calculation_replay` / `envelope_integrity` |
| Corrupted fact ledger | level 4 (reported, not swallowed) |
| Edited final report / tampered envelope bytes | `final_artifacts` / `envelope_integrity` |
| Duplicate role | `graph_and_contracts` |
| Incomplete provenance | `completeness` |

Two audit-code corrections were found by these fixtures:

- **Duplicate role IDs were reported as an unattributed top-level error.** They
  are now attributed to `graph_and_contracts` — the level that can detect them.
- **`replay_beneish` ignored a witness's declared `input_periods`/`input_bases`**
  while `validate_witness` honoured them. Period and basis are now checked on the
  audit path too.

The central fixture repairs the **entire** tamper chain — re-seals A3, rebinds the
run manifest, and runs a fixpoint cascade of parent checksums through A6, A6B,
A6R and A7 — so byte integrity, the dependency graph and final-artifact binding
all pass. Only the source-binding check remains, and it catches the fabrication.
This is the strongest form of the claim.

**Stripped legacy lineage is preserved as unavailable, not contradicted.** An
envelope whose sealed `upstream` is empty while execution recorded real parents
produces a *warning*, never a fabricated contradiction. The converse — a manifest
recording no parents while envelopes record real ones — is a genuine
contradiction and remains a hard error.

No audit contract was weakened to make the suite green.

### P6 — end-to-end offline smoke tests

14 tests driving `python -m src.controller` as a subprocess, mock provider, no
network, no credentials:

- normal execution → complete verified run
- `verify-run` and `verify-run --strict`
- disclosure of unsourced inputs in `verify-run` output
- interrupted run + `--resume`
- reused-envelope checksum stability across repeated resumes
- evidence snapshot reuse, and **refusal** to resume with changed evidence
- `bundle-evidence` → `verify-evidence` → `restore-evidence` round trip, plus
  failure against a wrong trusted digest
- `seal` refuses a modified envelope; `seal --force` states truthfully that the
  run "now certifies the edited bytes, not the original execution"
- seal idempotence
- research-packet export, shape and content determinism

The recovery policy is asserted **as implemented**: an uncertain provider call
stops for an explicit operator choice, and a retry may re-bill. **No
exactly-once billing is claimed anywhere**, because the API cannot guarantee it.

A forced seal was checked specifically for truthfulness. It restores the
*consistency* of levels 1/3/5; it cannot restore a fact that was never there.

### P7 — experimental features remain experimental

No behaviour change was needed; the guarantees were already implemented. They are
now pinned by 4 new regression tests so they cannot silently regress:

- `--independent-bear` stays **opt-in**, default off, capped at **one** extra call
- the blind pass's upstream **excludes A6B**, so it never sees the bull case
- the blind phase has a **distinct content checksum** from the reconciled pass,
  so a receipt can never be reused across the two
- `promotion_status` remains `experimental_pending_cost_and_review_evaluation`
- authoritative task instructions stay **outside** `<untrusted_source_data>`
  wrappers; retrieved content stays inside them
- Beneish `CALCULATOR_VERSION` `2.0.0`, `TATA` `+4.679`, `LVGI` `-0.327`,
  intercept `-4.84` and threshold `-1.78` are frozen
- the threshold remains a **screening signal**; the module is asserted to contain
  no fraud-proof language

No claim of improved investment performance was added anywhere.

---

## 5. Which audit levels now establish genuine source-backed arithmetic

| Level | Establishes source-backed arithmetic? | Precise guarantee |
|---|---|---|
| `envelope_integrity` | **No** | Bytes unchanged since sealing. Says nothing about what was sealed. |
| `source_reconstruction` | **No** | Quoted text traces to hashed evidence. Says nothing about whether it supports the conclusion. |
| `graph_and_contracts` | **No** | The dependency graph and agent contracts are internally consistent. |
| `calculation_replay` | **Yes — for sourced inputs** | Arithmetic replays, and every *sourced* input is bound to a real fact's actual value with matching company, unit, period, basis, filing identity and point-in-time availability. |
| `final_artifacts` | **No** | Published bytes are the audited bytes. |
| `completeness` | **No** | Nothing is silently missing. |

**Exactly one level establishes source-backed arithmetic**, and only for inputs
that actually cite a fact. This is the honest boundary.

### Levels that remain incomplete

- **`calculation_replay` is only as complete as the ledger.** A run with no fact
  ledger passes with every input disclosed as `unavailable`. That is correct
  behaviour, but it means a passing level is not evidence that inputs were
  sourced — the `unavailable` list is. The mock-provider runs in this repo are
  exactly this case, which is why the CLI smoke test asserts the disclosure is
  printed.
- **`source_reconstruction` is `not_applicable`** for runs without an evidence
  pack. Absence is reported as absence, never as a pass.
- **Derived ratios are declared, not computed.** A derivation is checked for
  structural completeness and for operands that exist; the expression string is
  not interpreted. Re-deriving the value from the expression would require a
  small expression evaluator, which is deliberately out of scope.
- **A tampered-then-fully-resealed run** defeats levels 1 and 5 completely. Only
  levels 3 and 4 survive a complete re-seal.

### Limitations that no amount of code checking can solve

1. **Whether the issuer's disclosures are accurate.** The chain proves a number
   was reported by the company and used faithfully. It cannot prove the company
   reported it truthfully. No checksum, schema or replay can reach that.
2. **Whether the input is the economically appropriate one.** Binding a real
   trailing-revenue figure to a DSRI input is *semantically satisfied*. Whether
   DSRI is the right index, or trailing revenue the right base, is an analyst's
   judgement. A system that "verifies" this would only be encoding one analyst's
   assumptions as truth.
3. **Whether the formula is economically sound.** Replay proves the formula was
   applied to the stated inputs — not that those inputs are the ones a competent
   analyst would choose.
4. **Whether the conclusion follows from the evidence.** Every level here is
   about provenance and arithmetic. None is about whether a stock is a good
   investment.
5. **Whether a Beneish score above −1.78 indicates fraud.** The threshold is a
   screening signal derived from published coefficients. It is **not** proof of
   misstatement, and the codebase must never describe it as such.
6. **Intraday point-in-time accuracy.** The SEC `filed` date has calendar-day
   precision only. The end-of-day policy is a conservative bound, not a
   publication timestamp.
7. **Source authenticity.** Evidence is integrity-checked, **not signed**. A
   SHA-256 digest proves bytes have not changed since collection; it does not
   prove they came from the issuer or say what they appear to say.

---

## 6. Honest reading of a fully passing run

A run reporting all six levels passed is **internally consistent and
provenance-checked**. That is a real and useful property. It is not a
certification of the analysis.

When the `unavailable` list is non-empty, add one more question: **which
conclusions rest on numbers no filing supports?**

---

## 7. Documentation added

| Document | Contents |
|---|---|
| `docs/audit-levels.md` | What each level proves and does not; why unsourced inputs are disclosed rather than failed; what no level can establish |
| `docs/fact-binding-policy.md` | The nine binding properties, exact-by-default numeric policy and its rationale, unit dimensions, period/basis rules, derived inputs |
| `docs/sec-availability-policy.md` | Why a filing date is not a timestamp, the end-of-day policy and its one-directional error, disclosure classification |
| `README.md` | Updated audit-level table; "a citation is not a verification" now stated explicitly, linked to the above |

---

## 8. Commits

```
37ad8f4 P8 docs: audit contract documentation; fix flaky resume assertion
1dedc6f P7: lock experimental-feature guarantees with regression tests
7e98edf P6: end-to-end offline CLI smoke tests
db771cc P5: adversarial fixtures at every audit level
4fb4516 P4: SEC CompanyFacts integration, conservative PIT availability, disclosure classification
9f4e098 P1: typed evidence attachments fail loudly; SEC routing explicit
549a9d2 P2+P3 core: fact-binding verification with explicit numeric policy
28c17b4 P0 baseline checkpoint: preserve research-compiler upgrade work before correctness fixes
```

Change footprint: 23 files, +4916 / −146.

---

## 9. Reproducing this report

```bash
git checkout p0-baseline-preserve-verification
git checkout 28c17b4 && .venv/bin/python -m pytest -q --tb=short   # 410 passed
git checkout - && .venv/bin/python -m pytest -q --tb=short         # 512 passed

# The success criterion:
.venv/bin/python -m pytest -q tests/test_fact_binding.py -k "witness_b or fabricated"
.venv/bin/python -m pytest -q tests/test_adversarial_audit.py
```

No credentials are required and no network access is used.