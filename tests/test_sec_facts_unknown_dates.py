"""Unknown filing dates never enter a numeric source ledger, even without a cutoff."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.facts import usable_as_of
from src.sec_facts import _conservative_availability, parse_sec_company_facts

FIXTURE = Path(__file__).parent / "fixtures" / "sec" / "companyfacts_base.json"


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _entries(payload):
    return payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"]


@pytest.mark.parametrize("filed", [None, "", "not-a-date", "2025-02-30", "2025-02-01garbage", "2025-02-01T99:00:00Z", "20250201", "2025-W05-6"])
@pytest.mark.parametrize("cutoff", [None, "2025-06-01", "2099-01-01"])
def test_unknown_date_excluded_by_intake_with_or_without_as_of(filed, cutoff):
    payload = _payload()
    _entries(payload)[0]["filed"] = filed
    assert parse_sec_company_facts(payload, ticker="TEST", as_of=cutoff) == []


def test_missing_filed_and_filing_date_are_excluded(caplog):
    payload = _payload()
    _entries(payload)[0].pop("filed")
    assert parse_sec_company_facts(payload, ticker="TEST") == []
    assert "unknown or malformed filing date" in caplog.text


def test_unknown_date_cannot_supersede_a_valid_original():
    payload = _payload()
    original = _entries(payload)[0]
    bad_amendment = deepcopy(original)
    bad_amendment.update(filed="not-a-date", val=999, form="10-K/A", accn="0000320193-25-000002")
    _entries(payload).append(bad_amendment)
    facts = parse_sec_company_facts(payload, ticker="TEST", as_of="2025-06-01")
    assert len(facts) == 1
    assert facts[0].value == 100
    assert facts[0].supersedes is None
    assert usable_as_of(facts[0], "2025-06-01")


def test_filing_date_alias_is_supported_without_manufacturing_a_date():
    payload = _payload()
    entry = _entries(payload)[0]
    entry["filingDate"] = entry.pop("filed")
    assert len(parse_sec_company_facts(payload, ticker="TEST")) == 1


@pytest.mark.parametrize("stamp, expected", [
    ("2025-02-01T23:30:00-05:00", "2025-02-02T23:59:59Z"),
    ("2025-02-02T00:30:00+05:00", "2025-02-01T23:59:59Z"),
    ("2025-02-01T09:00:00", "2025-02-01T23:59:59Z"),
])
def test_timestamp_fixture_day_is_normalised_to_utc(stamp, expected):
    assert _conservative_availability(stamp) == expected


def test_timestamp_offset_cannot_admit_a_future_filing():
    payload = _payload()
    _entries(payload)[0]["filed"] = "2025-02-01T23:30:00-05:00"
    assert parse_sec_company_facts(payload, ticker="TEST", as_of="2025-02-02T01:00:00Z") == []
    assert len(parse_sec_company_facts(payload, ticker="TEST", as_of="2025-02-03")) == 1


def test_offset_fixtures_sorted_by_utc_availability_for_supersession():
    payload = _payload()
    first = _entries(payload)[0]
    first.update(filed="2025-02-01T23:30:00-05:00", val=200)
    earlier = deepcopy(first)
    earlier.update(filed="2025-02-02T00:30:00+05:00", val=100, accn="0000320193-25-000002")
    _entries(payload).append(earlier)
    facts = parse_sec_company_facts(payload, ticker="TEST")
    assert [fact.value for fact in facts] == [100, 200]
    assert facts[1].supersedes == facts[0].id
