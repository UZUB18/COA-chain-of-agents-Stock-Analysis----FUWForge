# Source binding: how a calculation input is tied to a fact

This document specifies the numeric-comparison policy, the unit-dimension rules,
and the point-in-time availability policy used by FUWForge. It is the contract
that `src/fact_binding.py` implements and that both `src/calculation_witness.py`
and `src/run_semantic_audit.py` enforce.

The single rule everything else serves:

> **A citation is a claim that a number came from a fact. Verifying the
> citation's existence proves nothing. The number must equal the fact.**

---

## 1. Why this exists

A calculation witness previously recorded, for each input, the fact ID it came
from. The audit checked that the ID existed in the ledger, that units matched,
and that the fact was not dated in the future. It never compared the *number in
the witness* to the *number in the fact*.

The consequence: a witness could cite a real, correctly-dated, correctly-united
fact and supply any value it liked. The arithmetic replay then recomputed the
score faithfully **from the fabricated input** and reported success, because
internal consistency was the only property being tested.

A working demonstration is the mandatory test pair in
`tests/test_fact_binding.py`:

| Witness | Cited fact | Value used | Verdict |
|---|---|---|---|
| A | value `100` | input `100` | binds |
| B | value `100` | input `150` | **fails** |

Witness B passes every check that existed before this policy. It is caught now.

---

## 2. Numeric comparison policy

Policy identifier: `exact-float-equality-v1`.

### 2.1 Default: exact equality

Two floats are considered bound only when they are **exactly equal**.

This is not naive strictness. A value that has been serialized to JSON and
parsed back is bit-identical to the original, so any real difference is a real
difference. There is no representation noise to absorb in this path.

### 2.2 Why there is no blanket tolerance

The tempting shortcut is `abs(claimed - actual) < 1e-6`. It fails for two
distinct reasons:

- **It is arbitrary.** A tolerance picked for ratios is enormous for
  large-magnitude currency values (hundreds of billions) and negligible for
  small ones. The same constant cannot be right for both.
- **It must be wide enough to excuse rounding**, which is orders of magnitude
  wider than the discrepancies that matter. A tolerance large enough to explain
  a rounding difference of `0.01` on `383,285,000,000` would also excuse
  inventing a number that is off by 50%. Witness B (`100` vs `150`) is exactly
  that case.

Any tolerance large enough to excuse a fabricated input is a tolerance that
excuses fabrication. Only exactness keeps the check meaningful.

### 2.3 The opt-in scaled comparison

`compare_numbers()` accepts `rel_tol` and `abs_tol`. When either is supplied,
the bound is `rel_tol * max(|a|, |b|) + abs_tol`, and the effective bound is
**reported in the verdict** rather than hidden inside it:

```
claimed value 1.0 differs from the referenced fact's value 1.0000000000005 by
1e-13, exceeding the declared tolerance 1.1e-09 (rel_tol=1e-09, abs_tol=1e-12)
```

Three properties make this safe:

1. It is **never applied on the default binding path.** The binding checks call
   `compare_numbers()` with no tolerance arguments.
2. It must be **requested explicitly** by a caller that knows its domain.
3. It is **reported**, so a reader can see the tolerance that produced a verdict.

`src/fact_binding.py` currently makes no such request. The opt-in exists so a
future caller with a genuine rounding relationship can declare one without
reinventing the comparison.

### 2.3a Calculation replay and model-reported indices

These are not fact bindings, but they are the other numeric comparisons a run
reports:

- **Beneish replay is exact.** `calculation_witness.py` recomputes the score
  from the witness inputs and requires the recorded result to be the identical
  float. The runner produced it with the same function from the same
  JSON-round-tripped inputs, so no allowance is needed.
- **Model-reported Beneish indices** are compared with the runner's own index
  values with an absolute allowance of `5e-7` (coded as `5.000001e-7` so a value exactly
  5e-7 away is not lost to float rounding; `agents/model.py`). Models report
  indices to six decimal places; this is that rounding allowance, not a
  tolerance for different numbers. A larger disagreement rejects the response.

### 2.4 Rejected outright

Non-numeric values, booleans, `NaN`, and infinities cannot bind to anything. A
non-finite witness input is an unbounded number; it is refused before the
comparison is attempted.

---

## 3. Unit dimensions

Compatibility is decided on **dimension**, not string equality, because
legitimate synonyms exist (`ratio`/`pure`, `share`/`shares`).

