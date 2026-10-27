# Audit levels: what each one proves, and what it does not

`verify-run` runs six independent levels. They are **not** one aggregate score.
Each answers a different question, and each reports what it could not establish
as explicitly as what it proved.

The separation is the point. A calculation can be internally consistent without
being fact-backed. A fact-backed calculation can rest on an incorrect economic
assumption. Collapsing these into "verified / not verified" hides exactly the
distinctions an auditor needs.

Run: `python -m src.controller verify-run <run_dir> [--strict] [--require-source-backed] [--json]`

`--strict` preserves honest unavailable-source disclosures. For complete source-backed
core-forensics acceptance, add `--require-source-backed`; its policy is described below.

---

## Assurance level vs overall state

`Overall state: verified` answers one question: **did the audit pass?** It is not an
assurance level. A run whose forensic inputs rest on disclosed gaps passes the ordinary
audit, and reading "verified" as "the financial analysis was validated" is exactly the
misreading this section exists to prevent.

Every result therefore also carries an explicit **assurance** label, computed from the
same conditions the source-backed gate enforces:

| Assurance label | What it means |
|---|---|
| `source_backed` | Sealed bytes match, the core forensic witness replayed, all eight core inputs bound to typed facts, and no calculation gap is disclosed. |
| `internally_consistent_with_disclosed_gaps` | Sealed bytes match and the structure is internally consistent, but the run is **not** source-backed end to end. The unmet conditions are listed with the result. |
| `incomplete` | Only a completed subgraph was audited. Not a complete report. |
| `failed` | The audit found defects. No assurance level applies until they are resolved. |

**No assurance label means:** that the issuer's disclosures are accurate, that the
economic inputs were the right ones, or that the investment conclusion follows from the
evidence. Those are outside what provenance and arithmetic can decide.

A completed run is a claim about **execution**: every stage ran and produced output. A
source-backed run is a claim about **inputs**: the numbers trace to typed facts and
replay. The first never implies the second, and the label says which one you have.

---

## Summary

| # | Level | Question answered | Pass means |
|---|---|---|---|
| 1 | `envelope_integrity` | Have these bytes changed since sealing? | Nothing was altered after the fact |
| 2 | `source_reconstruction` | Can the source text be rebuilt from recorded evidence? | Quoted material traces to hashed evidence |
| 3 | `graph_and_contracts` | Is the run's structure internally consistent? | The dependency graph and contracts agree |
| 4 | `calculation_replay` | Do the numbers mean what they claim? | Arithmetic replays **and sourced inputs bind to facts** |
| 5 | `final_artifacts` | Do the deliverables match the recorded envelopes? | Published bytes are the audited bytes |
| 6 | `completeness` | Did everything that should have run, run? | No stage is silently missing |

---

## Level 1 — `envelope_integrity`

**Proves:** every envelope is schema-valid, and its recorded content checksum
matches its bytes.

**Does not prove:** that the content was ever *true*. A checksum detects
change; it says nothing about what was sealed. A run sealed around fabricated
numbers has perfectly valid checksums.

**Known limitation:** an attacker who re-seals after editing produces a
consistent checksum. Levels 1 and 5 are defeated by a complete re-seal; only
levels 3 and 4 survive that.

---

## Level 2 — `source_reconstruction`

**Proves:** where source documents exist, the text quoted in the run can be
reconstructed from hashed evidence whose integrity verifies.

**Does not prove:** that a reconstructed passage supports the conclusion drawn
from it. Reconstructing a quotation correctly says nothing about whether the
quotation answers the question.

**Reports `not_applicable`** for runs with no recorded source documents — a
mock run, or any run without an evidence pack. Absence is reported as absence,
never as a pass.

---

## Level 3 — `graph_and_contracts`

**Proves:**

- the dependency graph is acyclic and every declared parent exists;
- each envelope's `upstream` checksums match the referenced envelopes;
- the run manifest agrees with the envelopes on both graph and role bindings;
- duplicate role IDs are absent;
- agent contracts hold (for example, a calculator's recorded classification
  agrees with its recomputed score).

**Does not prove:** that the dependency graph reflects the *logical* order that
should have been used, or that an agent was given appropriate inputs.

**On stripped legacy lineage:** when an envelope's sealed `upstream` is empty
while execution recorded real parents, this is reported as a **warning** —
*"recorded lineage appears to have been stripped"*. It is missing data, so it is
disclosed rather than accused. Inventing a contradiction from absent data would
be fabricating a finding, which is the failure this whole audit exists to
prevent.

The converse is different and is a **hard error**: a manifest that records *no*
parents while envelopes record real ones is a genuine contradiction, not absent
data.

---

## Level 4 — `calculation_replay`

This is the level that changed most, and the one the success criterion depends on.

**Proves:**

1. the calculator re-executes to its recorded result;
2. for every **sourced** input, all nine binding properties hold:
   fact exists · belongs to this company · **the value equals the fact's
   value** · units are dimension-, scale- and currency-compatible · period and
   period type match · accounting basis matches · filing identity and source
   reference are well-formed · available by the cutoff · derivations are
   inspectable over existing facts.

