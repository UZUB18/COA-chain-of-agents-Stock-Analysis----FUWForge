"""Explicit public-document retrieval and hash-checked analysis evidence packs.

Models may suggest discovery URLs; only validated runner downloads claim retrieval.
Retrieval proves which bytes were supplied, not the truth of every claim in them.
"""
from __future__ import annotations

import csv
import html
import io
import ipaddress
import math
import json
import os
import re
import shutil
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from src.resources import resolve_env_path
from src.utils.checksum import sha256_digest

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 16
MAX_ATTACHMENTS = 16
_ENV_PATH = resolve_env_path() or (Path(__file__).resolve().parents[1] / ".env")
_LAST_SEC_REQUEST = 0.0
_SEC_THROTTLE_LOCK = threading.Lock()
INTAKE_COVERAGE_VERSION = "intake_coverage_v1"
#: Daily-bar policy for newly collected market history. A transform without
#: ``transform_version`` is the legacy (v1) rule and reconstructs unchanged.
MARKET_TRANSFORM_V2 = "market_daily_previous_completed_v2"
_US_EASTERN = ZoneInfo("America/New_York")


class DocumentTooLargeError(ValueError):
    """A response exceeded :data:`MAX_DOCUMENT_BYTES`.

    Raised only by the bounded download so callers can tell this limit apart
    from transport, checksum or parse failures without matching message text.
    """

    def __init__(self) -> None:
        super().__init__("Evidence document exceeds the 8 MiB size limit.")


def sec_user_agent() -> str:
    local = dotenv_values(_ENV_PATH, interpolate=False) if _ENV_PATH.is_file() else {}
    value = (os.environ.get("SEC_USER_AGENT") or local.get("SEC_USER_AGENT") or "").strip()
    if not value or "@" not in value or "\n" in value or "\r" in value:
        raise ValueError("Set SEC_USER_AGENT locally to your organization/contact email for SEC fair-access requests.")
    return value


def _validate_peer_address(response: Any) -> None:
    try:
        sock = getattr(response, "fp", None)
        raw = getattr(sock, "raw", None)
        sock_obj = getattr(raw, "_sock", None) or getattr(response, "sock", None)
        if sock_obj is not None and hasattr(sock_obj, "getpeername"):
            peer = sock_obj.getpeername()
            if isinstance(peer, (tuple, list)) and len(peer) >= 1:
                ip = ipaddress.ip_address(peer[0])
                if not ip.is_global:
                    raise ValueError(f"Connected peer IP {ip} is not a public global IP.")
    except (AttributeError, OSError, TypeError):
        pass


def download_public_document(url: str):
    """Bounded public download. Never sends model API keys or SEC contact to other hosts."""
    global _LAST_SEC_REQUEST
    url = _public_url(url, resolve=True)
    host = urllib.parse.urlsplit(url).hostname
    is_sec = host in ("sec.gov", "www.sec.gov", "data.sec.gov")
    user_agent = sec_user_agent() if is_sec else "FUWForge/2.1 public-document-retrieval"
    if is_sec:
        with _SEC_THROTTLE_LOCK:
            elapsed = time.monotonic() - _LAST_SEC_REQUEST
            if elapsed < 0.25:
                time.sleep(0.25 - elapsed)
            _LAST_SEC_REQUEST = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(_NoRedirects())
    with opener.open(req, timeout=30) as response:
        _validate_peer_address(response)
        if response.status != 200:
            raise ValueError(f"Evidence request did not succeed: HTTP {response.status}.")
        if response.headers.get("Content-Encoding", "identity") not in ("", "identity"):
            raise ValueError("Compressed evidence responses are not supported.")
        payload = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise DocumentTooLargeError()
        content_type = response.headers.get("Content-Type", "")
    accessed = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return payload, content_type, accessed


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "div", "br", "tr", "td", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _public_url(url: str, *, resolve: bool = False) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Evidence requires a public HTTPS URL without credentials.")
    if parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError("Evidence URLs cannot use custom ports or fragments.")
    for key, _ in urllib.parse.parse_qsl(parsed.query):
        if key.lower() in ("key", "api_key", "apikey", "token", "access_token", "signature"):
            raise ValueError("Do not put credentials in evidence URLs.")
    host = parsed.hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private hosts cannot be retrieved as public evidence.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private IP addresses cannot be retrieved as evidence.")
    if resolve:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("Evidence host resolves to a non-public IP address.")
    return url


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Evidence redirects are disabled; supply the final public document URL.")


def market_daily_transform(*, kind: str, ticker: str, symbol: str, analysis_date: str, collection_at: str) -> Dict[str, Any]:
    """Freeze the v2 daily-bar rule for a newly collected market history.

    The maximum eligible bar date is the earlier of the requested analysis date
    and the day before the US/Eastern collection date, so the collection day's
    bar is excluded whether it is intraday or after the close. It is derived
    from the recorded retrieval instant, never from the wall clock at replay.
    """
    if not isinstance(collection_at, str):
        raise ValueError("Market transform requires a recorded collection instant.")
    collected = datetime.fromisoformat(collection_at.replace("Z", "+00:00"))
    if collected.tzinfo is None:
        raise ValueError("Market transform collection instant must be timezone-aware.")
    eastern = collected.astimezone(_US_EASTERN).date()
    eligible = min(date.fromisoformat(analysis_date), eastern - timedelta(days=1))
    return {
        "transform_version": MARKET_TRANSFORM_V2,
        "kind": kind, "ticker": ticker, "symbol": symbol, "analysis_date": analysis_date,
        "collection_at": collection_at,
        "collection_date_us_eastern": eastern.isoformat(),
        "eligible_through": eligible.isoformat(),
    }


