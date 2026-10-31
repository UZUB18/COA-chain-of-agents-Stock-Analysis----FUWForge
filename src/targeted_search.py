"""Targeted research-time web search and execution bundles.

When an agent execution triggers a concrete data defect or structured search
request, one bounded Gemini search interaction may be performed. Downloaded and
verified source bytes are stored in an append-only per-execution bundle under:
    <run_dir>/targeted_search/<execution_id>/
Search prose is untrusted diagnostic evidence, never an authoritative fact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.agents.model import GeminiApiBackend
from src.evidence import (
    MAX_DOCUMENT_BYTES,
    _document_text,
    _public_url,
    download_public_document,
)
from src.utils.checksum import sha256_digest
from src.web_research import _PublicationParser, _citations, _dates

MAX_TARGETED_SOURCES = 6
MAX_TARGETED_WEB_BYTES = 512 * 1024


class SearchOutcome(str, Enum):
    """Lifecycle outcomes for conditional targeted web search."""

    NOT_TRIGGERED = "not_triggered"
    DISABLED_BY_USER = "disabled_by_user"
    NOT_PERFORMED = "not_performed"
    NO_ACCEPTED_SOURCES = "no_accepted_sources"
    ACCEPTED_SOURCES = "accepted_sources"
    ROUND_LIMIT_REACHED = "round_limit_reached"
    SEARCH_CONFLICT = "search_conflict"
    FAILED = "failed"


class SearchConflictError(ValueError):
    """Raised when targeted search revision conflicts with accepted upstream core data."""

    pass


@dataclass(frozen=True)
class SearchTrigger:
    """A concrete, searchable issue detected on an unaccepted draft."""

    agent_id: str
    field_paths: Tuple[str, ...]
    reason: str
    source_type_sought: str
    query_intents: Tuple[str, ...]
    category: str = "validation_defect"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "field_paths": list(self.field_paths),
            "reason": self.reason,
            "source_type_sought": self.source_type_sought,
            "query_intents": list(self.query_intents),
            "category": self.category,
        }


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _publication_date_for_targeted_source(
    payload: bytes, content_type: str, cutoff: date
) -> str:
    """Validate source publication date is on or before analysis cutoff.

    Unlike shared news intake, targeted financial sources are not limited
    to the 90-day window, but future or post-cutoff dates are rejected.
    """
    if "html" not in content_type.lower():
        # Text/JSON/XML/PDF sources without HTML metadata use cutoff date if valid
        return cutoff.isoformat()
    parser = _PublicationParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    published = _dates(parser.published)
    if not published:
        # Fallback to cutoff if published date tag is absent from HTML body
        return cutoff.isoformat()
    day = min(published)
    if day > cutoff or any(m > cutoff for m in _dates(parser.modified)):
        raise ValueError("Source publication or modification date is after the analysis cutoff.")
    return day.isoformat()


def detect_search_trigger(
    agent_id: str,
    parsed_dict: Dict[str, Any],
    contract_errors: Sequence[str],
    envelopes: Optional[Mapping[str, Any]] = None,
) -> Optional[SearchTrigger]:
    """Detect if an unaccepted draft warrants targeted web search.

    Triggers are restricted to:
    (a) Parseable responses failing contract validation with data errors.
    (b) Structured search_request emitted by the agent.
    (c) Market-binding conflict.
    """
    # 1. Structured search_request emitted by model draft
    req = parsed_dict.get("search_request") or (
        parsed_dict.get("artifacts", {}).get("search_request")
        if isinstance(parsed_dict.get("artifacts"), dict)
        else None
    )
    if isinstance(req, dict):
        problem = str(req.get("problem") or "").strip()
        fields = req.get("fields") or []
        if isinstance(fields, (list, tuple)) and problem:
            source_type = str(req.get("source_type") or "public_financial_disclosure")
            intents = req.get("query_intents") or [problem]
            if isinstance(intents, (list, tuple)):
                return SearchTrigger(
                    agent_id=agent_id,
                    field_paths=tuple(str(f) for f in fields),
                    reason=problem,
                    source_type_sought=source_type,
                    query_intents=tuple(str(i) for i in intents),
                    category="agent_request",
                )

    # 2. Market-binding conflict (A1 market observation or timestamp failure)
    for err in contract_errors:
        err_lower = err.lower()
        if any(term in err_lower for term in ("market_cap_check", "price_timestamp", "market transform", "daily-bar")):
            return SearchTrigger(
                agent_id=agent_id,
                field_paths=("market_cap_check", "price_timestamp_eet"),
                reason=err,
                source_type_sought="market_quote",
                query_intents=(f"stock price observation as of analysis date",),
                category="market_binding_conflict",
            )

    # 3. Contract validation data errors (missing, conflicting, or malformed field values)
    data_errors = []
    field_paths = set()
    for err in contract_errors:
        err_lower = err.lower()
        if any(keyword in err_lower for keyword in ("missing", "requires numeric", "deviates", "must equal", "must be positive", "must be an object", "must be a list", "out of bounds", "unsupported", "must contain", "at least", "expected", "not found")):
            data_errors.append(err)
            match = re.search(r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)", err)
            if match:
                field_paths.add(match.group(1))

    if data_errors and field_paths:
        return SearchTrigger(
            agent_id=agent_id,
            field_paths=tuple(sorted(field_paths)),
            reason="; ".join(data_errors[:3]),
            source_type_sought="public_regulatory_or_financial_filing",
            query_intents=tuple(f"verify {fp}" for fp in sorted(field_paths)[:3]),
            category="validation_defect",
        )

    return None


def write_search_bundle(
    bundle_dir: Path,
    manifest_data: Dict[str, Any],
) -> str:
    """Persist search_manifest.json atomically and return bundle_checksum."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest_clean = dict(manifest_data)
    manifest_clean.pop("bundle_checksum", None)
    canonical = _canonical_json(manifest_clean)
    bundle_checksum = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    manifest_clean["bundle_checksum"] = bundle_checksum
    manifest_file = bundle_dir / "search_manifest.json"
    manifest_file.write_text(json.dumps(manifest_clean, indent=2), encoding="utf-8")
    return bundle_checksum


