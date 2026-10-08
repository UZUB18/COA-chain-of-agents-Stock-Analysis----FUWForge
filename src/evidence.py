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
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List

from dotenv import dotenv_values

from src.utils.checksum import sha256_digest

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 16
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
_LAST_SEC_REQUEST = 0.0


def sec_user_agent() -> str:
    local = dotenv_values(_ENV_PATH, interpolate=False) if _ENV_PATH.is_file() else {}
    value = (os.environ.get("SEC_USER_AGENT") or local.get("SEC_USER_AGENT") or "").strip()
    if not value or "@" not in value or "\n" in value or "\r" in value:
        raise ValueError("Set SEC_USER_AGENT locally to your organization/contact email for SEC fair-access requests.")
    return value


def download_public_document(url: str):
    """Bounded public download. Never sends model API keys or SEC contact to other hosts."""
    global _LAST_SEC_REQUEST
    url = _public_url(url, resolve=True)
    host = urllib.parse.urlsplit(url).hostname
    is_sec = host in ("sec.gov", "www.sec.gov", "data.sec.gov")
    user_agent = sec_user_agent() if is_sec else "FUWForge/2.1 public-document-retrieval"
    if is_sec:
        time.sleep(max(0.0, 0.25 - (time.monotonic() - _LAST_SEC_REQUEST)))
        _LAST_SEC_REQUEST = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    opener = urllib.request.build_opener(_NoRedirects())
    with opener.open(req, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"Evidence request did not succeed: HTTP {response.status}.")
        if response.headers.get("Content-Encoding", "identity") not in ("", "identity"):
            raise ValueError("Compressed evidence responses are not supported.")
        payload = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise ValueError("Evidence document exceeds the 8 MiB size limit.")
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


def _transformed_text(payload: bytes, transform: Dict[str, Any]) -> str:
    """Curate date-bounded market history, excluding current/future quote metadata."""
    kind = transform.get("kind")
    as_of = date.fromisoformat(transform["analysis_date"])
    ticker = str(transform["ticker"]).upper()
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
            day = datetime.fromtimestamp(timestamp, timezone.utc).date()
            if day > as_of:
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
            if day > as_of:
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
    if (as_of - date.fromisoformat(history[-1]["Date"])).days > 7:
        raise ValueError("Public price history is stale relative to the requested analysis date.")
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
    return json.dumps({
        "dataset": "date-bounded daily market history", "ticker": ticker, "analysis_date": as_of.isoformat(),
        "price_basis": basis, "last_bar_date": history[-1]["Date"], "last_close": history[-1]["Close"],
        "TechnicalSet": technicals,
        "derivation": "SMA from retrieved closes; Wilder RSI(14) seeded in the retrieved history. Null means insufficient history, never a guessed value.",
        "history": history,
    }, indent=2)


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


def fetch_evidence(manifest: Path, output_dir: Path) -> Path:
    """Download only explicit manifest URLs, with bounded size and no redirects."""
    config = json.loads(Path(manifest).read_text(encoding="utf-8"))
    ticker = str(config.get("ticker") or "").strip().upper()
    documents = config.get("documents")
    if not ticker or not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise ValueError(f"Evidence manifest requires ticker and 1–{MAX_DOCUMENTS} documents.")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for index, document in enumerate(documents, 1):
        url = _public_url(str(document["url"]), resolve=True)
        doc_date = str(document["doc_date"])
        date.fromisoformat(doc_date)
        source_id = f"D{index:03d}"
        payload, content_type, accessed = download_public_document(url)
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
    path = output_dir / "evidence.json"
    path.write_text(json.dumps({"schema_version": "1", "ticker": ticker, "documents": records}, indent=2) + "\n")
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
    selected = []
    for pattern in financial:
        matches = list(re.finditer(pattern, text, re.I))
        if matches:
            # TOC occurrences have page numbers, not dense multi-year statement values.
            match = max(matches, key=lambda m: len(re.findall(r"\b\d[\d,]{2,}", text[m.start():m.start() + 5000])))
            selected.append(match.start())
    if selected:
        head = budget // 10
        financial_budget = budget * 8 // 10
        width = financial_budget // len(selected)
        pieces = [text[:head]]
        for start in selected:
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
        as_of = date.fromisoformat(analysis_date)
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
        self.attachments = self.data.get("attachments", [])
        if not isinstance(self.attachments, list) or len(self.attachments) > 16:
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
        # Load typed facts if available
        self.facts = []
        if "facts" in self.data and isinstance(self.data["facts"], list):
            from src.facts import Fact
            for f_item in self.data["facts"]:
                if isinstance(f_item, Fact):
                    self.facts.append(f_item)
                elif isinstance(f_item, dict):
                    self.facts.append(Fact.from_dict(f_item))
        for attachment in self.attachments:
            if attachment.get("kind") in ("fact_ledger", "sec_companyfacts", "financial_facts") or attachment.get("file", "").endswith(".facts.json"):
                from src.facts import load_fact_ledger
                fact_file = self._file(attachment["file"])
                try:
                    self.facts.extend(load_fact_ledger(fact_file))
                except Exception:
                    pass
        self.checksum = sha256_digest(json.dumps(self.data, sort_keys=True, separators=(",", ":")).encode())

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
        return EvidencePack(path, ticker=self.data["ticker"], analysis_date=max(d["doc_date"] for d in self.documents))

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
                    for f in usable[:100]
                ]
                fact_block = (
                    f"### Date-Bounded Typed Fact Ledger (Authoritative Reported Facts as of {self.analysis_date})\n"
                    f"Facts count: {len(usable)} (showing top {len(fact_payload)}).\n"
                    f"Note: Forward forecasts/guidance are excluded from reported facts.\n\n"
                    + json.dumps(fact_payload, indent=2)
                )
                chunks.append(html.escape(fact_block, quote=False))
        return "\n\n".join(chunks)