def _transformed_text(payload: bytes, transform: Dict[str, Any]) -> str:
    """Curate date-bounded market history, excluding current/future quote metadata."""
    kind = transform.get("kind")
    as_of = date.fromisoformat(transform["analysis_date"])
    ticker = str(transform["ticker"]).upper()
    version = transform.get("transform_version")
    if version is None:
        # Legacy v1: inclusive analysis date, UTC bar dates. Kept exactly so
        # archived sealed bytes re-derive to the same text.
        cutoff, eastern = as_of, None
    elif version == MARKET_TRANSFORM_V2:
        expected = market_daily_transform(
            kind=kind, ticker=transform.get("ticker"), symbol=transform.get("symbol"),
            analysis_date=transform["analysis_date"], collection_at=transform.get("collection_at"),
        )
        if transform != expected:
            raise ValueError("Market transform metadata is inconsistent with its recorded collection instant.")
        cutoff = date.fromisoformat(expected["eligible_through"])
        eastern = date.fromisoformat(expected["collection_date_us_eastern"])
    else:
        raise ValueError("Unsupported market transform version.")
    excluded = set()
    history = []
    if kind == "yahoo_daily":
        data = json.loads(payload)
        chart = data.get("chart") or {}
        if chart.get("error") or not chart.get("result"):
            raise ValueError("Public price provider returned an error or empty chart.")
        result = chart["result"][0]
        symbol = result.get("meta", {}).get("symbol", "").upper()
        if symbol != str(transform.get("symbol") or ticker).upper():
            raise ValueError("Public price history ticker mismatch.")
        timestamps = result.get("timestamp") or []
        quotes = (result.get("indicators", {}).get("quote") or [{}])[0]
        if len(quotes.get("close") or []) != len(timestamps):
            raise ValueError("Public price history has inconsistent quote/timestamp arrays.")
        for index, timestamp in enumerate(timestamps):
            day = datetime.fromtimestamp(timestamp, timezone.utc)
            day = (day if eastern is None else day.astimezone(_US_EASTERN)).date()
            if day > cutoff:
                excluded.add(day)
                continue
            row = {"Date": day.isoformat()}
            for key in ("open", "high", "low", "close", "volume"):
                values = quotes.get(key) or []
                value = values[index] if index < len(values) else None
                row[key.title()] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
            if row["Close"] is not None and row["Close"] > 0:
                history.append(row)
        basis = "Yahoo chart quote.close (not dividend-adjusted adjclose; provider may adjust for splits). Not a point-in-time corporate-action archive."
    elif kind == "stooq_daily":
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))
        if reader.fieldnames != ["Date", "Open", "High", "Low", "Close", "Volume"]:
            raise ValueError("Public CSV price provider returned no valid OHLCV header.")
        for item in reader:
            day = date.fromisoformat(item["Date"])
            if day > cutoff:
                excluded.add(day)
                continue
            row = {"Date": day.isoformat()}
            for key in ("Open", "High", "Low", "Close", "Volume"):
                try:
                    value = float(item[key])
                except (TypeError, ValueError):
                    value = None
                row[key] = value if value is not None and math.isfinite(value) else None
            if row["Close"] is not None and row["Close"] > 0:
                history.append(row)
        basis = "Stooq historical OHLCV; provider corporate-action adjustment policy applies. Not original point-in-time nominal quotes."
    else:
        raise ValueError("Unknown evidence transformation; only supported market-history transforms are allowed.")
    history.sort(key=lambda row: row["Date"])
    if not history or len({row["Date"] for row in history}) != len(history):
        raise ValueError("Price history is empty or has duplicate dates.")
    if (cutoff - date.fromisoformat(history[-1]["Date"])).days > 7:
        raise ValueError(
            "Public price history is stale relative to the requested analysis date." if eastern is None
            else "Public price history is stale relative to its maximum eligible bar date."
        )
    closes = [row["Close"] for row in history]
    technicals = {f"SMA_{period}": round(sum(closes[-period:]) / period, 4) if len(closes) >= period else None for period in (50, 200)}
    rsi = None
    if len(closes) >= 15:
        changes = [right - left for left, right in zip(closes, closes[1:])]
        gain = sum(max(change, 0) for change in changes[:14]) / 14
        loss = sum(max(-change, 0) for change in changes[:14]) / 14
        for change in changes[14:]:
            gain = (gain * 13 + max(change, 0)) / 14
            loss = (loss * 13 + max(-change, 0)) / 14
        rsi = 100 - 100 / (1 + gain / loss) if loss else (100.0 if gain else 50.0)
    technicals["RSI_14"] = round(rsi, 4) if rsi is not None else None
    output = {
        "dataset": "date-bounded daily market history", "ticker": ticker, "analysis_date": as_of.isoformat(),
        "price_basis": basis, "last_bar_date": history[-1]["Date"], "last_close": history[-1]["Close"],
        "TechnicalSet": technicals,
        "derivation": "SMA from retrieved closes; Wilder RSI(14) seeded in the retrieved history. Null means insufficient history, never a guessed value.",
    }
    if eastern is not None:
        output.update({
            "transform_version": version,
            "collection_at": transform["collection_at"],
            "collection_date_us_eastern": eastern.isoformat(),
            "eligible_through": cutoff.isoformat(),
            "collection_day_bar_excluded": eastern in excluded,
            "excluded_bar_count": len(excluded),
            "bar_policy": (
                f"Only daily bars dated on or before {cutoff.isoformat()} are used: the earlier of the analysis date "
                f"and the day before the US/Eastern collection date ({eastern.isoformat()}). A collection-day bar is "
                "excluded whether intraday or after the close. last_bar_date may be earlier (weekend or market "
                "holiday). last_close is the provider's daily-bar close field for last_bar_date; no time of day is "
                "recorded, and the retrieval time is not a price timestamp."
            ),
        })
    output["history"] = history
    return json.dumps(output, indent=2)