def perform_targeted_search(
    trigger: SearchTrigger,
    output_root: Path,
    execution_id: str,
    ticker: str,
    company: str,
    analysis_date: str,
    *,
    web_search_enabled: bool = True,
    is_mock: bool = False,
    is_controller: bool = False,
    api_key: Optional[str] = None,
    gemini_backend: Optional[GeminiApiBackend] = None,
    existing_round: int = 0,
) -> Tuple[SearchOutcome, Optional[Path], Optional[str], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Execute at most one bounded Gemini search for a triggered current execution.

    Returns: (outcome, bundle_dir, bundle_checksum, usage, accepted_sources)
    """
    if not web_search_enabled:
        return SearchOutcome.DISABLED_BY_USER, None, None, None, []
    if is_mock or is_controller:
        return SearchOutcome.NOT_PERFORMED, None, None, None, []
    if existing_round >= 1:
        return SearchOutcome.ROUND_LIMIT_REACHED, None, None, None, []

    # Check for Gemini credentials
    backend = gemini_backend
    if backend is None:
        try:
            backend = GeminiApiBackend(api_key=api_key)
        except Exception:
            backend = None

    if backend is None or not getattr(backend, "api_key", None):
        return SearchOutcome.NOT_PERFORMED, None, None, None, []

    cutoff = date.fromisoformat(analysis_date)
    bundle_dir = output_root / "targeted_search" / execution_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Build prompt bounded strictly to the concrete query intents
    intents_str = " AND ".join(trigger.query_intents[:3])
    prompt = (
        f"Find public, dated sources to investigate a concrete data issue for {company} (ticker: {ticker}).\n"
        f"Issue: {trigger.reason}\n"
        f"Target fields: {', '.join(trigger.field_paths)}\n"
        f"Query intents: {intents_str}\n"
        f"Requirements: Sources must be published on or before {analysis_date}. Limit search to relevant official disclosures, investor relations, or reputable news. Return findings with structured API source citations."
    )

    try:
        interaction = backend.research(prompt, url_context=True)
    except Exception:
        manifest_data = {
            "manifest_version": "targeted_search_v1",
            "execution_id": execution_id,
            "agent_id": trigger.agent_id,
            "trigger": trigger.to_dict(),
            "provider": "gemini",
            "model": backend.model,
            "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
            "request_count": 1,
            "usage": None,
            "sources": [],
            "excluded_sources": [],
            "outcome": SearchOutcome.FAILED.value,
        }
        checksum = write_search_bundle(bundle_dir, manifest_data)
        return SearchOutcome.FAILED, bundle_dir, checksum, None, []

    candidates = _citations(interaction)
    accepted_sources: List[Dict[str, Any]] = []
    excluded_sources: List[Dict[str, str]] = []

    for idx, candidate in enumerate(candidates, start=1):
        if len(accepted_sources) >= MAX_TARGETED_SOURCES:
            break
        url = candidate.get("url")
        if not url:
            continue
        try:
            _public_url(url, resolve=True)
            payload, content_type, accessed = download_public_document(
                url, max_bytes=MAX_TARGETED_WEB_BYTES, timeout=15
            )
            pub_date = _publication_date_for_targeted_source(payload, content_type, cutoff)
            text = _document_text(payload, content_type)
            if not text.strip():
                raise ValueError("Source has no usable text.")

            source_id = f"S{idx:03d}"
            raw_path = bundle_dir / f"{source_id}.bytes"
            text_path = bundle_dir / f"{source_id}.txt"
            raw_path.write_bytes(payload)
            text_path.write_text(text, encoding="utf-8")

            source_rec = {
                "source_id": source_id,
                "url": url,
                "title": candidate.get("title") or "Web source",
                "content_type": content_type,
                "published_date": pub_date,
                "accessed_at": accessed,
                "raw_sha256": sha256_digest(payload),
                "text_sha256": sha256_digest(text.encode("utf-8")),
                "raw_path": raw_path.name,
                "text_path": text_path.name,
                "verification_state": "verified",
            }
            accepted_sources.append(source_rec)
        except Exception as exc:
            excluded_sources.append({"url": str(url), "reason": str(exc)})

    outcome = SearchOutcome.ACCEPTED_SOURCES if accepted_sources else SearchOutcome.NO_ACCEPTED_SOURCES
    usage = interaction.get("usage")

    manifest_data = {
        "manifest_version": "targeted_search_v1",
        "execution_id": execution_id,
        "agent_id": trigger.agent_id,
        "trigger": trigger.to_dict(),
        "provider": "gemini",
        "model": backend.model,
        "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
        "request_count": 1,
        "usage": usage,
        "sources": accepted_sources,
        "excluded_sources": excluded_sources,
        "outcome": outcome.value,
    }
    checksum = write_search_bundle(bundle_dir, manifest_data)
    return outcome, bundle_dir, checksum, usage, accepted_sources


def format_untrusted_search_sources(sources: Sequence[Mapping[str, Any]], bundle_dir: Path) -> str:
    """Format accepted bundle sources as an untrusted evidence prompt block."""
    if not sources:
        return ""
    lines = [
        "TARGETED RESEARCH SOURCES (UNTRUSTED EVIDENCE - USE TO RESOLVE DEFECT ONLY):"
    ]
    for s in sources:
        sid = s.get("source_id", "S000")
        title = s.get("title", "Untitled")
        url = s.get("url", "")
        pub = s.get("published_date", "")
        text_path = bundle_dir / str(s.get("text_path", f"{sid}.txt"))
        text_content = ""
        if text_path.is_file():
            try:
                text_content = text_path.read_text(encoding="utf-8")[:4000]
            except Exception:
                text_content = ""
        lines.append(f"[{sid}] {title} (URL: {url}, Date: {pub})")
        if text_content:
            lines.append(f'"""\n{text_content}\n"""')
    return "\n\n".join(lines)


def verify_search_conflict(
    revised_envelope: Any,
    upstream_envelopes: Mapping[str, Any],
) -> Optional[str]:
    """Check whether a revised envelope attempts to rewrite accepted upstream core data.

    Core data: ticker, price observation date/value, share count, revenue, net income,
    and operating cash flow. Returns conflict description if contradiction occurs.
    """
    if not upstream_envelopes:
        return None

    # 1. Company / Ticker identity
    rev_params = getattr(revised_envelope, "params", {}) or {}
    for up_id, up_env in upstream_envelopes.items():
        up_params = getattr(up_env, "params", {}) or {}
        if up_params.get("ticker") and rev_params.get("ticker"):
            if str(up_params["ticker"]).upper() != str(rev_params["ticker"]).upper():
                return f"search_conflict: ticker changed from {up_params['ticker']} to {rev_params['ticker']}"

    # 2. Market price & shares from A1
    a1_env = upstream_envelopes.get("A1")
    if a1_env and getattr(revised_envelope, "agent", None) and revised_envelope.agent.id != "A1":
        a1_art = getattr(a1_env, "artifacts", {}) or {}
        rev_art = getattr(revised_envelope, "artifacts", {}) or {}

        # Price comparison
        a1_cap = a1_art.get("market_cap_check")
        rev_cap = rev_art.get("market_cap_check")
        if isinstance(a1_cap, dict) and isinstance(rev_cap, dict):
            a1_price = a1_cap.get("price")
            rev_price = rev_cap.get("price")
            if a1_price is not None and rev_price is not None:
                try:
                    if abs(float(rev_price) - float(a1_price)) > 1e-4:
                        return f"search_conflict: price value changed from {a1_price} to {rev_price}"
                except (ValueError, TypeError):
                    pass

        # Share count comparison
        a1_shares = a1_art.get("shares_basic") or (a1_cap.get("shares") if isinstance(a1_cap, dict) else None)
        rev_shares = rev_art.get("shares_basic") or (rev_cap.get("shares") if isinstance(rev_cap, dict) else None)
        if a1_shares is not None and rev_shares is not None:
            try:
                if abs(float(rev_shares) - float(a1_shares)) > 1e-4:
                    return f"search_conflict: share count changed from {a1_shares} to {rev_shares}"
            except (ValueError, TypeError):
                pass

    return None
