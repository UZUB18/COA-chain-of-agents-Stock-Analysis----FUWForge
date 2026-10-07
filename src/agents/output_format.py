"""Lossless, explicit representation repairs for model envelopes (no facts invented)."""
from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Dict, List

from src.contracts import REQUIRED_ARTIFACTS
from src.utils.finance import resolve_weights


def _without_trailing_commas(text: str) -> str:
    result = []
    quoted = escaped = False
    for index, char in enumerate(text):
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        else:
            if char == '"':
                quoted = True
            if char == "," and text[index + 1:].lstrip().startswith(("}", "]")):
                continue
            result.append(char)
    return "".join(result)


def parse_object(raw_text: str) -> Dict[str, Any]:
    """Parse complete top-level objects only, never fragments of malformed JSON."""
    cleaned = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", raw_text).strip()
    if cleaned.startswith("["):
        raise ValueError("A JSON array is not an envelope object.")

    def reject_constant(value):
        raise ValueError("Non-finite numbers are not valid JSON.")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object keys are not accepted.")
            result[key] = value
        return result

    def load(text: str) -> Dict[str, Any]:
        try:
            value = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_keys)
        except json.JSONDecodeError:
            value = json.loads(_without_trailing_commas(text), parse_constant=reject_constant, object_pairs_hook=unique_keys)
        if not isinstance(value, dict):
            raise ValueError("Model response must contain a complete JSON object.")
        return value

    fences = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    candidates: List[Dict[str, Any]] = []
    if fences:
        for block in fences:
            try:
                candidates.append(load(block))
            except (ValueError, json.JSONDecodeError):
                if any(f'"{key}"' in block for key in ("schema_version", "agent", "artifacts", "sections_md")):
                    raise ValueError("Malformed outer JSON envelope; nested fragments are not accepted.") from None
    else:
        # Skip whole brace groups in prose, including malformed groups. Never
        # advance one character into a failed envelope (the old nested-recovery bug).
        index = 0
        while index < len(cleaned):
            start = cleaned.find("{", index)
            if start < 0:
                break
            depth = 0
            quoted = escaped = False
            end = None
            for cursor in range(start, len(cleaned)):
                char = cleaned[cursor]
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                elif char == '"':
                    quoted = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = cursor + 1
                        break
            if end is None:
                raise ValueError("Incomplete outer JSON object; nested fragments are not accepted.")
            block = cleaned[start:end]
            try:
                candidates.append(load(block))
            except (ValueError, json.JSONDecodeError):
                if start == 0 or any(f'"{key}"' in block for key in ("schema_version", "agent", "artifacts", "sections_md")):
                    raise ValueError("Malformed outer JSON envelope; nested fragments are not accepted.") from None
            index = end
    for candidate in candidates:
        if any(key in candidate for key in ("schema_version", "agent", "artifacts")):
            return candidate
    if candidates:
        return max(candidates, key=len)
    raise ValueError("Could not extract a complete JSON object from model output.")


def canonical_artifacts(agent_id: str, data: Dict[str, Any], sections: Dict[str, Any]) -> Dict[str, Any]:
    """Map known alternative locations without overwriting canonical model data."""
    artifacts = copy.deepcopy(data.get("artifacts")) if isinstance(data.get("artifacts"), dict) else {}
    for key in REQUIRED_ARTIFACTS.get(agent_id, []):
        if key not in artifacts and key in data:
            artifacts[key] = copy.deepcopy(data[key])
    if agent_id != "A7":
        return artifacts
    score_section = sections.get("SECTION_16_SCORECARD")
    if "Scorecard" not in artifacts and isinstance(score_section, dict):
        scores = score_section.get("category_scores_10")
        if isinstance(scores, dict):
            aliases = {
                "Financials": "financials", "Moat": "moat", "Growth": "growth", "Valuation": "valuation",
                "MgmtGov": "mgmt_gov", "RiskInverse": "risk_inverse", "ESG": "esg", "Market": "market",
            }
            artifacts["Scorecard"] = {category: copy.deepcopy(scores[alias]) for category, alias in aliases.items() if alias in scores}
    swot_section = sections.get("SECTION_15_SWOT")
    if "SWOT" not in artifacts and isinstance(swot_section, dict):
        artifacts["SWOT"] = {
            category: copy.deepcopy(swot_section.get(category, swot_section.get(category.lower(), [])))
            for category in ("Strengths", "Weaknesses", "Opportunities", "Threats", "Watchlist")
        }
    glossary = sections.get("SECTION_17_GLOSSARY")
    if "Glossary" not in artifacts:
        if isinstance(glossary, list):
            artifacts["Glossary"] = copy.deepcopy(glossary)
        elif isinstance(glossary, dict) and isinstance(glossary.get("terms"), list):
            artifacts["Glossary"] = copy.deepcopy(glossary["terms"])
    return artifacts


def weighted_scorecard(existing: Any, weight_spec: Any) -> Dict[str, Any]:
    """Recompute derived cells using supplied scores, never a guessed category score."""
    weights, errors = resolve_weights(weight_spec)
    if errors or not isinstance(existing, dict):
        raise ValueError("Scorecard requires supplied category scores and valid run weights.")
    result = {}
    for category, weight in weights.items():
        cell = existing.get(category)
        value = cell.get("score") if isinstance(cell, dict) else cell
        if isinstance(value, bool):
            raise ValueError(f"Scorecard.{category}.score must be supplied as a finite number.")
        try:
            score = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Scorecard.{category}.score must be supplied as a finite number.") from None
        if not math.isfinite(score) or not 0 <= score <= 10:
            raise ValueError(f"Scorecard.{category}.score must be between 0 and 10.")
        result[category] = {"score": score, "weight": weight, "weighted": round(score * weight, 3)}
    total = round(sum(cell["weighted"] for cell in result.values()), 2)
    result["Overall"] = {"score": total, "rating_band": "Buy" if total >= 7.5 else ("Hold" if total >= 6 else "Sell")}
    return result