| Dimension | Members |
|---|---|
| `ratio` | `ratio`, `pure`, `x`, `multiple`, `times`, `percent` |
| `currency` | `USD`, `EUR`, `GBP`, … (a recognised ISO code) |
| `count` | `shares`, `share`, `count`, `employees` |
| `per_share` | `USD/share`, `EUR/share` |
| `unknown` | anything else — never valid for a numeric binding |

An unrecognised label yields `unknown` rather than being optimistically treated
as compatible. Currency is a **closed set**: a label that is not a known ISO code
is not a currency.

### 3.1 Scale suffixes are a mismatch, not a conversion

`USD` and `USD thousand` share a dimension but differ by 1000x. They are
reported as a scale mismatch naming the factor. A percent is a ratio with scale
0.01, so `percent` against `ratio` (12.5 vs 0.125) is a scale mismatch too:

```
scale mismatch: witness unit 'USD' vs fact unit 'USD thousand' differ by a factor of 1000
```

In a derivation, dividing two dimensionless operands divides their scales: a
percent over a ratio stays percent-scaled, a percent over a percent is a plain
ratio. Named constants carry no unit, so multiplying or dividing by 100 is not
recognised as a percent/ratio conversion; cite a fact in the unit the witness
expects instead.

**Incompatible dimensions are rejected, never implicitly converted.** A silent
conversion is how a 1000x scale error becomes a plausible-looking number.

### 3.2 Currency must match exactly

For `currency` and `per_share`, both sides must name the same ISO code. A
missing code is *incompatible*, not "assumed compatible" — assuming one is how a
EUR figure gets used as USD.

### 3.3 The ratio bypass

The previous guard was:

```python
expected_unit != "ratio" and fact_unit != expected_unit
```

Whenever the expected unit **was** `"ratio"`, the condition short-circuited and
the check was skipped entirely. A ratio input could therefore cite a
dollar-denominated or share-count fact without comment.

There is no short-circuit now. A `ratio` expectation against a `currency` or
`count` fact is an explicit, named failure. The one legitimate escape is the
derived-input path (§6).

---

## 4. Period and period type

A witness may declare its expectation per input:

```json
"input_periods": {"dsri": {"instant": "2024-12-31"}}
"input_periods": {"revenue": {"period_start": "2024-01-01", "period_end": "2024-12-31"}}
```

Three distinct failures are reported, not one:

- **period mismatch** — same kind, different period;
- **period type mismatch** — an `instant` fact cannot satisfy a `duration`
  expectation (a balance-sheet date is not a year of flows);
- **no recorded period** — the fact has no economic period at all.

An input that declares *no* period is not "wrong"; it is **undeclared**, and the
period check is skipped. Undeclared is weaker than verified, and the audit
discloses it rather than silently passing it.

---

## 5. Accounting basis, filing identity, source reference

- **Basis** is normalised (`US_GAAP` → `US-GAAP`) and must match. Different
  bases are different numbers.
- **Filing identity** must be a well-formed SEC accession
  (`NNNNNNNN-NN-NNNNNN`) **and not the all-zero placeholder**
  (`0000000000-00-000000`), which is well-formed by shape but identifies no
  filing.
- **Source reference** must be a non-empty absolute `http(s)` reference. An
  empty or relative reference cannot support a citation.

---

## 6. Derived inputs

A ratio may legitimately be computed from other facts. When an input declares a
`derivations` entry, its cited fact is treated as an **operand**, not as the
source of the value:

```json
"derivations": {
  "dsri": {
    "expression": "receivables_t1 / receivables_t0",
    "operand_names": ["receivables_t1", "receivables_t0"],
    "operand_fact_ids": ["fact_ar_2024", "fact_ar_2023"],
    "result_value": 1.42
  }
}
```

Alternatively, the operands may be declared as an `operands` dictionary mapping
variable names directly to fact IDs:

```json
"derivations": {
  "dsri": {
    "expression": "receivables_t1 / receivables_t0",
    "operands": {
      "receivables_t1": "fact_ar_2024",
      "receivables_t0": "fact_ar_2023"
    },
    "result_value": 1.42
  }
}
```

Where a derivation formula requires a fixed mathematical baseline (such as 1.0
when calculating relative change: `(margin_t0 / margin_t1) - one`), numeric
literals in the expression are disallowed to ensure all inputs are explicit.
Instead, declare them in a `constants` dictionary:

