"""Public data integration tests: synthetic provider responses, no network/keys."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import evidence, public_data
from src.evidence import EvidencePack, _document_text
from src.public_data import collect_public_sources


@pytest.fixture(autouse=True)
def isolate_contact(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(evidence, "_ENV_PATH", tmp_path / "absent.env")


def chart(ticker="TEST", days=250, future=True):
    first = date(2025, 1, 31) - timedelta(days=days - 1)
    dates = [first + timedelta(days=index) for index in range(days)]
    if future:
        dates.append(date(2025, 2, 1))
    closes = [100.0 + index for index in range(days)] + ([999999.0] if future else [])
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": ticker, "regularMarketPrice": 888888, "longName": "Future metadata must not enter historical prompts"},
        # Real Yahoo daily bars are stamped at the US session open (14:30 UTC in
        # EST), so the UTC (v1) and US/Eastern (v2) bar dates agree.
        "timestamp": [int(datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc).timestamp()) for day in dates],
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes, "volume": [100] * len(closes)}],
                       "adjclose": [{"adjclose": [555555] * len(closes)}]},
    }]}}


def test_price_transformation_excludes_future_and_current_metadata():
    text = _document_text(json.dumps(chart()).encode(), "application/json", {"kind": "yahoo_daily", "ticker": "TEST", "analysis_date": "2025-01-31"})
    data = json.loads(text)
    assert data["last_bar_date"] == "2025-01-31"
    assert data["last_close"] == 349.0
    assert data["TechnicalSet"]["SMA_50"] == 324.5
    assert data["TechnicalSet"]["SMA_200"] == 249.5
    assert data["TechnicalSet"]["RSI_14"] == 100.0
    assert "888888" not in text and "999999" not in text and "555555" not in text
    assert "Future metadata" not in text


def test_short_history_does_not_invent_indicators():
    data = json.loads(_document_text(json.dumps(chart(days=2)).encode(), "application/json", {"kind": "yahoo_daily", "ticker": "TEST", "analysis_date": "2025-01-31"}))
    assert data["TechnicalSet"] == {"SMA_50": None, "SMA_200": None, "RSI_14": None}


def test_invalid_price_data_and_ticker_are_rejected():
    with pytest.raises(ValueError, match="ticker mismatch"):
        _document_text(json.dumps(chart(ticker="OTHER")).encode(), "application/json", {"kind": "yahoo_daily", "ticker": "TEST", "analysis_date": "2025-01-31"})
    with pytest.raises(ValueError, match="OHLCV header"):
        _document_text(b"No data", "text/plain", {"kind": "stooq_daily", "ticker": "TEST", "analysis_date": "2025-01-31"})
    with pytest.raises(ValueError, match="Unknown evidence transformation"):
        _document_text(b"{}", "application/json", {"kind": "unknown", "ticker": "TEST", "analysis_date": "2025-01-31"})


def test_contact_loads_from_local_file_without_mutating_environment(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text('SEC_USER_AGENT="Test Research test@example.com"\n')
    monkeypatch.setattr(evidence, "_ENV_PATH", path)
    assert evidence.sec_user_agent() == "Test Research test@example.com"
    assert "SEC_USER_AGENT" not in evidence.os.environ
    monkeypatch.setenv("SEC_USER_AGENT", "Shell Research shell@example.com")
    assert evidence.sec_user_agent() == "Shell Research shell@example.com"


def test_missing_contact_stops_before_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing SEC contact must not make requests")
    monkeypatch.setattr(public_data, "download_public_document", forbidden)
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        collect_public_sources("TEST", "2025-01-31", "Quick", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_full_public_collection_is_date_bounded_and_hash_checked(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Research test@example.com")
    filings = [
        ("10-K", "2026-01-01", "0000000001-26-000001", "future.htm"),
        ("10-K", "2024-12-01", "0000000001-24-000001", "annual24.htm"),
        ("10-K", "2023-12-01", "0000000001-23-000001", "annual23.htm"),
        ("10-K", "2022-12-01", "0000000001-22-000001", "annual22.htm"),
        ("10-Q", "2025-01-01", "0000000001-25-000001", "quarter.htm"),
        ("DEF 14A", "2025-01-10", "0000000001-25-000002", "proxy.htm"),
    ]
    columns = {key: [row[index] for row in filings] for index, key in enumerate(("form", "filingDate", "accessionNumber", "primaryDocument"))}
    fetched = []

    def download(url):
        fetched.append(url)
        if url.endswith("company_tickers.json"):
            data = {"0": {"ticker": "TEST", "cik_str": 1}}
            payload, kind = json.dumps(data).encode(), "application/json"
        elif "companyfacts" in url:
            data = {
                "cik": 1,
                "entityName": "Test Co",
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {
                                        "start": "2023-01-01",
                                        "end": "2023-12-31",
                                        "val": 123,
                                        "accn": "0000000001-24-000001",
                                        "fy": 2023,
                                        "fp": "FY",
                                        "form": "10-K",
                                        "filed": "2024-12-01",
                                    }
                                ]
                            }
                        }
                    }
                },
            }
            payload, kind = json.dumps(data).encode(), "application/json"
        elif url.endswith("CIK0000000001.json"):
            data = {"name": "Test Co", "tickers": ["TEST"], "filings": {"recent": columns, "files": []}}
            payload, kind = json.dumps(data).encode(), "application/json"
        elif "finance.yahoo.com" in url:
            payload, kind = json.dumps(chart()).encode(), "application/json"
        else:
            assert "future.htm" not in url
            payload, kind = b"<p>Official statement Revenue 123 and shares 100.</p>", "text/html"
        return payload, kind, "2026-10-06T20:00:00Z"

    monkeypatch.setattr(public_data, "download_public_document", download)
    monkeypatch.setattr(evidence, "download_public_document", download)
    # Keep URL validation active but answer DNS offline.
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    path = collect_public_sources("TEST", "2025-01-31", "Quick", tmp_path / "collected")
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    assert len(pack.documents) == 6  # Three annuals, one quarter, one proxy, market history.
    assert all(document["doc_date"] <= "2025-01-31" for document in pack.documents)
    assert "999999" not in pack.prompt_text()
    assert "Official statement Revenue 123" in pack.prompt_text()
    snapshot = pack.snapshot(tmp_path / "run_evidence")
    assert snapshot.checksum == pack.checksum
    coverage = json.loads((path.parent.parent / "collection.json").read_text())
    assert coverage["annual_filings_found"] == 3 and coverage["proxy_found"]
    assert len(coverage["discovery_records"]) == 3
    assert not any("future.htm" in url for url in fetched)

    # SEC CompanyFacts is attached as a typed-facts attachment and routed
    # through the SEC parser, producing a provenance-complete fact.
    assert coverage["company_facts"]["collected"] is True
    assert coverage["company_facts"]["fact_count"] == 1
    assert any(a["kind"] == "sec_companyfacts" for a in pack.attachments)
    assert len(pack.facts) == 1
    fact = pack.facts[0]
    assert fact.extractor_version == "sec_companyfacts_v1"
    assert fact.value == 123.0
    assert fact.filing_accession == "0000000001-24-000001"
    # Conservative point-in-time availability: end of the filed day.
    assert fact.availability_time == "2024-12-01T23:59:59Z"


def test_companyfacts_unavailable_is_recorded_not_silently_missing(monkeypatch, tmp_path):
    """If SEC CompanyFacts cannot be retrieved, that is recorded explicitly."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test Research test@example.com")
    filings = [
        ("10-K", "2024-12-01", "0000000001-24-000001", "annual24.htm"),
        ("10-K", "2023-12-01", "0000000001-23-000001", "annual23.htm"),
        ("10-K", "2022-12-01", "0000000001-22-000001", "annual22.htm"),
    ]
    columns = {
        key: [row[index] for row in filings]
        for index, key in enumerate(("form", "filingDate", "accessionNumber", "primaryDocument"))
    }

    def download(url):
        if url.endswith("company_tickers.json"):
            payload, kind = json.dumps({"0": {"ticker": "TEST", "cik_str": 1}}).encode(), "application/json"
        elif "companyfacts" in url:
            raise OSError("simulated SEC outage")
        elif url.endswith("CIK0000000001.json"):
            payload, kind = json.dumps(
                {"name": "Test Co", "tickers": ["TEST"], "filings": {"recent": columns, "files": []}}
            ).encode(), "application/json"
        elif "finance.yahoo.com" in url:
            payload, kind = json.dumps(chart()).encode(), "application/json"
        else:
            payload, kind = b"<p>Statement</p>", "text/html"
        return payload, kind, "2026-10-06T20:00:00Z"

    monkeypatch.setattr(public_data, "download_public_document", download)
    monkeypatch.setattr(evidence, "download_public_document", download)
    # Keep URL validation active but answer DNS offline.
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    path = collect_public_sources("TEST", "2025-01-31", "Quick", tmp_path / "collected")
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    assert pack.facts == []
    coverage = json.loads((path.parent.parent / "collection.json").read_text())
    assert coverage["company_facts"]["collected"] is False
    assert "unavailable" in coverage["company_facts"]["reason"].lower()


