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
    assert len(coverage["discovery_records"]) == 2
    assert not any("future.htm" in url for url in fetched)


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
