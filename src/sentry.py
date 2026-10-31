# Jev Thesis Sentry for FUWForge: evaluates incoming filings/news against frozen thesis claims.

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import dotenv_values

from src.resources import resolve_env_path

_API_KEY_ENV = "TYPESAFE_API_KEY"
_ENV_PATH = resolve_env_path() or (Path(__file__).resolve().parents[1] / ".env")


@dataclass
class SentryResult:
    ticker: str
    company: str
    run_dir: str
    doc_snippet: str
    materiality: str
    materiality_prob: float
    action: str
    action_prob: float
    contradiction_scores: Dict[str, float]
    threatened_theses: List[Dict[str, Any]]
    requires_rerun: bool
    provider: str
    latency_ms: int
    threshold: float = 0.75
    #: Set when ``--provider auto`` asked for Jev and the call failed, so the
    #: result below came from the mock evaluator. ``None`` means no fallback.
    fallback_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_thesis_state(run_dir: Path) -> Tuple[str, str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extract ticker, company, BullCase theses, and DangerZone categories from a run directory.
    """
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")

    ticker = "UNKNOWN"
    company = "Unknown Company"
    bull_cases: List[Dict[str, Any]] = []
    danger_zone: List[Dict[str, Any]] = []

    # Try loading from a6b_envelope.json
    a6b_path = run_dir / "a6b_envelope.json"
    if a6b_path.exists():
        try:
            data = json.loads(a6b_path.read_text(encoding="utf-8"))
            params = data.get("params", {})
            ticker = params.get("ticker", ticker)
            company = params.get("company", company)
            bull_cases = data.get("artifacts", {}).get("BullCase", [])
        except Exception:
            pass

    # Try loading from a6_envelope.json for DangerZone
    a6_path = run_dir / "a6_envelope.json"
    if a6_path.exists():
        try:
            data = json.loads(a6_path.read_text(encoding="utf-8"))
            params = data.get("params", {})
            ticker = params.get("ticker", ticker)
            company = params.get("company", company)
            danger_zone = data.get("artifacts", {}).get("DangerZoneDashboard", [])
        except Exception:
            pass

    # Fallback to *_artifacts.json or a1_envelope.json if missing
    if not bull_cases:
        for art_file in sorted(run_dir.glob("*_artifacts.json")):
            try:
                data = json.loads(art_file.read_text(encoding="utf-8"))
                if "A6B" in data and isinstance(data["A6B"], dict):
                    a6b_env = data["A6B"]
                    bull_cases = a6b_env.get("artifacts", {}).get("BullCase", [])
                    p = a6b_env.get("params", {})
                    if p.get("ticker"):
                        ticker = p["ticker"]
                        company = p.get("company", company)
                elif "BullCase" in data:
                    bull_cases = data["BullCase"]
                if "A6" in data and isinstance(data["A6"], dict) and not danger_zone:
                    danger_zone = data["A6"].get("artifacts", {}).get("DangerZoneDashboard", [])
                if ticker == "UNKNOWN":
                    for env_val in data.values():
                        if isinstance(env_val, dict) and "params" in env_val:
                            p = env_val.get("params", {})
                            if p.get("ticker"):
                                ticker = p["ticker"]
                                company = p.get("company", company)
                                break
                if bull_cases:
                    break
            except Exception:
                pass

    if ticker == "UNKNOWN":
        for env_file in run_dir.glob("a*_envelope.json"):
            try:
                data = json.loads(env_file.read_text(encoding="utf-8"))
                params = data.get("params", {})
                if params.get("ticker"):
                    ticker = params["ticker"]
                    company = params.get("company", ticker)
                    break
            except Exception:
                pass

    return ticker, company, bull_cases, danger_zone


def _build_typesafe_questions(bull_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    from typesafe_sdk import Choice, Noul, NoulCriteria

    questions: Dict[str, Any] = {}

    for item in bull_cases:
        bid = item.get("id", "B")
        thesis = item.get("thesis", "Thesis claim")
        impact = item.get("impact", "")
        q_id = f"contradicts_{bid}"

        questions[q_id] = Noul(
            instructions={
                "question": f"Does the incoming text present credible evidence contradicting or degrading thesis '{thesis}'?",
                "focus": f"Judge whether the reported event directly undermines the thesis claim: '{thesis}' (Expected impact: {impact}).",
                "ignore": "Minor day-to-day noise or unrelated company segments.",
            },
            criteria=NoulCriteria(
                true={
                    "what": f"Credible, material evidence that '{thesis}' is faltering, broken, or suffering unexpected negative headwinds",
                    "examples": [
                        "Management reduced guidance citing this segment",
                        "Major customer cancellation or elevated churn",
                        "Gross margin compression or loss of pricing power",
                        "Regulatory ban, fatal security breach, or patent loss",
                    ],
                },
                false={
                    "what": f"No credible contradiction of '{thesis}', or the text actually supports/confirms it",
                    "examples": [
                        "Routine marketing announcements or minor updates",
                        "Financial results in line with or exceeding expectations",
                        "News about an unrelated business line",
                    ],
                },
            ),
        )

    questions["materiality"] = Choice(
        instructions={
            "question": "What is the materiality of this information to an institutional equity thesis?",
            "focus": "Classify the scale of impact on fundamental valuation and forward cash flows.",
        },
        criteria={
            "noise": {
                "what": "Routine marketing, minor social buzz, or normal daily volatility",
                "examples": ["New promotional video", "Minor blog post", "General macro commentary"],
            },
            "minor": {
                "what": "Small operational update or variance well within normal expectations",
                "examples": ["Routine vendor renewal", "Minor feature patch"],
            },
            "meaningful": {
                "what": "Substantive development affecting forward estimates, competitive moat, or regulatory stance",
                "examples": ["Analyst consensus revision", "New major OEM partnership", "Notice of inquiry"],
            },
            "critical": {
                "what": "Existential or thesis-invalidating event: fraud, sudden CEO exit, massive revenue guidance cut",
                "examples": ["SEC formal investigation into accounting", "400bps margin collapse", "Loss of primary customer"],
            },
        },
    )

    questions["recommended_action"] = Choice(
        instructions={
            "question": "What action should the FUWForge research system take based on this document?",
            "focus": "Decide whether the frozen equity model can be maintained or requires a complete re-run.",
        },
        criteria={
            "ignore": {
                "what": "No action needed, investment thesis remains intact",
                "examples": ["Routine news", "Noise"],
            },
            "log_note": {
                "what": "Log note for next scheduled review; thesis trajectory is not broken",
                "examples": ["Moderate development that doesn't breach scenario bounds"],
            },
            "trigger_deep_rerun": {
                "what": "Thesis assumption is under material threat; re-run FUWForge pipeline immediately",
                "examples": ["Severe contradiction on core bull theses", "Danger zone flag triggered"],
            },
        },
    )

    return questions


def _mock_evaluate(
    ticker: str,
    company: str,
    bull_cases: List[Dict[str, Any]],
    doc_text: str,
    threshold: float,
) -> Tuple[str, float, str, float, Dict[str, float], List[Dict[str, Any]], bool]:
    """
    Deterministic mock evaluator for testing and offline environments.
    # ponytail: simple substring/keyword heuristic to avoid external dependencies in tests.
    # Upgrade path: production uses live TypeSafeClient with Jev System One predictions.
    """
    text_lower = doc_text.lower()
    negative_signals = [
        "outage",
        "churn",
        "margin compress",
        "loss of",
        "cut guidance",
        "decline",
        "investigation",
        "lawsuit",
        "fraud",
        "resign",
        "breach",
        "fall short",
        "collapse",
    ]

    contradiction_scores: Dict[str, float] = {}
    threatened: List[Dict[str, Any]] = []

    has_neg = any(sig in text_lower for sig in negative_signals)

    for item in bull_cases:
        bid = item.get("id", "B")
        thesis = item.get("thesis", "").lower()
        words = [w for w in thesis.split() if len(w) > 4]

        # Check if text mentions words from this thesis alongside negative signals
        thesis_neg_match = has_neg and any(w in text_lower for w in words)

        if thesis_neg_match:
            score = 0.88
        elif has_neg:
            score = 0.45
        else:
            score = 0.06

        contradiction_scores[bid] = score
        if score >= threshold:
            threatened.append(item)

    if threatened:
        materiality = "critical" if any(s > 0.85 for s in contradiction_scores.values()) else "meaningful"
        materiality_prob = 0.92
        action = "trigger_deep_rerun"
        action_prob = 0.89
        requires_rerun = True
    elif has_neg:
        materiality = "meaningful"
        materiality_prob = 0.74
        action = "log_note"
        action_prob = 0.78
        requires_rerun = False
    else:
        materiality = "noise"
        materiality_prob = 0.95
        action = "ignore"
        action_prob = 0.94
        requires_rerun = False

    return materiality, materiality_prob, action, action_prob, contradiction_scores, threatened, requires_rerun


def _llm_evaluate(
    backend: Any,
    ticker: str,
    company: str,
    bull_cases: List[Dict[str, Any]],
    doc_text: str,
    threshold: float,
    danger_zone: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, float, str, float, Dict[str, float], List[Dict[str, Any]], bool]:
    from src.agents.model import extract_json_payload

    theses_lines = []
    for item in bull_cases:
        if isinstance(item, dict):
            bid = item.get("id", "B")
            thesis = item.get("thesis", "Thesis claim")
            impact = item.get("impact", "")
            theses_lines.append(f"- ID: {bid}, Thesis: \"{thesis}\", Expected impact: \"{impact}\"")

    theses_formatted = "\n".join(theses_lines)

    danger_block = ""
    if danger_zone:
        dz_lines = []
        for dz in danger_zone:
            if isinstance(dz, dict):
                cat = dz.get("category", "")
                flag = dz.get("flag", "")
                note = dz.get("note", "")
                dz_lines.append(f"- {flag} {cat}: {note}")
        danger_block = "\nKNOWN COMPANY RISKS / DANGER ZONE:\n" + "\n".join(dz_lines) + "\n"

    prompt = f"""You are an adversarial equity research sentry for {company} ({ticker}).
Evaluate the incoming document against the company's frozen bull theses:

FROZEN THESES:
{theses_formatted}
{danger_block}
INCOMING DOCUMENT:
\"\"\"{doc_text}\"\"\"

Task:
1. For each thesis ID, output the probability (0.0 to 1.0) that this document contradicts, undermines, or degrades that thesis.
   - 0.05 = text is benign, unrelated, or supportive.
   - 0.85+ = text provides credible evidence the thesis is failing or invalid.
2. Classify materiality: "noise", "minor", "meaningful", or "critical".
3. Recommend action: "ignore", "log_note", or "trigger_deep_rerun".

Respond STRICTLY in JSON format with this exact structure:
{{
  "materiality": "noise",
  "materiality_prob": 0.95,
  "action": "ignore",
  "action_prob": 0.94,
  "contradiction_scores": {{
    "B1": 0.05
  }}
}}"""

    raw = backend.generate(prompt)

    VALID_MATERIALITY = {"noise", "minor", "meaningful", "critical"}
    VALID_ACTIONS = {"ignore", "log_note", "trigger_deep_rerun"}

    data = None
    try:
        data = extract_json_payload(raw)
    except Exception:
        data = None

    if not isinstance(data, dict):
        # Fail closed on malformed or non-JSON output
        materiality = "critical"
        mat_prob = 1.0
        action = "trigger_deep_rerun"
        act_prob = 1.0
        contradiction_scores = {b.get("id", "B"): 1.0 for b in bull_cases}
        return materiality, mat_prob, action, act_prob, contradiction_scores, list(bull_cases), True

    raw_mat = str(data.get("materiality", "")).lower().strip()
    raw_action = str(data.get("action", "")).lower().strip()

    if raw_mat not in VALID_MATERIALITY or raw_action not in VALID_ACTIONS:
        materiality = "critical"
        mat_prob = 1.0
        action = "trigger_deep_rerun"
        act_prob = 1.0
        contradiction_scores = {b.get("id", "B"): 1.0 for b in bull_cases}
        return materiality, mat_prob, action, act_prob, contradiction_scores, list(bull_cases), True

    def _bounded_prob(val: Any, default: float) -> float:
        try:
            f = float(val)
            if math.isfinite(f):
                return max(0.0, min(1.0, f))
        except (ValueError, TypeError):
            pass
        return default

    materiality = raw_mat
    mat_prob = _bounded_prob(data.get("materiality_prob"), 0.90)
    action = raw_action
    act_prob = _bounded_prob(data.get("action_prob"), 0.90)

    raw_scores = data.get("contradiction_scores")
    if not isinstance(raw_scores, dict):
        raw_scores = {}

    contradiction_scores: Dict[str, float] = {}
    threatened: List[Dict[str, Any]] = []

    for item in bull_cases:
        if isinstance(item, dict):
            bid = item.get("id", "B")
            score = _bounded_prob(raw_scores.get(bid), 0.05)
            contradiction_scores[bid] = round(score, 4)
            if score >= threshold:
                threatened.append(item)

    requires_rerun = (action == "trigger_deep_rerun") or (len(threatened) > 0)
    return materiality, mat_prob, action, act_prob, contradiction_scores, threatened, requires_rerun


def evaluate_document(
    run_dir: Path,
    doc_text: str,
    provider: str = "auto",
    api_key: Optional[str] = None,
    threshold: float = 0.75,
    model: Optional[str] = None,
) -> SentryResult:
    """
    Evaluate a document using Jev, Gemini (Google AI Studio), OpenCode Go, or mock.
    """
    started = time.perf_counter()
    ticker, company, bull_cases, danger_zone = extract_thesis_state(run_dir)

    if not bull_cases:
        # Fallback generic thesis entry
        bull_cases = [{"id": "B1", "thesis": "Core revenue and earnings growth", "impact": "Baseline DCF"}]

    clean_text = doc_text.strip()
    snippet = clean_text[:120] + "..." if len(clean_text) > 120 else clean_text

    prov = provider.lower()
    if prov not in ("auto", "mock", "jev", "gemini", "opencode", "opencode-go", "opencode_go"):
        raise ValueError(f"Unknown monitor provider '{provider}'.")

    # Explicit live providers only: auto retains its Jev/mock behavior.
    if prov in ("gemini", "opencode", "opencode-go", "opencode_go"):
        from src.agents.model import GeminiApiBackend, OpenCodeGoBackend

        if prov == "gemini":
            backend = GeminiApiBackend(api_key=api_key, model=model)
            provider_name = "gemini"
        else:
            backend = OpenCodeGoBackend(api_key=api_key, model=model or "deepseek-v4.1-flash")
            provider_name = "opencode"
        (
            materiality,
            mat_prob,
            action,
            act_prob,
            contradiction_scores,
            threatened,
            requires_rerun,
        ) = _llm_evaluate(backend, ticker, company, bull_cases, clean_text, threshold, danger_zone=danger_zone)
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return SentryResult(
            ticker=ticker,
            company=company,
            run_dir=str(run_dir),
            doc_snippet=snippet,
            materiality=materiality,
            materiality_prob=mat_prob,
            action=action,
            action_prob=act_prob,
            contradiction_scores=contradiction_scores,
            threatened_theses=threatened,
            requires_rerun=requires_rerun,
            provider=f"{provider_name} ({backend.model})",
            latency_ms=latency_ms,
            threshold=threshold,
        )

    local = dotenv_values(_ENV_PATH, interpolate=False) if prov in ("auto", "jev") and _ENV_PATH.is_file() else {}
    resolved_jev_key = api_key or os.environ.get(_API_KEY_ENV) or local.get(_API_KEY_ENV)

    use_jev = False
    auto_fallback_reason: Optional[str] = None
    if prov == "jev":
        use_jev = True
    elif prov == "auto" and resolved_jev_key:
        use_jev = True

    if use_jev:
        try:
            from typesafe_sdk import TypeSafeClient

            client = TypeSafeClient(api_key=resolved_jev_key)
            questions = _build_typesafe_questions(bull_cases)
            state = {
                "company": company,
                "ticker": ticker,
                "incoming_document": clean_text,
            }

            prediction = client.system_one(
                model=model or "jev-latest",
                state=state,
                questions=questions,
            )
            answers = prediction.answers

            contradiction_scores: Dict[str, float] = {}
            threatened: List[Dict[str, Any]] = []

            for item in bull_cases:
                bid = item.get("id", "B")
                q_id = f"contradicts_{bid}"
                prob = float(answers[q_id].noul)
                if not 0.0 <= prob <= 1.0:
                    raise ValueError(f"Invalid contradiction probability for {bid}.")
                contradiction_scores[bid] = round(prob, 4)
                if contradiction_scores[bid] >= threshold:
                    threatened.append(item)

            mat_ans = answers["materiality"]
            materiality = mat_ans.choice
            mat_prob = float(mat_ans.probabilities[materiality])
            act_ans = answers["recommended_action"]
            action = act_ans.choice
            act_prob = float(act_ans.probabilities[action])
            if materiality not in ("noise", "minor", "meaningful", "critical") or not 0.0 <= mat_prob <= 1.0:
                raise ValueError("Invalid materiality answer.")
            if action not in ("ignore", "log_note", "trigger_deep_rerun") or not 0.0 <= act_prob <= 1.0:
                raise ValueError("Invalid recommended action answer.")

            requires_rerun = (action == "trigger_deep_rerun") or (len(threatened) > 0)
            latency_ms = max(1, int((time.perf_counter() - started) * 1000))

            return SentryResult(
                ticker=ticker,
                company=company,
                run_dir=str(run_dir),
                doc_snippet=snippet,
                materiality=str(materiality),
                materiality_prob=round(float(mat_prob), 4),
                action=str(action),
                action_prob=round(float(act_prob), 4),
                contradiction_scores=contradiction_scores,
                threatened_theses=threatened,
                requires_rerun=requires_rerun,
                provider="jev",
                latency_ms=latency_ms,
                threshold=threshold,
            )
        except Exception as exc:
            detail = str(exc).replace(resolved_jev_key, "[REDACTED]") if resolved_jev_key else str(exc)
            if prov == "jev":
                raise RuntimeError(f"TypeSafe Jev evaluation failed: {detail}") from None
            # auto: fall back to mock, but never silently. The reason travels
            # with the result so a mock verdict cannot read as a Jev verdict.
            auto_fallback_reason = f"TypeSafe Jev evaluation failed: {detail}"

    # Mock mode fallback (or auto fallback when no live keys present)
    (
        materiality,
        mat_prob,
        action,
        act_prob,
        contradiction_scores,
        threatened,
        requires_rerun,
    ) = _mock_evaluate(ticker, company, bull_cases, clean_text, threshold)

    latency_ms = max(1, int((time.perf_counter() - started) * 1000))

    return SentryResult(
        ticker=ticker,
        company=company,
        run_dir=str(run_dir),
        doc_snippet=snippet,
        materiality=materiality,
        materiality_prob=mat_prob,
        action=action,
        action_prob=act_prob,
        contradiction_scores=contradiction_scores,
        threatened_theses=threatened,
        requires_rerun=requires_rerun,
        provider="mock",
        latency_ms=latency_ms,
        threshold=threshold,
        fallback_reason=auto_fallback_reason,
    )


def format_sentry_report(res: SentryResult) -> str:
    """Format a human-readable Sentry audit report for terminal/logs."""
    status_icon = "🔴 THESIS BREACHED" if res.requires_rerun else "🟢 THESIS INTACT"
    lines = [
        f"════════════════════════════════════════════════════════════════════════",
        f"  FUWForge Jev Thesis Sentry — {res.company} ({res.ticker})",
        f"  Status: {status_icon} | Provider: {res.provider} ({res.latency_ms}ms)",
        f"════════════════════════════════════════════════════════════════════════",
        f"Document: \"{res.doc_snippet}\"",
        f"",
        f"Judgments:",
        f"  • Materiality:        {res.materiality.upper()} (prob: {res.materiality_prob:.2f})",
        f"  • Recommended Action: {res.action.upper()} (prob: {res.action_prob:.2f})",
        f"",
        f"Bull Thesis Contradiction Probabilities (P(Contradicts)):",
    ]
    if res.fallback_reason:
        anchor = next(i for i, ln in enumerate(lines) if ln.startswith("Document:")) + 1
        lines[anchor:anchor] = [
            f"",
            f"  !! NOT A JEV RESULT: --provider auto called Jev and it FAILED.",
            f"     The judgments below are deterministic MOCK output, not model",
            f"     probabilities. Reason: {res.fallback_reason}",
        ]

    for bid, score in res.contradiction_scores.items():
        flag = "🔴 ALERT" if score >= res.threshold else ("🟡 WATCH" if score >= (res.threshold * 0.5) else "🟢 OK")
        lines.append(f"  • [{bid}] {flag:<8}  Score: {score:.2f}")

    if res.threatened_theses:
        lines.append(f"")
        lines.append(f"Threatened Theses Detected:")
        for item in res.threatened_theses:
            lines.append(f"  - [{item.get('id')}]: {item.get('thesis')} (Impact: {item.get('impact')})")

    lines.append(f"")
    if res.requires_rerun:
        lines.append(f"⚡ ACTION REQUIRED: Trigger full pipeline re-run (`controller run`) and diff.")
    else:
        lines.append(f"✓ No pipeline re-analysis needed at this time.")
    lines.append(f"════════════════════════════════════════════════════════════════════════")

    return "\n".join(lines)