def test_filing_context_preserves_middle_statement_tables():
    text = "Introduction " * 2000
    for heading in ("CONSOLIDATED STATEMENTS OF OPERATIONS", "CONSOLIDATED BALANCE SHEETS", "CONSOLIDATED STATEMENTS OF CASH FLOWS"):
        text += heading + "\n" + ("Revenue 123456 Cash 234567 Equity 345678\n" * 20) + ("Narrative " * 2000)
    excerpt = evidence._context_excerpt(text, 6000)
    assert "CONSOLIDATED STATEMENTS OF OPERATIONS" in excerpt
    assert "CONSOLIDATED BALANCE SHEETS" in excerpt
    assert "CONSOLIDATED STATEMENTS OF CASH FLOWS" in excerpt
    assert "123456" in excerpt and "intervening text omitted" in excerpt
    assert len(excerpt) < 6500


def test_ticker_cik_mismatch_is_not_silently_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Research test@example.com")
    monkeypatch.setattr(public_data, "download_public_document", lambda url: (
        json.dumps({"name": "Other", "tickers": ["OTHER"], "filings": {}}).encode(), "application/json", "2026-10-06T20:00:00Z"
    ))
    with pytest.raises(ValueError, match="does not confirm"):
        collect_public_sources("TEST", "2025-01-31", "Quick", tmp_path / "wrong", cik="1")


