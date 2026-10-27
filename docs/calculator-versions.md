# Beneish M-Score Calculator Versions & Corrections

This document records the versioning, equation derivation, coefficient corrections, and policy boundaries for the deterministic eight-variable Beneish M-Score calculator (`src/utils/beneish.py`).

---

## 1. Overview of Changes in Version 2.0.0

The eight-variable Beneish M-Score calculator is versioned under identity `beneish_m_score` version `2.0.0`.

Two primary mathematical corrections and one major structural policy change were introduced relative to the legacy in-repo helper:

1. **Coefficient Correction: Total Accruals to Total Assets (`TATA`)**
   - Corrected from `+4.037` to `+4.679` to match the published formula.
2. **Coefficient Correction: Leverage Index (`LVGI`)**
   - Corrected from `+0.0327` to `-0.327`. The sign and order of magnitude both differed in the legacy implementation. Holding other inputs fixed, increasing LVGI lowers the score by 0.327 per unit in this equation. The coefficient alone does not establish a causal relationship between leverage and manipulation risk.
3. **Removal of Neutral Default Inputs**
   - The legacy wrapper supplied default ratios (`1.0`) when input indices were omitted, fabricating plausible-looking scores for incomplete data.
   - Version 2.0.0 eliminates all defaults. Every input ratio must be an explicit, finite float. Any missing input results in an explicit unavailable status (`unavailable_beneish`), never a fabricated number.
4. **Calculation Witness Records**
   - Every computed score is emitted with a calculation witness (`CalculationWitness`, `src/calculation_witness.py`) recording the formula ID, version (`2.0.0`), all 8 input ratios, parameter thresholds, and unrounded raw score so results can be audited and independently recomputed.

### Runner-derived inputs

On 2026-10-26 the runner's gross-margin index was corrected to use the prior
gross margin divided by the current gross margin. Earlier runner-derived inputs
could subtract one from that ratio, lowering the score by 0.528. The runner
also now refuses to combine annual flows with balance-sheet dates that cannot be
matched to the same periods. These changes do not change the score equation or
its calculator version.

Historical runs are not rewritten. Check a saved witness's GMI derivation and
periods, and create a fresh run when they are affected. Replaying a witness
checks its recorded arithmetic; it does not independently establish that the
recorded economic formula was appropriate.

The runner now requires consecutive fiscal years and comparable concepts across
both periods. General and administrative expense alone is not treated as total
selling, general and administrative expense. Recorded runner derivations are
checked for these cases during replay; other financial claims still need review.

---

## 2. Published Source and Coefficient Comparison

### Published Reference
- **Source**: European Central Bank (ECB) Occasional Paper Series No. 385, Equation 2, page 10.
- **Variant**: Eight-variable Beneish M-Score (`m-score-8-variable`).

### Published Equation
$$M = -4.84 + 0.920 \cdot \text{DSRI} + 0.528 \cdot \text{GMI} + 0.404 \cdot \text{AQI} + 0.892 \cdot \text{SGI} + 0.115 \cdot \text{DEPI} - 0.172 \cdot \text{SGAI} - 0.327 \cdot \text{LVGI} + 4.679 \cdot \text{TATA}$$

### Coefficient Side-by-Side Comparison

| Parameter / Index | Variable Description | Legacy Helper (v1.x) | Corrected v2.0.0 (ECB OP 385) | Delta / Status |
|---|---|---|---|---|
| **Intercept** | Constant term | `-4.84` | `-4.84` | Unchanged |
| **DSRI** | Days Sales in Receivables Index | `+0.920` | `+0.920` | Unchanged |
| **GMI** | Gross Margin Index | `+0.528` | `+0.528` | Unchanged |
| **AQI** | Asset Quality Index | `+0.404` | `+0.404` | Unchanged |
| **SGI** | Sales Growth Index | `+0.892` | `+0.892` | Unchanged |
| **DEPI** | Depreciation Index | `+0.115` | `+0.115` | Unchanged |
| **SGAI** | Sales, General & Admin Expenses Index | `-0.172` | `-0.172` | Unchanged |
| **LVGI** | Leverage Index | `+0.0327` | `-0.327` | **Corrected** (sign flipped, 10x magnitude fix) |
| **TATA** | Total Accruals to Total Assets | `+4.037` | `+4.679` | **Corrected** (+0.642 adjustment to published term) |

---

## 3. Threshold Policy & Screening Boundaries

- **Threshold Policy ID**: `ecb-385-eq2-conventional`
- **Screening Cutoff**: `-1.78`
  - $M > -1.78$: Classified as `High Manipulation Risk`
  - $M \le -1.78$: Classified as `Unlikely Manipulator`
- **Screening Signal Boundary**:
  - The classification is a statistical screening signal derived from historical financial ratios.
  - Crossing the $-1.78$ threshold is **not** a finding or proof that earnings manipulation or accounting fraud occurred. The calculator and pipeline do not make legal or forensic fraud findings.
  - Applicability, index derivations, and semantic interpretation remain the responsibility of the analyst and caller.

---

## 4. Immutability and Run Migration Policy

- **Legacy Runs are Immutable**: Archived historical runs executed prior to version 2.0.0 represent historical execution state. They must **not** be silently rewritten, modified, or resealed under version 2.0.0.
- **New Runs for New Version**: To obtain scores under calculator version 2.0.0 with calculation witnesses, fresh runs must be executed with explicit input ratios.