```json
"derivations": {
  "growth": {
    "expression": "(revenue_t1 / revenue_t0) - one",
    "operands": {
      "revenue_t1": "fact_rev_2024",
      "revenue_t0": "fact_rev_2023"
    },
    "constants": {
      "one": 1.0
    },
    "result_value": 0.15
  }
}
```

Requirements:

1. An expression must be present (non-empty safe AST arithmetic: `+`, `-`, `*`, `/`, `(...)`).
2. At least one operand fact ID must be present.
3. Every operand variable used in `expression` must resolve to a declared operand fact or constant.
4. Every declared operand fact must exist in the ledger and be used in the expression (no unused operands).
5. Constant names must be non-empty and map to finite numeric values. Numeric literals in expression strings are rejected.
6. The claimed input value must equal `result_value` (and match recomputed AST value).

Because the cited fact is an operand, the operand's unit is not compared
against the derived ratio's unit — that would be a category error, and it is
why an `/A`-style derivation is allowed to draw on currency-denominated
operands.

**Stated plainly:** this establishes that the derivation is *declared* and
recomputes from facts that exist. It does **not** establish that the formula is
economically the right one for the index being computed. No code check settles
that.

---

## 7. Point-in-time availability

Every bound input must have been publicly available by the research cutoff. A
fact whose `availability_time` is after the cutoff is a look-ahead violation
and is refused.

For SEC-sourced facts the availability time is derived conservatively — see
[`docs/sec-availability-policy.md`](sec-availability-policy.md). In short: the
SEC `filed` field is a calendar date with no intraday publication time, so
availability is set to the **end** of the filed day (`23:59:59Z`), never its
start. This can exclude a fact that was in fact public earlier in the day; it
can never include one that was not yet public.

---

## 8. What binding establishes — and what it does not

**Establishes**, for each sourced input:

| # | Property |
|---|---|
| 1 | the referenced fact exists in the ledger |
| 2 | it belongs to the run's company |
| 3 | the supplied value equals the fact's recorded value |
| 4 | units share a compatible dimension, scale and currency |
| 5 | period and period type match |
| 6 | accounting basis is compatible |
| 7 | filing identity and source reference are well-formed |
| 8 | the information was available by the cutoff |
| 9 | a derived ratio has an inspectable derivation over existing facts |

**Does not establish** — no amount of code changes these:

- **That the fact is true.** Binding proves the witness number matches the
  recorded fact. It says nothing about whether the issuer reported the fact
  correctly.
- **That the fact is the economically appropriate input.** Binding a real
  trailing-revenue figure to a DSRI input is *semantically satisfied*. Whether
  DSRI is the right index to compute is an analytical judgement.
- **That the arithmetic is economically sound.** Replay proves the formula was
  applied to the stated inputs. Whether those inputs are the ones a competent
  analyst would use is not a mechanical property.
- **That the research conclusion follows.** Every level here is about
  provenance and arithmetic. None of them is about whether a stock is a good
  investment.

---

## 9. Unsourced inputs are disclosed, not failed

An input that cites **no** fact is reported as `unavailable`, not as a failure.

This distinction matters. A witness may legitimately record an index that has no
single underlying fact. Failing it would make the check unusable and push
operators to invent citations — the opposite of the goal.

Unverified inputs are surfaced in the audit's `unavailable` list, which:

- does **not** mark the run invalid;
- is **not** escalated by `--strict`;
- **is** printed by `verify-run` under *"Not established by this audit"*.

So `calculation_replay: passed` never silently means "every input was
source-backed". It means the arithmetic replayed, and the disclosure says how
much of it was anchored to a filing.

---

## 10. Implementation map

| Concern | Location |
|---|---|
| Numeric policy | `compare_numbers`, `NUMERIC_COMPARISON_POLICY` |
| Unit dimensions | `unit_dimension`, `units_compatible`, `describe_unit_mismatch` |
| Single-input check | `verify_input_binding` |
| Whole-witness check | `verify_witness_bindings` |
| Verdict extraction | `binding_failures`, `binding_notes` |
| Witness validator | `validate_witness` (`src/calculation_witness.py`) |
| Independent audit | `replay_beneish` (`src/run_semantic_audit.py`) |
| Tests | `tests/test_fact_binding.py`, `tests/test_adversarial_audit.py` |

`validate_witness` and `replay_beneish` both delegate to
`verify_witness_bindings`, so the runtime validator and the independent auditor
cannot drift apart in what they accept.