# --- intake coverage and oversized historical annuals (Task 4.4 / MELI Task 1) ---

_COMPANYFACTS = {
    "cik": 1, "entityName": "Test Co",
    "facts": {"us-gaap": {"Revenues": {"units": {"USD": [{
        "start": "2023-01-01", "end": "2023-12-31", "val": 123, "accn": "0000000001-24-000001",
        "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-12-01",
    }]}}}},
}


def _collect(monkeypatch, tmp_path, *, oversized=(), failing=(), companyfacts="ok"):
    """Run the automatic collector against synthetic SEC/price responses."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test Research test@example.com")
    filings = [
        ("10-K", "2024-12-01", "0000000001-24-000001", "annual24.htm"),
        ("10-K", "2023-12-01", "0000000001-23-000001", "annual23.htm"),
        ("10-K", "2022-12-01", "0000000001-22-000001", "annual22.htm"),
        ("10-Q", "2025-01-01", "0000000001-25-000001", "quarter.htm"),
        ("DEF 14A", "2025-01-10", "0000000001-25-000002", "proxy.htm"),
    ]
    columns = {key: [row[index] for row in filings] for index, key in enumerate(("form", "filingDate", "accessionNumber", "primaryDocument"))}
    fetched = []

    def download(url):
        fetched.append(url)
        if any(url.endswith(name) for name in oversized):
            raise evidence.DocumentTooLargeError()
        if any(url.endswith(name) for name in failing):
            raise ValueError("Evidence request did not succeed: HTTP 500.")
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"ticker": "TEST", "cik_str": 1}}).encode(), "application/json", "2026-10-06T20:00:00Z"
        if "companyfacts" in url:
            if companyfacts == "oversized":
                raise evidence.DocumentTooLargeError()
            return json.dumps(_COMPANYFACTS).encode(), "application/json", "2026-10-06T20:00:00Z"
        if url.endswith("CIK0000000001.json"):
            data = {"name": "Test Co", "tickers": ["TEST"], "filings": {"recent": columns, "files": []}}
            return json.dumps(data).encode(), "application/json", "2026-10-06T20:00:00Z"
        if "finance.yahoo.com" in url:
            return json.dumps(chart()).encode(), "application/json", "2026-10-06T20:00:00Z"
        return b"<p>Official statement Revenue 123.</p>", "text/html", "2026-10-06T20:00:00Z"

    monkeypatch.setattr(public_data, "download_public_document", download)
    monkeypatch.setattr(evidence, "download_public_document", download)
    # Keep URL validation active but answer DNS offline.
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    path = collect_public_sources("TEST", "2025-01-31", "Quick", tmp_path / "collected")
    return path, fetched


def test_document_too_large_error_is_a_value_error():
    error = evidence.DocumentTooLargeError()
    assert isinstance(error, ValueError)
    assert str(error) == "Evidence document exceeds the 8 MiB size limit."


def test_oversized_older_annual_is_omitted_with_checksummed_coverage(monkeypatch, tmp_path):
    path, fetched = _collect(monkeypatch, tmp_path, oversized=("annual22.htm",))
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    assert not any(d["url"].endswith("annual22.htm") for d in pack.documents)
    assert len(pack.documents) == 5  # Two annuals, quarter, proxy, market history.
    # Each document is downloaded once; the omission does not trigger a refetch.
    assert sum(url.endswith("annual22.htm") for url in fetched) == 1
    assert sum(url.endswith("annual24.htm") for url in fetched) == 1
    coverage = pack.data["intake_coverage"]
    assert coverage["schema_version"] == "intake_coverage_v1"
    assert coverage["ticker"] == "TEST" and coverage["analysis_date"] == "2025-01-31"
    annual = coverage["annual_filings"]
    assert (annual["requested"], annual["found"], annual["retrieved"]) == (3, 3, 2)
    assert annual["omissions"] == [{
        "title": "TEST 10-K filed 2022-12-01",
        "url": "https://www.sec.gov/Archives/edgar/data/1/000000000122000001/annual22.htm",
        "doc_date": "2022-12-01",
        "reason": "Evidence document exceeds the 8 MiB size limit.",
    }]
    assert len(coverage["discovery"]) == 3
    # collection.json carries the same coverage and the retrieved count.
    collection = json.loads((path.parent.parent / "collection.json").read_text())
    assert collection["intake_coverage"] == coverage
    assert collection["annual_filings_found"] == 3
    assert collection["annual_filings_retrieved"] == 2
    # The gap is stated factually, never as a complete filing set.
    summary = pack.coverage_summary()
    text = json.dumps(summary)
    assert "Requested 3 annual filings; found 3; retrieved 2" in text
    assert "incomplete filing coverage" in text
    assert "TEST 10-K filed 2022-12-01" in text
    # The coverage survives a snapshot and reload unchanged.
    snapshot = pack.snapshot(tmp_path / "snap")
    assert snapshot.checksum == pack.checksum
    assert snapshot.data["intake_coverage"] == coverage
    assert snapshot.coverage_summary() == summary


def test_changing_an_omission_record_changes_the_pack_checksum(monkeypatch, tmp_path):
    path, _ = _collect(monkeypatch, tmp_path, oversized=("annual22.htm",))
    original = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31").checksum
    data = json.loads(path.read_text())
    data["intake_coverage"]["annual_filings"]["omissions"][0]["reason"] = "edited"
    path.write_text(json.dumps(data))
    assert EvidencePack(path, ticker="TEST", analysis_date="2025-01-31").checksum != original


@pytest.mark.parametrize("name", ["annual24.htm", "quarter.htm", "proxy.htm"])
def test_oversized_mandatory_document_still_fails(monkeypatch, tmp_path, name):
    with pytest.raises(evidence.DocumentTooLargeError):
        _collect(monkeypatch, tmp_path, oversized=(name,))
    assert not (tmp_path / "collected" / "pack" / "evidence.json").exists()


def test_other_older_annual_failure_is_not_an_omission(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="HTTP 500"):
        _collect(monkeypatch, tmp_path, failing=("annual22.htm",))
    assert not (tmp_path / "collected" / "pack" / "evidence.json").exists()


def test_explicit_fetch_sources_stays_fail_closed_for_oversized_documents(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"ticker": "TEST", "documents": [
        {"url": "https://www.sec.gov/a.htm", "doc_date": "2024-01-01"},
        {"url": "https://www.sec.gov/b.htm", "doc_date": "2023-01-01"},
    ]}))
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])

    def download(url):
        if url.endswith("b.htm"):
            raise evidence.DocumentTooLargeError()
        return b"text", "text/plain", "2026-10-06T20:00:00Z"

    monkeypatch.setattr(evidence, "download_public_document", download)
    with pytest.raises(evidence.DocumentTooLargeError):
        evidence.fetch_evidence(manifest, tmp_path / "pack")


def test_complete_collection_persists_seal_intake_coverage(monkeypatch, tmp_path):
    from src.sec_facts import parse_sec_company_facts_report

    path, _ = _collect(monkeypatch, tmp_path)
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    expected = parse_sec_company_facts_report(_COMPANYFACTS, ticker="TEST").coverage.to_dict()
    collection = json.loads((path.parent.parent / "collection.json").read_text())
    assert collection["company_facts"]["intake"] == expected
    assert pack.data["intake_coverage"]["company_facts"]["intake"] == expected
    attachment = next(a for a in pack.attachments if a["kind"] == "sec_companyfacts")
    assert attachment["metadata"]["seal_intake_coverage"] == expected
    reloaded = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    assert reloaded.data["intake_coverage"]["company_facts"]["intake"] == expected
    assert len(reloaded.facts) == 1
    annual = pack.data["intake_coverage"]["annual_filings"]
    assert (annual["requested"], annual["found"], annual["retrieved"], annual["omissions"]) == (3, 3, 3, [])
    # A complete intake has no coverage gap to warn about.
    assert pack.coverage_summary() == []


def test_oversized_companyfacts_is_an_explicit_no_facts_gap(monkeypatch, tmp_path):
    path, _ = _collect(monkeypatch, tmp_path, companyfacts="oversized")
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    assert pack.facts == []
    assert not any(a["kind"] == "sec_companyfacts" for a in pack.attachments)
    gap = "no typed facts: CompanyFacts exceeded the 8 MiB limit"
    facts_cov = pack.data["intake_coverage"]["company_facts"]
    assert facts_cov["collected"] is False and facts_cov["gap"] == gap
    collection = json.loads((path.parent.parent / "collection.json").read_text())
    assert collection["company_facts"]["gap"] == gap
    assert gap in json.dumps(pack.coverage_summary())


# --- one price download and versioned market replay (Task 4.9 / MELI Task 2) ---

V1_YAHOO_SHA256 = "sha256:948d775343569ecad6a675a687864199303c75a19be15ed33621e171d0ee78e5"
V1_STOOQ_SHA256 = "sha256:555d8cb4058a91c58155483da9da9cce60e2d6dc503e6f5f178e580decaf0fdc"
_V1_TRANSFORM = {"kind": "yahoo_daily", "ticker": "TEST", "symbol": "TEST", "analysis_date": "2026-10-28"}


def _weekdays(last: date, count: int):
    days, day = [], last
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def yahoo_bars(days, *, ticker="TEST", hour=13, minute=30, closes=None):
    """Yahoo-shaped daily bars stamped at the US session open (13:30 UTC in EDT)."""
    closes = list(closes) if closes is not None else [100.0 + index for index in range(len(days))]
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": ticker},
        "timestamp": [int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).timestamp()) for d in days],
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes, "volume": [100] * len(closes)}]},
    }]}}).encode()


def stooq_bars(days, closes=None):
    closes = list(closes) if closes is not None else [100.0 + index for index in range(len(days))]
    rows = ["Date,Open,High,Low,Close,Volume"] + [f"{d.isoformat()},{c},{c},{c},{c},100" for d, c in zip(days, closes)]
    return ("\n".join(rows) + "\n").encode()


def v2(kind, analysis_date, collection_at, ticker="TEST"):
    return evidence.market_daily_transform(
        kind=kind, ticker=ticker, symbol=ticker, analysis_date=analysis_date, collection_at=collection_at,
    )


def _market(payload, transform, content_type="application/json"):
    return json.loads(_document_text(payload, content_type, transform))


def test_v1_market_transform_reconstructs_exactly_as_before():
    """Old packs carry no transform_version and must re-derive byte for byte."""
    days = _weekdays(date(2026, 10, 28), 260)
    yahoo = _document_text(yahoo_bars(days), "application/json", dict(_V1_TRANSFORM))
    stooq = _document_text(stooq_bars(days), "text/csv", dict(_V1_TRANSFORM, kind="stooq_daily"))
    # Hashes pinned from the pre-change implementation (HEAD 17c7f09).
    assert evidence.sha256_digest(yahoo.encode()) == V1_YAHOO_SHA256
    assert evidence.sha256_digest(stooq.encode()) == V1_STOOQ_SHA256
    # v1 keeps the requested date inclusive, including the collection-day bar.
    assert json.loads(yahoo)["last_bar_date"] == "2026-10-28"
    assert "transform_version" not in json.loads(yahoo)


def test_v2_transform_records_policy_and_derived_dates():
    transform = v2("yahoo_daily", "2026-10-28", "2026-10-28T15:00:00Z")
    assert transform == {
        "transform_version": "market_daily_previous_completed_v2",
        "kind": "yahoo_daily", "ticker": "TEST", "symbol": "TEST", "analysis_date": "2026-10-28",
        "collection_at": "2026-10-28T15:00:00Z",
        "collection_date_us_eastern": "2026-10-28",
        "eligible_through": "2026-10-27",
    }


@pytest.mark.parametrize("collection_at", [
    "2026-10-28T15:00:00Z",  # 11:00 EDT: intraday bar of the collection day
    "2026-10-28T21:30:00Z",  # 17:30 EDT: after the close, same US/Eastern date
])
@pytest.mark.parametrize("kind", ["yahoo_daily", "stooq_daily"])
def test_v2_excludes_the_collection_day_bar(kind, collection_at):
    days = _weekdays(date(2026, 10, 28), 260)
    closes = [100.0 + index for index in range(len(days))]
    closes[-1] = 999999.0  # The collection-day bar must not reach price or indicators.
    payload = yahoo_bars(days, closes=closes) if kind == "yahoo_daily" else stooq_bars(days, closes)
    data = _market(payload, v2(kind, "2026-10-28", collection_at))
    assert data["last_bar_date"] == "2026-10-27"
    assert data["last_close"] == closes[-2]
    eligible = closes[:-1]
    assert data["TechnicalSet"]["SMA_50"] == round(sum(eligible[-50:]) / 50, 4)
    assert data["TechnicalSet"]["SMA_200"] == round(sum(eligible[-200:]) / 200, 4)
    assert "999999" not in json.dumps(data)
    assert data["history"][-1]["Date"] == "2026-10-27"
    assert data["eligible_through"] == "2026-10-27"
    assert data["collection_date_us_eastern"] == "2026-10-28"
    assert data["collection_day_bar_excluded"] is True
    assert data["transform_version"] == "market_daily_previous_completed_v2"


def test_v2_historical_cutoff_bounds_a_later_collection():
    days = _weekdays(date(2025, 2, 7), 260)
    data = _market(yahoo_bars(days), v2("yahoo_daily", "2025-01-31", "2026-10-06T20:00:00Z"))
    assert data["eligible_through"] == "2025-01-31"
    assert data["last_bar_date"] == "2025-01-31"
    assert data["collection_day_bar_excluded"] is False
    assert all(row["Date"] <= "2025-01-31" for row in data["history"])


def test_v2_weekend_uses_the_latest_earlier_bar():
    # Collected Monday 2026-10-26: the maximum eligible date is Sunday; the
    # last available bar is Friday 2026-10-23.
    days = _weekdays(date(2026, 10, 26), 260)
    data = _market(yahoo_bars(days), v2("yahoo_daily", "2026-10-26", "2026-10-26T14:00:00Z"))
    assert data["eligible_through"] == "2026-10-25"
    assert data["last_bar_date"] == "2026-10-23"


@pytest.mark.parametrize("collection_at, eastern, eligible", [
    ("2026-10-29T02:00:00Z", "2026-10-28", "2026-10-27"),  # 22:00 EDT on the 28th; UTC says the 29th
    ("2026-10-29T04:30:00Z", "2026-10-29", "2026-10-28"),  # 00:30 EDT on the 29th
    ("2026-01-15T04:30:00Z", "2026-01-14", "2026-01-13"),  # 23:30 EST (DST-aware: EDT would be the 15th)
])
def test_v2_uses_the_us_eastern_collection_date(collection_at, eastern, eligible):
    transform = v2("yahoo_daily", "2026-10-29", collection_at)
    assert transform["collection_date_us_eastern"] == eastern
    assert transform["eligible_through"] == eligible


def test_v2_yahoo_bar_dates_are_us_eastern_dates():
    # A bar stamped 03:30 UTC on 2026-10-28 is the 27th in New York.
    days = _weekdays(date(2026, 10, 27), 30)
    data = _market(yahoo_bars(days, hour=3), v2("yahoo_daily", "2026-10-28", "2026-10-28T15:00:00Z"))
    assert data["last_bar_date"] == "2026-10-26"


def test_v2_absent_or_stale_history_fails():
    stale = _weekdays(date(2026, 10, 15), 30)
    with pytest.raises(ValueError, match="stale"):
        _document_text(yahoo_bars(stale), "application/json", v2("yahoo_daily", "2026-10-28", "2026-10-28T15:00:00Z"))
    with pytest.raises(ValueError, match="empty"):
        _document_text(yahoo_bars([date(2026, 10, 28)]), "application/json", v2("yahoo_daily", "2026-10-28", "2026-10-28T15:00:00Z"))


@pytest.mark.parametrize("field, value", [
    ("eligible_through", "2026-10-28"),
    ("collection_date_us_eastern", "2026-10-29"),
    ("collection_at", "2026-10-28"),
    ("transform_version", "market_daily_previous_completed_v9"),
])
def test_v2_inconsistent_transform_metadata_is_rejected(field, value):
    days = _weekdays(date(2026, 10, 28), 30)
    transform = dict(v2("yahoo_daily", "2026-10-28", "2026-10-28T15:00:00Z"), **{field: value})
    with pytest.raises(ValueError):
        _document_text(yahoo_bars(days), "application/json", transform)


def _price_collect(monkeypatch, tmp_path, *, yahoo_fails=False, accessed="2026-10-28T15:00:00Z", analysis_date="2026-10-28"):
    """Collector run whose filings predate the cutoff and whose bars run through it."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test Research test@example.com")
    filings = [("10-K", "2026-02-01", "0000000001-26-000001", "annual26.htm")]
    columns = {key: [row[index] for row in filings] for index, key in enumerate(("form", "filingDate", "accessionNumber", "primaryDocument"))}
    days = _weekdays(date.fromisoformat(analysis_date), 260)
    closes = [100.0 + index for index in range(len(days))]
    closes[-1] = 999999.0
    fetched = []

    def download(url):
        fetched.append(url)
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"ticker": "TEST", "cik_str": 1}}).encode(), "application/json", accessed
        if "companyfacts" in url:
            raise OSError("simulated SEC outage")
        if url.endswith("CIK0000000001.json"):
            data = {"name": "Test Co", "tickers": ["TEST"], "filings": {"recent": columns, "files": []}}
            return json.dumps(data).encode(), "application/json", accessed
        if "finance.yahoo.com" in url:
            if yahoo_fails:
                raise ValueError("Evidence request did not succeed: HTTP 429.")
            return yahoo_bars(days, closes=closes), "application/json", accessed
        if "stooq.com" in url:
            return stooq_bars(days, closes), "text/csv", accessed
        return b"<p>Official statement Revenue 123.</p>", "text/html", accessed

    monkeypatch.setattr(public_data, "download_public_document", download)
    monkeypatch.setattr(evidence, "download_public_document", download)
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    path = collect_public_sources("TEST", analysis_date, "Quick", tmp_path / "collected")
    return path, fetched, closes


