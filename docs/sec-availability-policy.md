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

### The research cutoff side of the comparison

A fact is usable when `availability_time <= cutoff`. The cutoff is converted by
`cutoff_instant()` in `src/facts.py`:

- A **date-only** cutoff `D` (an exact `YYYY-MM-DD` string or a `date`) means
  the **end of that UTC day**, `DT23:59:59Z`. A fact filed on `D` is usable in
  an analysis dated `D`; a fact filed on `D+1` is not. Evidence documents use
  the same boundary: a document dated `D` is accepted and one dated `D+1` is
  rejected (calendar dates are compared inclusively). The evaluation scorer
  applies the same rule to case cutoffs.
- An **explicit timestamp** keeps its instant. A cutoff of `DT00:00:00Z` or
  `DT12:00:00Z` still excludes a fact filed on `D`, because that fact's
  availability is `DT23:59:59Z`.

The two halves fit together: the filing side assumes the latest moment of the
filed day, and a date-only research cutoff covers that whole day. So a
date-only analysis on the filing day includes the fact, while any intraday
cutoff on that day excludes it. Economic periods, filing metadata and other
timestamps are still parsed with start-of-day semantics; only cutoffs use the
end-of-day rule. Daily market bars follow a separate collection-date rule and
are not governed by this conversion.

### Daily market bars (`market_daily_previous_completed_v2`)

Newly collected Yahoo or Stooq daily history records a versioned transform
with the retrieval instant (`collection_at`), its US/Eastern calendar date
(`collection_date_us_eastern`, via `America/New_York`) and the maximum
eligible bar date:

```text
eligible_through = min(analysis_date, collection_date_us_eastern - 1 day)
```

Bars dated after `eligible_through` are dropped before the last close and the
SMA/RSI indicators are computed, so the collection day's bar is excluded even
after the close. The last bar may be older (weekend or market holiday); history
whose last eligible bar is more than seven days older than `eligible_through`,
or that has no eligible bar, fails. Yahoo timestamps are converted to the
US/Eastern date; Stooq dates are used as given. The sealed text records
`last_bar_date` and whether a collection-day bar was excluded, and replay
re-derives `eligible_through` from `collection_at` rather than the wall clock.
The market document's `doc_date` is the bar date it reflects.

A transform without `transform_version` is the legacy rule (inclusive
analysis date, UTC bar dates) and still reconstructs unchanged for older packs.
The rule avoids a market-calendar dependency; it does not make provider-
adjusted history a point-in-time archive.

For a v2 pack, strict A1 output must use the bar date as a date-only
`price_timestamp_eet` (the key name is kept for compatibility) and the frozen
close (exact, or rounded to cents) as `market_cap_check.price`; a conflicting
value is a validation error that goes through the normal single repair.

**The policy is recorded on every fact** as
`metadata.availability_policy = "sec-filed-date-end-of-day-v1"`, so a reader can
always tell how a date was derived.

### Unparseable dates

An empty, missing, or unparseable `filed` value has **no availability timestamp**.
The parser excludes that observation and logs a warning, both with and without
an `as_of` cutoff. It cannot enter the numeric ledger or supersede a dated fact.
A historical epoch is not an unavailable marker: it would pass modern
`availability <= cutoff` checks, so the parser never uses one for an unknown
filing date.

A valid date prefix followed by malformed text is also rejected. When an
upstream fixture supplies a full timestamp, its calendar day is first
normalised to UTC, then bounded to the end of that UTC day. Naive timestamp
fixtures use UTC. The original supplied filing date remains in provenance.

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
| `repeated_disclosure` | same value, unit and accounting basis | `REPORTED`; supersedes the prior observation |
| `restated` | value differs from prior | `RESTATEMENT`; supersedes prior |
| `superseded_incomparable` | not a same-value repeat and not directly comparable (a null value, or a changed unit or accounting basis) | `RESTATEMENT`; supersedes prior; metadata records that the two were not directly comparable |

Intake groups observations by (taxonomy, concept, normalized unit, period), so a
unit change starts a separate group; an accounting-basis change does not.

Consequences worth stating:

- **An amendment form is a restatement even when the value is unchanged.** An
  `/A` filing is a formal correction; the *act* is the signal, not the delta.
- **Each concept-period has one linear history.** Every later observation
  supersedes the immediately prior one, so the original is always superseded
  by later filings and point-in-time resolution returns the latest observation
  available at the cutoff. A repeat and a restatement answer different
  questions: a repeat stays `REPORTED` (the original was never wrong, and
  `prior_fact_id` keeps its provenance), a restatement says the number changed.
- **Ledger consistency treats reported figures and restatements as one family.**
  An unlinked restatement that conflicts with a reported figure is flagged;
  forecasts and estimates are separate families.

---

## 5. Provenance retained per fact

Every accepted fact records:

| Field | Purpose |
|---|---|
| `filing_accession` | exact filing identity |
| `source_location.form` / `source_location.filed` | which filing, and when |
| `period_start` / `period_end` / `instant` | the economic period described |
| `availability_time` | point-in-time availability (policy-derived) |
| `value` / `unit` / `currency` | the figure as filed |
| `extractor_version` | which extractor produced it |
| `supersedes` | the immediately prior observation, if any |
| `metadata.disclosure_relation` | initial_disclosure / repeated_disclosure / amended / restated / superseded_incomparable |
| `metadata.prior_fact_id`, `metadata.prior_value` | the prior observation and its value |
| `metadata.availability_policy` | the policy that derived the date |

Custom or dimensional concepts are not turned into facts by automatic intake;
they are listed in the intake coverage as `deferred_concepts`.

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