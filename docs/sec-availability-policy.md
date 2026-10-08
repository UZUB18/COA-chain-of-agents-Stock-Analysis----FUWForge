# Point-in-time availability policy for SEC-sourced facts

Policy identifier: `sec-filed-date-end-of-day-v1`.

This document explains when a fact parsed out of an SEC `CompanyFacts`
document is treated as having been **publicly available**, and why the choice is
conservative in one direction only.

Implemented in `src/sec_facts.py`
(`AVAILABILITY_POLICY`, `_conservative_availability`).

---

## 1. The problem

Every source-backed calculation is only meaningful if the underlying number
was *public at the time the analysis claims to have been done*. A backtest that
uses figures filed after its own cutoff is not a test — it is hindsight with a
date label on it.

SEC `CompanyFacts` gives each observation an `end` (the economic period the
number describes), a `filed` date, a `frame`, an `fy`/`fp`, and a `form`. It
does **not** give a time of day.

## 2. Why the filing date is not a timestamp

A `filed` value of `2026-02-01` is consistent with a submission at 00:01 and
one at 23:59. The EDGAR API exposes no intraday publication timestamp for the
filing itself.

Claiming `2026-02-01T00:00:00Z` asserts the number was public at the instant the
date began. That assertion is **not supported by the evidence**, and it is the
direction of error that produces look-ahead bias: a run with a cutoff of
`2026-02-01T12:00:00Z` would accept a fact that in fact appeared that evening.

## 3. The chosen policy

**Availability is `YYYY-MM-DDT23:59:59Z`** — the end of the filed day.

```
filed = "2023-11-03"   ->   availability_time = "2023-11-03T23:59:59Z"
```

- A fact is usable by any cutoff at or after the **end** of its filing day.
- A fact is never usable by a cutoff earlier that same day.

### Why the asymmetry is the right one

| Situation | Effect of this policy |
|---|---|
| Filing truly appeared at 09:00; cutoff at 18:00 | **Fact excluded.** Conservative — a real, usable fact is withheld. |
| Filing truly appeared at 23:50; cutoff at 12:00 | **Fact excluded.** Correct — this is the look-ahead case. |
| Filing at 23:50; cutoff next morning | Fact included. Correct. |

Every error this policy can make is a **false exclusion**: it may withhold a
fact that was in fact public. It cannot admit a fact that was not.

The alternative — anchoring to the start of the filed day — can admit a fact
hours before it existed. In a provenance system, admitting an unavailable fact
is an unsoundness; excluding an available one is a disclosure. They are not
symmetric.

**The policy is recorded on every fact** as
`metadata.availability_policy = "sec-filed-date-end-of-day-v1"`, so a reader can
always tell how a date was derived.

### Unparseable dates

An empty, missing, or unparseable `filed` value yields
`1970-01-01T23:59:59Z`.

This makes the fact effectively unusable for any realistic cutoff, which is the
correct outcome: an observation whose filing date cannot be established has no
defensible point-in-time availability, and must not borrow one.

---

## 4. Repeated disclosures versus restatements

`CompanyFacts` is an **accumulated** dataset: it holds every observation ever
filed for a company/concept/period/unit, not just the latest. The same economic
period legitimately appears many times.

Labelling every later appearance as a "restatement" would be wrong — a company
re-files a 10-K and the XBRL viewer re-presents unchanged values routinely. That
would inflate the apparent restatement count and mislead anyone auditing
accounting changes.

`src/sec_facts.py` classifies each observation relative to the prior one, in
this precedence order:

| Relation | Trigger | Treatment |
|---|---|---|
| `initial_disclosure` | first observation for (concept, period, unit) | `REPORTED`; supersedes nothing |
| `amended` | form ends `/A` (e.g. `10-K/A`) | `RESTATEMENT`; supersedes prior |
| `repeated_disclosure` | same value, unit and accounting basis | `REPORTED`; **supersedes nothing** |
| `restated` | value differs from prior | `RESTATEMENT`; supersedes prior |
| `superseded_incomparable` | differs in unit or basis | flagged; not compared |

Two consequences worth stating:

- **An amendment form is a restatement even when the value is unchanged.** An
  `/A` filing is a formal correction; the *act* is the signal, not the delta.
- **A repeated disclosure does not supersede the original.** Two observations
  with different periods, units or accounting bases are **not** a restatement —
  they are different facts, and the audit must not silently replace one with
  another.

---

## 5. Provenance retained per fact

Every accepted fact records:

| Field | Purpose |
|---|---|
| `accession_number` | exact filing identity |
| `form` / `filed` | which filing, and when |
| `period_start` / `period_end` | the economic period described |
| `availability_time` | point-in-time availability (policy-derived) |
| `reported_value` / `unit` | the figure as filed |
| `extraction_method` | how it was obtained |
| `disclosure_relation` | initial / repeated / amended / restated |
| `prior_fact_id`, `prior_value` | the observation it supersedes, if any |
| `availability_policy` | the policy that derived the date |

`parse_sec_company_facts(..., as_of=...)` filters observations to those
available at the requested point in time, so a caller cannot accidentally
receive a post-cutoff fact.

---

## 6. What this policy does not establish

- **It is not a publication timestamp.** It is a conservative bound derived
  from a calendar date. The true intraday time is unknown and unknowable from
  this source.
- **It says nothing about whether the filing was accurate.** Availability is a
  question of *when*, not *whether*.
- **It does not detect an amended filing filed before the original**, or any
  irregularity in EDGAR's own accumulation.
- **It applies to SEC CompanyFacts only.** A different provider needs its own
  documented policy, not this one assumed across sources.