def test_collector_downloads_the_price_history_once_and_seals_those_bytes(monkeypatch, tmp_path):
    path, fetched, closes = _price_collect(monkeypatch, tmp_path)
    yahoo = [url for url in fetched if "finance.yahoo.com" in url]
    assert len(yahoo) == 1
    assert not any("stooq.com" in url for url in fetched)
    pack = EvidencePack(path, ticker="TEST", analysis_date="2026-10-28")
    market = next(d for d in pack.documents if d["type"] == "MarketHistory")
    assert market["url"] == yahoo[0]
    transform = market["transform"]
    assert transform["transform_version"] == "market_daily_previous_completed_v2"
    assert transform["collection_at"] == market["accessed"] == "2026-10-28T15:00:00Z"
    assert transform["eligible_through"] == "2026-10-27"
    # The document is dated by the bar it reflects, not the requested cutoff.
    assert market["doc_date"] == "2026-10-27"
    assert "2026-10-27" in market["title"]
    snapshot = pack.market_snapshot()
    assert snapshot["last_bar_date"] == "2026-10-27"
    assert snapshot["last_close"] == closes[-2]
    assert snapshot["collection_day_bar_excluded"] is True
    assert "999999" not in pack.prompt_text()
    collection = json.loads((path.parent.parent / "collection.json").read_text())
    assert collection["market"]["last_bar_date"] == "2026-10-27"
    # The v2 pack replays from its sealed bytes, also after a snapshot.
    reloaded = pack.snapshot(tmp_path / "snap")
    assert reloaded.checksum == pack.checksum
    assert reloaded.market_snapshot() == snapshot