**Does not prove:**

- that the underlying fact is true — only that the witness matches the ledger;
- that the fact is the *economically appropriate* input for this index;
- that the formula is the right economic choice;
- anything about the investment conclusion.

The numeric comparison is **exact by default**
(`exact-float-equality-v1`). See
[`fact-binding-policy.md`](fact-binding-policy.md) for why a blanket tolerance
is not used and how the opt-in scaled comparison works.

**Unsourced inputs are disclosed, not failed.** An input citing no fact is
reported in the run's `unavailable` list. It does not mark the run invalid and
is **not** escalated by `--strict` — a legitimate facts-free run must not be
made to fail. `verify-run` prints these under *"Not established by this audit
(disclosed gaps)"*.

`calculation_replay: passed` means that supported arithmetic replayed and cited
inputs passed binding. It does not mean every input was source-backed. Inspect
the `unavailable` list and coverage fields, not only the level label.

When the runner has all eight fact-derived Beneish indices, it uses their full
precision and bindings. Model-provided ratios must agree within the rounding
used in the prompt; a material disagreement stops the run. If runner inputs
are unavailable, model ratios can still be replayed, but source binding is
established only when the recorded supporting facts and derivations verify.

With zero replayed witnesses, the level is `not_applicable` when none were
attempted, or `not_replayable` when witnesses were attempted but could not replay.
Verified derived inputs do not give replay credit to an unsupported output formula.

### Opt-in source-backed gate

`--require-source-backed` fails unless all of the following hold:

- `source_reconstruction` is `source_bytes_verified`, not envelope-only facts;
- A3 BeneishM actually replays (`core_witnesses_replayed == 1`);
- all eight Beneish inputs are verified (`core_inputs_verified == 8`) and none
  remain unverified (`core_inputs_unverified == 0`);
- aggregate replay/binding coverage is nonzero and no calculation gap is disclosed.

Aggregate fields are `witnesses_attempted`, `witnesses_replayed`, `inputs_verified`
(also exposed as `inputs_bound`), and `inputs_unverified`. The separate `core_*`
fields prevent an unrelated identity metric from replacing missing A3 forensics.
The gate also checks embedded witnesses; an unsupported formula or an honest
unavailable metric still prevents complete source-backed acceptance.

This gate proves source-byte consistency and binding, not accurate extraction,
authentic issuer economics, or an economically appropriate choice of inputs.

A run that reports `one witness attempted, zero replayed, zero verified inputs` has
attempted forensics and established none. That is a weaker result than a run with
replayed witnesses, and weaker still than one whose eight core inputs all bound; the
counters distinguish them, and the assurance label above states which one applies.

---

## Level 5 — `final_artifacts`

**Proves:** the report, artifacts and provenance log on disk match the
envelopes the manifest binds — byte for byte, by hash.

**Does not prove:** that the report is correct, complete, or unbiased. It proves
the published file *is* the audited file.

For new model-produced A3 sections, the runner writes a Beneish result block
from the artifact and the audit compares that block in the report and envelope
with the artifact. The check does not verify other prose or historical runs
without this block; in particular, another section can still misstate a score.

**After `seal --force`:** a forced seal re-seals envelopes whose upstream bytes
changed and recomputes parent digests. The CLI states plainly that *"this run
now certifies the edited bytes, not the original execution."* A forced seal
restores the *consistency* of levels 1/3/5; it cannot restore a fact that was
never there.

---

## Level 6 — `completeness`

**Proves:** every stage the run's parameters called for actually produced
output, and no expected agent or artifact is silently missing.

**Does not prove:** that the outputs are good — only that nothing vanished
between the plan and the result.

---

## What no level establishes

These are not implementation gaps. They are outside what provenance and
arithmetic can decide, and no amount of code will settle them.

1. **Whether the issuer's disclosures are accurate.** The chain proves a number
   was reported by the company and used faithfully. It cannot prove the company
   reported it truthfully.
2. **Whether the analysis is economically sound.** Binding a real trailing-revenue
   figure to a DSRI input is *semantically satisfied*. Whether DSRI is the right
   index, or trailing revenue the right base, is an analyst's judgement.
3. **Whether the conclusion follows from the evidence.** Every level here is
   about provenance and arithmetic. None is about whether a stock is a good
   investment.
4. **Whether a Beneish score above −1.78 indicates fraud.** The threshold is a
   **screening signal** from published coefficients. It is not proof of
   misstatement, and the codebase must never describe it as such.
5. **Point-in-time accuracy at intraday resolution.** The SEC `filed` date has
   calendar-day precision only. See
   [`sec-availability-policy.md`](sec-availability-policy.md).

---

## Reading a report honestly

A run that reports all six levels passed is **internally consistent and
provenance-checked**. That is a real and useful property. It is not a
certification of the analysis.

The questions a full pass does **not** answer, restated as questions:

- Were the inputs the ones a competent analyst would choose?
- Does the cited fact say what the report claims it says?
- Is the conclusion supported by the evidence assembled?

When the `unavailable` list is non-empty, add one more:

- Which conclusions rest on numbers no filing supports?
