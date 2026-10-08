# Research Evaluation Policy

## 1. Objectives and Scope

The FUWForge evaluation harness provides deterministic, error-focused scoring for multi-agent equity research pipelines. Its primary purpose is to identify failure modes that mislead human decision-makers: unsupported narrative claims, arithmetic errors, temporal leakage, unit-scaling errors, unhandled missing inputs, and prompt injection vulnerabilities.

Evaluation is structured into six orthogonal categories and two critical safety sub-checks:
- **Unsupported Claims:** Detects factual assertions and forward-looking claims lacking verifiable citations in the evidence bundle.
- **Arithmetic & Formula Accuracy:** Validates quantitative models (e.g. Beneish M-Score, Altman Z-Score, diluted EPS) against exact deterministic formulas.
- **Chronology (`usable_as_of`):** Enforces anti-look-ahead boundaries so that facts or filings made available after the analysis cutoff date are strictly rejected.
- **Source References:** Verifies that cited source identifiers exist in the bounded evidence pack and that amended filings supersede obsolete reports.
- **Cost & Latency:** Quantifies token consumption, dollar cost, and wall-clock latency per case.
- **Reviewer Effort:** Measures the number of open items, missing disclosures, and required human verification actions.
- **Missingness Handling (`missingness_ok`):** Confirms that absent data is explicitly reported as unavailable rather than silently fabricated with plausible defaults.
- **Injection Resistance (`injection_safe`):** Flags instances where models follow instructions embedded in untrusted evidence text.

---

## 2. Deterministic Scoring as Ground Truth

Deterministic scoring is the primary ground truth of the evaluation framework. Model judges (LLM-as-a-judge) may be employed for exploratory qualitative sampling, but cannot serve as the authoritative standard for numeric, temporal, or attribution correctness:
1. Writing fluency must never compensate for numeric or factual failure: persuasive prose with an incorrect financial formula fails the arithmetic score.
2. Exact formula calculation takes precedence over model consensus or qualitative plausibility.
3. Provenance and source existence are verified through direct indexing, not model opinion.

---

## 3. Historical Replay and Temporal Leakage Caveat

When running retrospective evaluations on historical cutoff dates:
- **Source-Faithfulness, Not Predictive Validity:** Filtering retrieved documents to an old cutoff date tests whether the system is faithful to historical sources. It does **not** prove prospective investment performance.
- **Parametric Memory Leakage:** LLMs may carry post-cutoff corporate outcomes in their pre-training weights. An accurate retrospective narrative may reflect memorization rather than inference from the provided sources.
- **Policy Rule:** Do not market retrospective evaluation scores as proof of investable market alpha or predictive validity. Prospective archived evaluations on out-of-sample forward dates remain the only valid test of forecasting capability.

---

## 4. Review Priority Heuristic

When information is missing or uncertain, the evaluation harness scores "Reviewer Effort" using a transparent heuristic called **Review Priority**:
1. Missing inputs are ranked by their potential to change a valuation model, thesis rule, or distress classification (e.g., missing Beneish TATA or restated revenues are High Priority).
2. Items are secondary-ranked by estimated retrieval and verification cost.
3. **Terminology Rule:** This scoring is explicitly designated as **Review Priority**. Hand-tuned ranking weights must **not** be misrepresented as formal Expected Value of Information (EVOI) or Bayesian decision-theoretic utility.

---

## 5. Upgrade Acceptance and Promotion Rule ("Promote Only If")

New pipeline stages, such as an independent first-pass bear hypothesis (Task 8 A6 challenge) or expanded retrieval passes, must be evaluated against frozen test sets under the following strict rule:
- **Criterion:** Promote a workflow upgrade **only if** it catches additional material factual/arithmetic errors at an acceptable marginal cost and latency.
- **Non-Criteria:** Increased report length, higher aesthetic fluency, or higher inter-agent agreement rates are **not** acceptance criteria. A pipeline that produces high agreement on erroneous data is worse than a pipeline that exposes honest disagreement.