def test_stooq_fallback_is_attempted_once_and_its_probe_bytes_are_sealed(monkeypatch, tmp_path):
    path, fetched, closes = _price_collect(monkeypatch, tmp_path, yahoo_fails=True)
    assert sum("finance.yahoo.com" in url for url in fetched) == 1
    assert sum("stooq.com" in url for url in fetched) == 1
    pack = EvidencePack(path, ticker="TEST", analysis_date="2026-10-28")
    market = next(d for d in pack.documents if d["type"] == "MarketHistory")
    assert market["transform"]["kind"] == "stooq_daily"
    assert pack.market_snapshot()["last_close"] == closes[-2]


def test_tampered_v2_market_transform_fails_replay(monkeypatch, tmp_path):
    path, _, _ = _price_collect(monkeypatch, tmp_path)
    data = json.loads(path.read_text())
    market = next(d for d in data["documents"] if d["type"] == "MarketHistory")
    market["transform"]["collection_at"] = "2026-10-29T15:00:00Z"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        EvidencePack(path, ticker="TEST", analysis_date="2026-10-28")


def test_legacy_market_pack_has_no_market_snapshot(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"ticker": "TEST", "documents": [{
        "url": "https://query1.finance.yahoo.com/v8/finance/chart/TEST", "doc_date": "2026-10-28",
        "type": "MarketHistory", "transform": _V1_TRANSFORM,
    }]}))
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    days = _weekdays(date(2026, 10, 28), 30)
    monkeypatch.setattr(evidence, "download_public_document", lambda url: (yahoo_bars(days), "application/json", "2026-10-28T15:00:00Z"))
    path = evidence.fetch_evidence(manifest, tmp_path / "pack")
    pack = EvidencePack(path, ticker="TEST", analysis_date="2026-10-28")
    assert pack.market_snapshot() is None
    assert json.loads((tmp_path / "pack" / "D001.txt").read_text())["last_bar_date"] == "2026-10-28"