def _document_text(payload: bytes, content_type: str, transform=None) -> str:
    if transform:
        return _transformed_text(payload, transform)
    if "pdf" in content_type.lower():
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(payload), strict=True)
        except Exception:
            raise ValueError("PDF evidence could not be parsed safely.") from None
        if reader.is_encrypted or len(reader.pages) > 300:
            raise ValueError("Encrypted PDFs and PDFs over 300 pages are not supported.")
        text = "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
        if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("Extracted PDF text exceeds the evidence size limit.")
    elif any(kind in content_type.lower() for kind in ("text/", "json", "xml")):
        text = payload.decode("utf-8", errors="replace")
    else:
        raise ValueError("Unsupported binary evidence format; use text, HTML, JSON, XML, CSV or PDF.")
    if "html" in content_type.lower():
        parser = _TextParser()
        parser.feed(text)
        text = "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip())
    if not text.strip():
        raise ValueError("Evidence document contains no usable text.")
    return text


def fetch_evidence(
    manifest: Path,
    output_dir: Path,
    attachments: Optional[List[Dict[str, Any]]] = None,
    *,
    intake_coverage: Optional[Dict[str, Any]] = None,
    omittable_document_indices: frozenset = frozenset(),
    prefetched: Optional[Dict[str, Tuple[bytes, str, str]]] = None,
) -> Path:
    """
    Download only explicit manifest URLs, with bounded size and no redirects.

    ``attachments`` is an optional list of already-retrieved, already-hashed
    files to copy into the pack verbatim, each of the form
    ``{"path": Path, "kind": str}``. The bytes are not re-downloaded; they are
    copied and re-hashed on load. This keeps non-document artefacts (typed fact
    ledgers, SEC CompanyFacts, web-research traces) under the same checksum and
    containment guarantees as documents. An optional ``metadata`` mapping on a
    spec is stored with its attachment record.

    Explicit manifests are fail-closed. Only the automatic collector passes
    ``omittable_document_indices`` (one-based manifest positions of older
    annual filings) together with ``intake_coverage``: for those positions, and
    only for :class:`DocumentTooLargeError`, the document is omitted and the
    omission is recorded in ``intake_coverage["annual_filings"]``. Every other
    error, and every other position, still fails the pack. The coverage object
    is stored in ``evidence.json`` so the pack checksum covers it.

    ``prefetched`` maps a manifest URL to an already validated
    ``(payload, content_type, accessed)`` response from the same bounded
    downloader (the collector's price probe). Those exact bytes are sealed
    without a second request; each entry is used once and must name a
    manifest document.
    """
    config = json.loads(Path(manifest).read_text(encoding="utf-8"))
    ticker = str(config.get("ticker") or "").strip().upper()
    documents = config.get("documents")
    if not ticker or not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise ValueError(f"Evidence manifest requires ticker and 1–{MAX_DOCUMENTS} documents.")
    # Enforce the load-time limits before anything is written, so a pack that
    # could never load is never created.
    attachments = list(attachments or [])
    if len(attachments) > MAX_ATTACHMENTS:
        raise ValueError(f"Evidence packs allow at most {MAX_ATTACHMENTS} attachments.")
    reserved = {"evidence.json"} | {
        f"D{index:03d}.{suffix}" for index in range(1, len(documents) + 1) for suffix in ("bytes", "txt")
    }
    seen_names: set = set()
    for spec in attachments:
        filename = str(spec.get("name") or Path(spec["path"]).name)
        if Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise ValueError("Evidence attachment filenames must be simple relative names.")
        if filename in reserved or filename in seen_names:
            raise ValueError(f"Evidence attachment name {filename!r} is reserved or duplicated.")
        seen_names.add(filename)
        source = Path(spec["path"])
        if not source.is_file():
            raise ValueError(f"Evidence attachment source {source} is not a readable file.")
        if source.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError(f"Evidence attachment {filename!r} exceeds the document size limit.")
    pending = dict(prefetched or {})
    if not set(pending) <= {str(document.get("url")) for document in documents}:
        raise ValueError("Prefetched evidence responses must belong to manifest documents.")
    omittable = frozenset(omittable_document_indices)
    annual_coverage = (intake_coverage or {}).get("annual_filings")
    if omittable and (not isinstance(annual_coverage, dict) or 1 in omittable):
        raise ValueError("Document omission requires annual intake coverage and never applies to the first document.")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for index, document in enumerate(documents, 1):
        cached = pending.pop(str(document["url"]), None)
        url = _public_url(str(document["url"]), resolve=cached is None)
        doc_date = str(document["doc_date"])
        date.fromisoformat(doc_date)
        source_id = f"D{index:03d}"
        try:
            payload, content_type, accessed = cached if cached is not None else download_public_document(url)
        except DocumentTooLargeError as exc:
            if index not in omittable:
                raise
            annual_coverage["omissions"].append({
                "title": str(document.get("title") or url), "url": url,
                "doc_date": doc_date, "reason": str(exc),
            })
            annual_coverage["retrieved"] -= 1
            continue
        text = _document_text(payload, content_type, document.get("transform"))
        raw_file, text_file = f"{source_id}.bytes", f"{source_id}.txt"
        (output_dir / raw_file).write_bytes(payload)
        (output_dir / text_file).write_text(text, encoding="utf-8")
        records.append({
            "id": source_id, "type": str(document.get("type") or "RetrievedDocument"),
            "title": str(document.get("title") or url), "url": url,
            "doc_date": doc_date, "date_basis": str(document.get("date_basis") or "supplied_manifest"),
            "accessed": accessed,
            "content_type": content_type, "content_sha256": sha256_digest(payload),
            "text_sha256": sha256_digest(text.encode("utf-8")),
            "raw_file": raw_file, "text_file": text_file, "verification": "retrieved",
            **({"transform": document["transform"]} if document.get("transform") else {}),
        })
    attachment_records = []
    for spec in attachments:
        source_path = Path(spec["path"]).resolve()
        filename = str(spec.get("name") or Path(source_path).name)
        target = output_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        attachment_records.append({
            "file": filename,
            "sha256": sha256_digest(target.read_bytes()),
            "kind": str(spec.get("kind") or "attachment"),
            **({"metadata": spec["metadata"]} if spec.get("metadata") else {}),
        })
    pack = {"schema_version": "1", "ticker": ticker, "documents": records}
    if attachment_records:
        pack["attachments"] = attachment_records
    if intake_coverage is not None:
        pack["intake_coverage"] = intake_coverage
    path = output_dir / "evidence.json"
    path.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    return path


