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
        "timestamp": [int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp()) for day in dates],
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
