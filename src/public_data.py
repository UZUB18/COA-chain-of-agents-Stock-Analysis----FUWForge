"""Automatic date-bounded SEC filing discovery plus public daily price history.

Requires a real SEC contact User-Agent; no Gemini credentials are sent to sources.
Public prices are provider-adjusted history, not a certified point-in-time archive.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.evidence import MAX_DOCUMENTS, _document_text, download_public_document, fetch_evidence, sec_user_agent
from src.utils.checksum import sha256_digest


class _Discovery:
    def __init__(self, root: Path):
        self.root = root
        self.records = []
        self.last_path: Path | None = None

    def get(self, url: str):
        payload, content_type, accessed = download_public_document(url)
        name = f"lookup_{len(self.records) + 1:02d}.json"
        target = self.root / name
        target.write_bytes(payload)
        self.records.append(
            {
                "url": url,
                "accessed": accessed,
                "file": name,
                "content_sha256": sha256_digest(payload),
            }
        )
        self.last_path = target
        return json.loads(payload)


SUPPORTED_ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
SUPPORTED_QUARTER_FORMS = ("10-Q", "10-Q/A")
SUPPORTED_PROXY_FORMS = ("DEF 14A", "DEFA14A")
SUPPORTED_MATERIAL_FORMS = ("8-K", "8-K/A")
ALL_SUPPORTED_FORMS = (
    SUPPORTED_ANNUAL_FORMS
    + SUPPORTED_QUARTER_FORMS
    + SUPPORTED_PROXY_FORMS
    + SUPPORTED_MATERIAL_FORMS
)


def _filing_rows(columns: Dict[str, Any], as_of: date) -> List[Dict[str, str]]:
    rows = []
    forms = columns.get("form") or []
    report_dates = columns.get("reportDate") or []
    for index, form in enumerate(forms):
        if form not in ALL_SUPPORTED_FORMS:
            continue
        row = {key: str((columns.get(key) or [])[index]) for key in ("form", "filingDate", "accessionNumber", "primaryDocument")}
        # ``reportDate`` is the period the filing covers. It is the only field
        # that identifies which fiscal year a document belongs to; two filings
        # of the same form in one year otherwise look like two different years.
        row["reportDate"] = str(report_dates[index]) if index < len(report_dates) and report_dates[index] else ""
        if date.fromisoformat(row["filingDate"]) <= as_of:
            rows.append(row)
    return rows


def _fiscal_anchor(row: Dict[str, str]) -> str:
    """The reporting period a filing belongs to, used to group by fiscal year."""
    return str(row.get("reportDate") or row.get("filingDate") or "")


def _select_filings(rows, years):
    """Choose the filings a run will read, one per fiscal period.

    Two selection defects are fixed here, both of which let a document that
    should not compete consume a slot:

    1. **Same-day filing order decided which proxy won.** A ``DEFA14A``
       (additional soliciting materials) and the ``DEF 14A`` (the definitive
       proxy statement) are routinely filed the same day. Sorting only by date
       left the winner to whatever order the SEC listing happened to use, so the
       run could read the supplementary filing instead of the statement. The
       definitive form now wins explicitly.
    2. **An amendment consumed a year.** ``10-K`` and ``10-K/A`` for the same
       fiscal year are two documents describing one year. Taking the first N
       annual documents therefore covered fewer than N fiscal years — three
       annual filings for two years — which silently narrowed the comparison
       window the depth setting asked for.

    Grouping is by reporting period, not by form or date. Within a group the
    amendment is preferred over the original (it is the corrected statement),
    and otherwise the most recent filing wins.
    """
    unique = {row["accessionNumber"]: row for row in rows}
    rows = sorted(unique.values(), key=lambda row: row["filingDate"], reverse=True)

    def _per_fiscal_period(candidates, *, prefer_amendment: bool):
        best: Dict[str, Dict[str, str]] = {}
        for row in candidates:
            anchor = _fiscal_anchor(row)
            current = best.get(anchor)
            if current is None:
                best[anchor] = row
                continue
            if prefer_amendment:
                row_is_amendment = row["form"].endswith("/A")
                current_is_amendment = current["form"].endswith("/A")
                if row_is_amendment != current_is_amendment:
                    best[anchor] = row if row_is_amendment else current
                    continue
            if row["filingDate"] > current["filingDate"]:
                best[anchor] = row
        return sorted(best.values(), key=lambda row: row["filingDate"], reverse=True)

    annuals = _per_fiscal_period(
        [row for row in rows if row["form"] in SUPPORTED_ANNUAL_FORMS],
        prefer_amendment=True,
    )[:years]
    quarters = _per_fiscal_period(
        [row for row in rows if row["form"] in SUPPORTED_QUARTER_FORMS],
        prefer_amendment=True,
    )[:1]
    # Rank the proxy forms before collapsing, so a DEFA14A can never displace the
    # definitive statement filed the same day. DEFA14A is still eligible when it
    # is the only proxy document for a period. ``SUPPORTED_PROXY_FORMS`` lists the
    # definitive form first, so the sort key is the plain rank (ascending), with
    # the filing date as a tiebreaker.
    proxy_rank = {form: index for index, form in enumerate(SUPPORTED_PROXY_FORMS)}
    proxies = _per_fiscal_period(
        sorted(
            (row for row in rows if row["form"] in SUPPORTED_PROXY_FORMS),
            key=lambda row: (proxy_rank[row["form"]], row["filingDate"], row["accessionNumber"]),
        ),
        prefer_amendment=False,
    )[:1]
    material_events = [row for row in rows if row["form"] in SUPPORTED_MATERIAL_FORMS][:2]
    return annuals, quarters, proxies, material_events


def _price_document(ticker: str, as_of: date):
    symbol = ticker.replace(".", "-")
    start = as_of - timedelta(days=550)
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    yahoo = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?period1={period1}&period2={period2}&interval=1d"
    stooq = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(ticker.lower())}.us&i=d&d1={start:%Y%m%d}&d2={as_of:%Y%m%d}"
    failures = []
    for url, kind in ((yahoo, "yahoo_daily"), (stooq, "stooq_daily")):
        transform = {"kind": kind, "ticker": ticker, "symbol": symbol, "analysis_date": as_of.isoformat()}
        try:
            payload, content_type, accessed = download_public_document(url)
            snapshot = json.loads(_document_text(payload, content_type, transform))
            return {
                "title": f"{ticker} daily price history through {as_of.isoformat()}", "type": "MarketHistory",
                "url": url, "doc_date": as_of.isoformat(), "date_basis": "requested_market_history_cutoff",
                "transform": transform,
            }, {"provider": kind, "last_bar_date": snapshot["last_bar_date"], "price_basis": snapshot["price_basis"], "probe_accessed": accessed}
        except Exception as exc:
            failures.append({"provider": kind, "error": str(exc)[:250]})
    raise ValueError("Public price providers did not return valid date-bounded history: " + json.dumps(failures))


def collect_public_sources(ticker: str, analysis_date: str, depth: str, output_dir: Path, cik: str | None = None) -> Path:
    """Discover actual filing URLs/dates, fetch their content, and add price history."""
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,14}", ticker):
        raise ValueError("Use a valid US-listed equity ticker.")
    as_of = date.fromisoformat(analysis_date)
    if as_of > datetime.now(timezone.utc).date():
        raise ValueError("Cannot collect research inputs for a future analysis date.")
    years = {"quick": 3, "standard": 5, "deep": 10}.get(depth.lower())
    if years is None:
        raise ValueError("Depth must be Quick, Standard or Deep.")
    sec_user_agent()  # Fail before paid analysis or network requests if contact is missing.
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    lookup_dir = output_dir / "discovery"
    lookup_dir.mkdir()
    discovery = _Discovery(lookup_dir)
    if cik is None:
        directory = discovery.get("https://www.sec.gov/files/company_tickers.json")
        match = next((entry for entry in directory.values() if str(entry.get("ticker", "")).upper().replace(".", "-") == ticker.replace(".", "-")), None)
        if match is None:
            raise ValueError("Ticker was not found in the current SEC identifier directory; specify --cik if appropriate.")
        cik = str(match["cik_str"])
    if not re.fullmatch(r"\d{1,10}", str(cik)):
        raise ValueError("CIK must contain 1–10 digits.")
    cik_number = str(int(cik))
    submissions = discovery.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    sec_tickers = [str(value).upper().replace(".", "-") for value in submissions.get("tickers", [])]
    if ticker.replace(".", "-") not in sec_tickers:
        raise ValueError("The SEC company record does not confirm this ticker/CIK pairing.")
    filings = submissions.get("filings") or {}
    rows = _filing_rows(filings.get("recent") or {}, as_of)
    annuals, quarters, proxies, material_events = _select_filings(rows, years)
    older = filings.get("files") or []
    requests = 0
    for file in older:
        if len(annuals) >= years and (quarters or any(row["form"] in ("20-F", "40-F") for row in annuals)) and proxies:
            break
        if date.fromisoformat(file["filingFrom"]) > as_of:
            continue
        name = str(file["name"])
        if not re.fullmatch(r"CIK\d+-submissions-\d+\.json", name):
            raise ValueError("Unexpected SEC submission-history file name.")
        requests += 1
        if requests > 12:
            break
        rows.extend(_filing_rows(discovery.get(f"https://data.sec.gov/submissions/{name}"), as_of))
        annuals, quarters, proxies, material_events = _select_filings(rows, years)
    if not annuals:
        raise ValueError("No date-appropriate annual filing was found for this ticker.")
    documents = []
    for row in annuals + quarters + proxies + material_events:
        accession = row["accessionNumber"]
        primary = row["primaryDocument"]
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(r"[A-Za-z0-9_.-]+", primary):
            raise ValueError("Unexpected SEC primary-document metadata.")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession.replace('-', '')}/{primary}"
        doc_type = "SECMaterialEvent" if row["form"] in SUPPORTED_MATERIAL_FORMS else "SECFiling"
        documents.append({"title": f"{ticker} {row['form']} filed {row['filingDate']}", "type": doc_type,
                          "url": url, "doc_date": row["filingDate"], "date_basis": "SEC_filing_date"})
    price_document, price_info = _price_document(ticker, as_of)
    documents.append(price_document)
    if len(documents) > MAX_DOCUMENTS:
        raise ValueError("Collected evidence exceeds the configured document limit.")
    # Fetch SEC CompanyFacts as a first-class typed-facts attachment. This is
    # optional: if it cannot be retrieved the run still proceeds, but the typed
    # facts are then explicitly unavailable rather than silently absent.
    company_facts = _collect_company_facts(ticker, cik_number, as_of, discovery)
    facts_info = company_facts["coverage"]
    attachments = company_facts.get("attachments") or []
    coverage = {
        "ticker": ticker, "cik": cik_number, "company": submissions.get("name"), "analysis_date": analysis_date,
        "annual_filings_requested": years, "annual_filings_found": len(annuals),
        "quarterly_filing_found": bool(quarters), "proxy_found": bool(proxies),
        "material_events_found": len(material_events), "market": price_info,
        "company_facts": facts_info,
        "discovery_records": discovery.records,
        "limitations": ["Identifier lookup uses the current SEC ticker/CIK mapping, not a historical ticker database.",
                        "SEC inclusion is based on filing date, not an intraday publication-time cutoff.",
                        "Market-provider corporate-action adjustment policies apply; this is not a certified point-in-time backtest dataset.",
                        "Culture, peer estimates, social media and broader news may still be unavailable; never fabricate them."],
    }
    (output_dir / "collection.json").write_text(json.dumps(coverage, indent=2) + "\n")
    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps({"ticker": ticker, "documents": documents}, indent=2) + "\n")
    return fetch_evidence(manifest, output_dir / "pack", attachments=attachments)


def _collect_company_facts(
    ticker: str,
    cik_number: str,
    as_of: date,
    discovery: "_Discovery",
) -> Dict[str, Any]:
    """
    Retrieve SEC CompanyFacts for the ticker and prepare it as a typed-facts
    attachment.

    This is a *best-effort enrichment*: if the SEC endpoint is unreachable or
    the payload is unusable, the run continues with typed facts explicitly
    unavailable (recorded in ``collection.json``), never silently missing. When
    it succeeds, the raw CompanyFacts bytes are attached with kind
    ``sec_companyfacts`` so ``EvidencePack`` routes them through the SEC parser.

    The raw SEC JSON is attached (not a pre-filtered subset) so the full
    provenance — every filing, period, unit and value the issuer reported — is
    preserved and independently re-parsable. Size is bounded by the SEC
    download guard; an oversized response is reported as unavailable rather than
    truncated into something that looks complete.
    """
    from src.sec_facts import parse_sec_company_facts

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik_number):010d}.json"
    coverage: Dict[str, Any] = {
        "collected": False,
        "url": url,
        "availability_policy": None,
        "fact_count": 0,
        "reason": None,
    }
    try:
        payload = discovery.get(url)
    except Exception as exc:
        coverage["reason"] = (
            f"SEC CompanyFacts could not be retrieved ({type(exc).__name__}: {exc}). "
            "Typed financial facts are unavailable for this run; they must not be "
            "guessed or substituted."
        )
        return {"coverage": coverage, "attachments": []}

    # Parse to compute coverage and confirm the payload is usable; the attachment
    # itself carries the raw bytes so provenance is never reduced.
    try:
        parsed = parse_sec_company_facts(payload, ticker=ticker)
    except Exception as exc:
        coverage["reason"] = (
            f"SEC CompanyFacts retrieved but could not be parsed ({type(exc).__name__}: {exc}). "
            "Typed financial facts are unavailable."
        )
        return {"coverage": coverage, "attachments": []}

    from src.sec_facts import AVAILABILITY_POLICY

    coverage.update(
        {
            "collected": True,
            "availability_policy": AVAILABILITY_POLICY,
            "fact_count": len(parsed),
            "reason": None,
        }
    )
    # The discovery cache already stored the exact bytes; attach that file.
    if discovery.last_path is None:
        coverage["collected"] = False
        coverage["reason"] = "SEC CompanyFacts bytes were not cached and cannot be attached."
        return {"coverage": coverage, "attachments": []}
    return {
        "coverage": coverage,
        "attachments": [
            {"path": discovery.last_path, "kind": "sec_companyfacts", "name": "sec_companyfacts.json"}
        ],
    }