def _context_excerpt(text: str, budget: int) -> str:
    """Prefer real statement tables over long filing introductions/appendices."""
    if len(text) <= budget:
        return text
    financial = [
        r"consolidated\s+statements?\s+of\s+(?:operations|income|earnings)",
        r"consolidated\s+balance\s+sheets?",
        r"consolidated\s+statements?\s+of\s+cash\s+flows?",
    ]
    supplementary = [
        r"(?:shares?\s+of\s+common\s+stock\s+outstanding|shares?\s+outstanding|number\s+of\s+shares)",
        r"(?:CEO\s+pay\s+ratio|pay\s+ratio|compensation\s+peer\s+group)",
        r"(?:independent\s+registered\s+public\s+accounting|auditor\s+tenure)",
    ]
    numeric = re.compile(r"\b\d[\d,]{2,}")
    #: A window narrow enough not to bleed into the *next* statement, which made
    #: a prose reference look as dense as the table it referred to.
    window = 2500

    def _looks_like_statement_heading(offset: int) -> bool:
        """True when the match starts a statement rather than sitting mid-sentence.

        A note that says "…as the cash flows of the related items" contains the
        same words as the statement heading and plenty of nearby numbers. What it
        does not have is the heading's position and shape: a statement title
        begins a line and is followed by its period and column captions.
        """
        line_start = text.rfind("\n", 0, offset) + 1
        if text[line_start:offset].strip():
            return False
        following = text[offset:offset + 80]
        if not re.match(r"[A-Z]", following):
            return False
        # A heading is followed by a period caption, a year, or a table label.
        tail = following.split("\n", 1)[1] if "\n" in following else ""
        return bool(
            re.match(r"\s*(?:year|years|period|quarter|three|nine|six|as of|december|january|february|march|april|may|june|july|august|september|october|november)", tail, re.I)
        )

    selected = []
    for pattern in financial:
        best = None
        for match in re.finditer(pattern, text, re.I):
            start = match.start()
            density = len(numeric.findall(text[start:start + window]))
            if density == 0:
                continue
            heading = _looks_like_statement_heading(start)
            score = (1 if heading else 0, density)
            if best is None or score > best[0]:
                best = (score, start, density)
        if best is not None:
            selected.append((best[1], best[2]))
    if selected:
        head = budget // 10
        financial_budget = budget * 8 // 10
        # Order by position and weight each statement by its numeric density, not
        # equally. An equal split gives every statement the same character
        # allowance, but a cash-flow statement carries several times the numbers
        # of a balance sheet and is correspondingly longer. Equal shares truncated
        # the cash flow mid-table — the operating section survived and investing
        # and financing activities did not — which is what made a real statement
        # look as though it had not been retrieved.
        selected.sort()
        densities = [density + 1 for _start, density in selected]
        total_density = sum(densities)
        pieces = [text[:head]]
        for (start, _density), weight in zip(selected, densities):
            width = max(1, financial_budget * weight // total_density)
            pieces.append(f"[STATEMENT EXCERPT AT TEXT OFFSET {start}]\n" + text[start:start + width])
        for pattern in supplementary[:1]:
            match = re.search(pattern, text, re.I)
            if match:
                start = max(0, match.start() - 150)
                pieces.append(f"[SHARE EXCERPT AT TEXT OFFSET {start}]\n" + text[start:start + budget // 15])
        return "\n[DOCUMENT EXCERPT: intervening text omitted]\n".join(pieces)
    # Proxy documents need pay/auditor/peer sections rather than just cover pages.
    positions = []
    for pattern in supplementary[1:]:
        matches = list(re.finditer(pattern, text, re.I))
        if matches:
            match = max(matches, key=lambda m: len(re.findall(r"\d", text[m.start():m.start() + 3000])))
            positions.append(match.start())
    if positions:
        width = budget * 8 // 10 // len(positions)
        pieces = [text[:budget // 10]] + [text[start:start + width] for start in positions]
        return "\n[DOCUMENT EXCERPT: intervening text omitted]\n".join(pieces)
    half = budget // 2
    return text[:half] + "\n[DOCUMENT EXCERPT TRUNCATED: middle omitted]\n" + text[-half:]


# Put the financial statements/forensic inputs ahead of incidental disclosures.
# Round-robin across concepts prevents a long history of one tag from consuming
# the entire context. This is a prompt projection, not a truncated fact ledger.
_PROMPT_CORE_CONCEPTS = (
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
    "Assets", "Liabilities", "StockholdersEquity", "NetIncomeLoss", "GrossProfit",
    "CostOfRevenue", "CostOfGoodsAndServicesSold", "OperatingIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities", "OperatingCashFlow",
    "AccountsReceivableNetCurrent", "AccountsReceivableNet", "AccountsReceivable",
    "AssetsCurrent", "LiabilitiesCurrent", "CashAndCashEquivalentsAtCarryingValue",
    "PropertyPlantAndEquipmentNet", "DepreciationDepletionAndAmortization",
    "SellingGeneralAndAdministrativeExpense", "LongTermDebtCurrent", "LongTermDebtNoncurrent",
    "PaymentsToAcquirePropertyPlantAndEquipment", "CapitalExpenditures",
    "EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding", "EarningsPerShareDiluted",
)


def _select_prompt_facts(facts, limit: int = 100):
    """Deterministic current-reported selection, preserving concept/period breadth."""
    superseded = {fact.supersedes for fact in facts if fact.supersedes}
    # Null-valued facts stay in the ledger but never occupy a prompt slot.
    active = [fact for fact in facts if fact.id not in superseded and fact.value is not None]
    buckets = {}
    for fact in active:
        key = (fact.concept, fact.unit, str(fact.accounting_basis))
        buckets.setdefault(key, []).append(fact)

    def recency(fact):
        annual = False
        if fact.period_start and fact.period_end:
            try:
                annual = 330 <= (date.fromisoformat(fact.period_end) - date.fromisoformat(fact.period_start)).days <= 400
            except ValueError:
                pass  # Invalid fact dates are rejected on intake/binding, never repaired here.
        return annual, fact.instant or fact.period_end or "", fact.availability_time, fact.id

    priority = {concept: index for index, concept in enumerate(_PROMPT_CORE_CONCEPTS)}
    keys = sorted(buckets, key=lambda key: (priority.get(key[0], len(priority)), key))
    for key, bucket in buckets.items():
        bucket.sort(key=recency, reverse=True)
        seen_periods = set()
        unique = []
        for fact in bucket:
            if fact.period_key() not in seen_periods:
                unique.append(fact)
                seen_periods.add(fact.period_key())
        buckets[key] = unique
    selected = []
    positions = dict.fromkeys(keys, 0)
    # Keep current/prior financial periods even in a many-hundred-concept pack.
    for _ in range(2):
        for key in keys:
            if key[0] in priority and positions[key] < len(buckets[key]) and len(selected) < limit:
                selected.append(buckets[key][positions[key]])
                positions[key] += 1
    while len(selected) < limit:
        added = False
        for key in keys:
            if positions[key] < len(buckets[key]) and len(selected) < limit:
                selected.append(buckets[key][positions[key]])
                positions[key] += 1
                added = True
        if not added:
            break
    return selected, len(active)


class EvidencePack:
    def __init__(self, path: Path, *, ticker: str, analysis_date: str):
        self.path = Path(path).resolve()
        self.analysis_date = str(analysis_date)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.documents: List[Dict[str, Any]] = self.data.get("documents", [])
        if self.data.get("schema_version") != "1" or self.data.get("ticker") != ticker.upper():
            raise ValueError("Evidence pack schema/ticker does not match this run.")
        if not isinstance(self.documents, list) or not 1 <= len(self.documents) <= MAX_DOCUMENTS:
            raise ValueError("Evidence pack requires a bounded nonempty document list.")
        from src.facts import cutoff_instant

        # A date-only cutoff D admits evidence through the end of D (UTC);
        # documents carry calendar dates, so compare dates inclusively.
        as_of = cutoff_instant(analysis_date).date()
        seen = set()
        for document in self.documents:
            if document["id"] in seen:
                raise ValueError("Duplicate evidence source ID.")
            seen.add(document["id"])
            _public_url(document["url"])
            if document.get("transform") and date.fromisoformat(document["transform"]["analysis_date"]) > as_of:
                raise ValueError("Transformed evidence contains a future analysis cutoff.")
            if date.fromisoformat(document["doc_date"]) > as_of:
                raise ValueError("Evidence document is dated after the analysis date (look-ahead risk).")
            accessed = datetime.fromisoformat(document["accessed"].replace("Z", "+00:00"))
            if accessed.tzinfo is None or document.get("verification") != "retrieved":
                raise ValueError("Evidence requires retrieval metadata with a timezone-aware timestamp.")
            for key, hash_key in (("raw_file", "content_sha256"), ("text_file", "text_sha256")):
                file = self._file(document[key])
                if file.stat().st_size > MAX_DOCUMENT_BYTES or sha256_digest(file.read_bytes()) != document[hash_key]:
                    raise ValueError(f"Evidence content checksum mismatch for {document['id']}.")
            # The cached text must be reproducible from the actual downloaded bytes.
            derived = _document_text(self._file(document["raw_file"]).read_bytes(), document["content_type"], document.get("transform"))
            if sha256_digest(derived.encode("utf-8")) != document["text_sha256"]:
                raise ValueError("Evidence text does not match extraction from downloaded bytes.")
            transform = document.get("transform") or {}
            if transform.get("transform_version") == MARKET_TRANSFORM_V2:
                # The retrieval instant is the transform's collection instant,
                # and the document is dated by the bar it actually reflects.
                if transform["collection_at"] != document["accessed"] or document["doc_date"] != json.loads(derived)["last_bar_date"]:
                    raise ValueError("Market evidence dates do not match its frozen transform.")
        self._validate_intake_coverage(as_of)
        self.attachments = self.data.get("attachments", [])
        if not isinstance(self.attachments, list) or len(self.attachments) > MAX_ATTACHMENTS:
            raise ValueError("Evidence attachments require a bounded list.")
        attachment_files = set()
        document_files = {d[key] for d in self.documents for key in ("raw_file", "text_file")}
        for attachment in self.attachments:
            relative = attachment["file"]
            if relative in attachment_files or relative in document_files or relative == "evidence.json":
                raise ValueError("Evidence attachment paths must be unique and separate from documents.")
            attachment_files.add(relative)
            file = self._file(relative)
            if file.stat().st_size > MAX_DOCUMENT_BYTES or sha256_digest(file.read_bytes()) != attachment["sha256"]:
                raise ValueError("Evidence attachment checksum mismatch.")
        # Typed facts. Optional fact-bearing attachments are loaded with explicit,
        # typed error handling: a genuinely absent attachment is simply "no facts",
        # but a malformed or incompatible one raises, so corruption can never be
        # mistaken for absence.
        self.facts = self._load_embedded_facts()
        self.facts.extend(self._load_fact_attachments())
        self.checksum = sha256_digest(json.dumps(self.data, sort_keys=True, separators=(",", ":")).encode())

    def _validate_intake_coverage(self, as_of: date) -> None:
        """Check optional collector coverage metadata; absence means a legacy pack."""
        coverage = self.data.get("intake_coverage")
        if coverage is None:
            return
        if not isinstance(coverage, dict) or coverage.get("schema_version") != INTAKE_COVERAGE_VERSION:
            raise ValueError("Evidence intake coverage has an unsupported format.")
        if coverage.get("ticker") != self.data["ticker"]:
            raise ValueError("Evidence intake coverage ticker does not match the pack.")
        if date.fromisoformat(str(coverage.get("analysis_date"))) > as_of:
            raise ValueError("Evidence intake coverage has a future analysis cutoff.")
        annual = coverage.get("annual_filings")
        if annual is None:
            return
        counts = [annual.get(key) if isinstance(annual, dict) else None for key in ("requested", "found", "retrieved")]
        omissions = annual.get("omissions") if isinstance(annual, dict) else None
        if any(type(value) is not int or value < 0 for value in counts) or not isinstance(omissions, list):
            raise ValueError("Evidence intake coverage annual counts are malformed.")
        if counts[2] != counts[1] - len(omissions) or counts[1] > counts[0]:
            raise ValueError("Evidence intake coverage annual counts are inconsistent.")
        retrieved_urls = {document["url"] for document in self.documents}
        for omission in omissions:
            if not isinstance(omission, dict) or not all(
                isinstance(omission.get(key), str) and omission[key] for key in ("title", "url", "doc_date", "reason")
            ):
                raise ValueError("Evidence intake coverage omission records are malformed.")
            if date.fromisoformat(omission["doc_date"]) > as_of or omission["url"] in retrieved_urls:
                raise ValueError("Evidence intake coverage omission conflicts with the pack.")

    def market_snapshot(self) -> Optional[Dict[str, Any]]:
        """The v2 market history's frozen last bar and indicators, or ``None``.

        ``None`` for legacy packs (no versioned market transform), which keep
        their prior behavior. The text was re-derived from the sealed bytes
        and hash-checked when the pack loaded.
        """
        markets = [
            document for document in self.documents
            if (document.get("transform") or {}).get("transform_version") == MARKET_TRANSFORM_V2
        ]
        if not markets:
            return None
        if len(markets) > 1:
            raise ValueError("Evidence pack contains more than one versioned market history.")
        data = json.loads(self._file(markets[0]["text_file"]).read_text(encoding="utf-8"))
        return {
            "document_id": markets[0]["id"],
            **{key: data[key] for key in (
                "transform_version", "last_bar_date", "last_close", "TechnicalSet", "collection_at",
                "collection_date_us_eastern", "eligible_through", "collection_day_bar_excluded",
            )},
        }

    def coverage_summary(self) -> List[Dict[str, str]]:
        """Deterministic, factual intake gaps recorded by the collector.

        Empty for a complete intake and for legacy packs without coverage
        metadata. Used verbatim for the agent directive and the report.
        """
        coverage = self.data.get("intake_coverage")
        if not isinstance(coverage, dict):
            return []
        rows: List[Dict[str, str]] = []
        annual = coverage.get("annual_filings")
        if isinstance(annual, dict) and annual["retrieved"] < annual["requested"]:
            detail = (
                f"Requested {annual['requested']} annual filings; found {annual['found']}; "
                f"retrieved {annual['retrieved']}."
            )
            if annual["omissions"]:
                detail += (
                    f" {len(annual['omissions'])} older annual filing(s) were omitted by the 8 MiB "
                    "per-document limit. This is incomplete filing coverage, not a complete "
                    f"{annual['requested']}-year filing set."
                )
            rows.append({"field": "annual_filings", "detail": detail})
            for omission in annual["omissions"]:
                rows.append({
                    "field": "omitted_annual_filing",
                    "detail": f"{omission['title']} ({omission['url']}, filed {omission['doc_date']}): {omission['reason']}",
                })
        facts = coverage.get("company_facts")
        if isinstance(facts, dict) and not facts.get("collected"):
            rows.append({"field": "company_facts", "detail": str(facts.get("gap") or "no typed facts: SEC CompanyFacts unavailable")})
        return rows

    # --- typed attachment loading -------------------------------------------

    def _load_embedded_facts(self) -> List[Any]:
        """Load facts embedded directly in ``evidence.json`` under ``facts``."""
        from src.facts import Fact, validate_fact

        embedded = self.data.get("facts")
        if embedded is None:
            return []
        if not isinstance(embedded, list):
            raise ValueError(
                "Evidence pack 'facts' must be a list of typed facts; got "
                f"{type(embedded).__name__}."
            )
        loaded: List[Any] = []
        for index, item in enumerate(embedded):
            if isinstance(item, Fact):
                loaded.append(item)
            elif isinstance(item, dict):
                try:
                    loaded.append(Fact.from_dict(item))
                except (TypeError, ValueError, KeyError) as exc:
                    raise ValueError(
                        f"Embedded fact at index {index} is malformed and cannot be "
                        f"loaded as a typed fact: {exc}"
                    ) from exc
            else:
                raise ValueError(
                    f"Embedded fact at index {index} must be a Fact or dict, got "
                    f"{type(item).__name__}."
                )
            # Same bar as fact-ledger attachments: loadable is not valid.
            problems = validate_fact(loaded[-1]).errors
            if problems:
                raise ValueError(
                    f"Embedded fact at index {index} "
                    f"({getattr(loaded[-1], 'id', '<unknown>')!r}) is invalid: " + "; ".join(problems)
                )
        return loaded

    def _load_fact_attachments(self) -> List[Any]:
        """
        Load typed facts from fact-bearing attachments.

        Routing is by attachment *kind*:

        - ``sec_companyfacts`` -> the raw SEC CompanyFacts parser.
        - ``fact_ledger`` / ``financial_facts`` (or a ``.facts.json`` file) ->
          the typed fact-ledger loader.

        A malformed or incompatible attachment raises :class:`ValueError`. It is
        never silently skipped, because "the ledger is corrupt" and "there are
        no facts" lead to opposite conclusions and must never look alike.
        """
        from src.facts import Fact, load_fact_ledger, validate_fact
        from src.sec_facts import parse_sec_company_facts

        collected: List[Any] = []
        for attachment in self.attachments:
            kind = attachment.get("kind")
            relative = attachment["file"]
            is_sec_kind = kind == "sec_companyfacts"
            is_ledger_kind = kind in ("fact_ledger", "financial_facts") or relative.endswith(
                ".facts.json"
            )
            if not (is_sec_kind or is_ledger_kind):
                continue

            fact_file = self._file(relative)
            try:
                payload = json.loads(fact_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f"Fact attachment '{relative}' is not valid JSON and cannot be "
                    f"loaded: {exc}"
                ) from exc

            if is_sec_kind:
                # Raw SEC CompanyFacts JSON must go through the SEC parser, not
                # the typed-ledger loader. Validate it looks like CompanyFacts.
                from src.facts import _is_sec_companyfacts_payload

                if not _is_sec_companyfacts_payload(payload):
                    raise ValueError(
                        f"Attachment '{relative}' is declared kind 'sec_companyfacts' "
                        "but its payload is not a SEC CompanyFacts document "
                        "(expected top-level 'facts' plus 'cik' or 'entityName')."
                    )
                try:
                    collected.extend(
                        parse_sec_company_facts(payload, ticker=self.data.get("ticker"))
                    )
                except (TypeError, ValueError, KeyError) as exc:
                    raise ValueError(
                        f"SEC CompanyFacts attachment '{relative}' could not be parsed "
                        f"into typed facts: {exc}"
                    ) from exc
                continue

            # Typed fact-ledger path.
            try:
                loaded = load_fact_ledger(fact_file)
            except (ValueError, TypeError, FileNotFoundError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Fact ledger attachment '{relative}' could not be loaded as typed "
                    f"facts: {exc}"
                ) from exc

            # A ledger that loads but whose entries do not validate is malformed,
            # not empty: reject it rather than trusting unusable facts.
            for fact in loaded:
                problems = validate_fact(fact).errors
                if problems:
                    raise ValueError(
                        f"Fact ledger attachment '{relative}' contains an invalid fact "
                        f"{getattr(fact, 'id', '<unknown>')!r}: " + "; ".join(problems)
                    )
            collected.extend(loaded)
        return collected

    def get_facts(self, as_of: Optional[Union[str, date, datetime]] = None, allow_forecasts: bool = False):
        from src.facts import filter_facts_as_of
        cutoff = as_of or self.analysis_date
        return filter_facts_as_of(self.facts, cutoff, allow_forecasts=allow_forecasts)

    def _file(self, relative: str) -> Path:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Evidence cache paths must be contained relative paths.")
        path = (self.path.parent / relative).resolve()
        if not path.is_relative_to(self.path.parent) or not path.is_file():
            raise ValueError("Evidence cache path escapes its pack or is missing.")
        return path

    def snapshot(self, output_dir: Path) -> "EvidencePack":
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=False)
        for document in self.documents:
            for key in ("raw_file", "text_file"):
                source = self._file(document[key])
                target = output_dir / document[key]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        for attachment in self.attachments:
            source = self._file(attachment["file"])
            target = output_dir / attachment["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        path = output_dir / "evidence.json"
        path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        # Copying evidence must preserve the research cutoff. Using the newest
        # document date here can hide later-but-still-eligible typed facts and
        # makes a resumed run see a different prompt from the initial run.
        return EvidencePack(path, ticker=self.data["ticker"], analysis_date=self.analysis_date)

    def sources(self) -> List[Dict[str, Any]]:
        return [{key: value for key, value in document.items() if key not in ("raw_file", "text_file")} for document in self.documents]

    def prompt_text(self, budget: int = 80000) -> str:
        chunks = []
        financial_count = sum(d.get("type") != "WebNews" for d in self.documents)
        per_document = budget // max(1, financial_count)
        # Added news must not crowd out previously supplied statement/share/price
        # excerpts. Its separate bounded allowance is at most 3,000 chars/page.
        news_budget = min(3000, budget // len(self.documents))
        for document in self.documents:
            text = self._file(document["text_file"]).read_text(encoding="utf-8")
            text = _context_excerpt(text, news_budget if document.get("type") == "WebNews" else per_document)
            metadata = {key: document[key] for key in ("id", "title", "url", "doc_date", "accessed", "content_sha256")}
            chunks.append(html.escape(json.dumps(metadata) + "\n" + text, quote=False))
        for attachment in self.attachments:
            if attachment.get("kind") == "gemini_web_research":
                trace = json.loads(self._file(attachment["file"]).read_text(encoding="utf-8"))
                # Diagnostic coverage only: never feed the generated research prose
                # or Search suggestions HTML as authoritative source content.
                coverage = {"web_research_accepted_pages": len(trace["accepted"]), "limitations": trace["limitations"]}
                chunks.append(html.escape(json.dumps(coverage), quote=False))
        if self.facts:
            usable = self.get_facts(as_of=self.analysis_date, allow_forecasts=False)
            if usable:
                selected, active_count = _select_prompt_facts(usable)
                fact_payload = [
                    {
                        "id": f.id,
                        "concept": f.concept,
                        "value": f.value,
                        "unit": f.unit,
                        "period": f.period_key(),
                        "accession": f.filing_accession,
                        "available": f.availability_time,
                        "basis": str(f.accounting_basis.value if hasattr(f.accounting_basis, "value") else f.accounting_basis),
                        **({"supersedes": f.supersedes} if f.supersedes else {}),
                    }
                    for f in selected
                ]
                fact_block = (
                    f"### Date-Bounded Typed Fact Ledger (Authoritative Reported Facts as of {self.analysis_date})\n"
                    f"Facts count: {active_count} current eligible records (showing {len(fact_payload)}).\n"
                    "Note: Superseded records and forecasts/guidance are excluded. Core financial concepts "
                    "and recent annual periods are prioritized; the complete ledger remains available for binding.\n\n"
                    + json.dumps(fact_payload, indent=2)
                )
                chunks.append(html.escape(fact_block, quote=False))
        return "\n\n".join(chunks)
