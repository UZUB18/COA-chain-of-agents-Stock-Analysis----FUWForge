"""SEC CompanyFacts point-in-time intake and disclosure classification.

Three properties matter here, and each one previously failed:

1. **Conservative availability.** The SEC ``filed`` field is a calendar date
   with no intraday publication time. Treating a fact as available from
   ``00:00:00Z`` on its filing date asserts intraday availability the evidence
   does not support. These tests pin availability to the *end* of the filed day.

2. **Repeats are not restatements.** A later filing that re-reports the *same*
   value for the same period is a repeated disclosure, not a restatement.
   Mislabelling it as a restatement would supersede (and thereby erase) an
   original figure that was never wrong.

3. **Distinguishing the four cases.** Initial disclosure, repeated disclosure,
   changed value, and an explicit ``/A`` amendment must each be distinguishable,
   because they justify different conclusions about a company's reporting.

All fixtures are deterministic, local, and require no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.facts import FactKind, resolve_as_of, usable_as_of
from src.sec_facts import (
    AVAILABILITY_POLICY,
    _conservative_availability,
    parse_sec_company_facts,
)

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "sec"


def _parse(name: str, ticker: str = "TEST"):
    return parse_sec_company_facts(FIXTURES / f"companyfacts_{name}.json", ticker=ticker)


def _by_accession(facts, accession):
    return next(f for f in facts if f.filing_accession == accession)


# ================================================== 1. conservative availability


def test_availability_is_end_of_filed_day_not_start():
    facts = _parse("repeat_disclosure")
    for fact in facts:
        # filed 2025-02-01 -> available 2025-02-01T23:59:59Z, never T00:00:00Z.
        assert fact.availability_time.endswith("T23:59:59Z"), fact.availability_time
    assert _conservative_availability("2025-02-01") == "2025-02-01T23:59:59Z"


def test_fact_is_not_usable_at_the_start_of_its_filing_day():
    """
    A cutoff at midnight on the filing date must NOT see the fact.

    This is the concrete harm the policy prevents: claiming a fact was public
    from 00:00 when the filing may not have been accepted until after the close.
    """
    fact = _by_accession(_parse("base"), "0000320193-25-000001")
    assert usable_as_of(fact, "2025-02-01T00:00:00Z") is False
    assert usable_as_of(fact, "2025-02-01T12:00:00Z") is False
    # Only once the filed day has fully elapsed.
    assert usable_as_of(fact, "2025-02-01T23:59:59Z") is True
    assert usable_as_of(fact, "2025-02-02T00:00:00Z") is True


def test_unparseable_filing_date_has_no_availability_timestamp():
    """An epoch would pass modern cutoffs; unknown dates have no timestamp."""
    assert _conservative_availability("") is None
    assert _conservative_availability("not-a-date") is None


def test_availability_policy_is_recorded_on_every_fact():
    facts = _parse("changed_value")
    for fact in facts:
        assert fact.metadata is not None
        assert fact.metadata["availability_policy"] == AVAILABILITY_POLICY


# ==================================== 2. repeated disclosure is NOT a restatement


def test_same_value_in_a_later_filing_is_a_repeat_not_a_restatement():
    facts = _parse("repeat_disclosure")
    original = _by_accession(facts, "0000320193-25-000001")
    repeat = _by_accession(facts, "0000320193-25-000002")

    assert repeat.value == original.value == 100.0
    assert repeat.metadata["disclosure_relation"] == "repeated_disclosure"
    assert repeat.kind == FactKind.REPORTED, "a repeat is not a restatement"
    assert repeat.supersedes is None, "a repeat must not supersede the original"
    # The original survives as its own observation.
    assert original.id in {f.id for f in facts}


def test_point_in_time_resolution_keeps_the_original_across_a_repeat():
    """
    Because a repeat does not supersede, resolving as of a date between the two
    filings still yields the original fact.
    """
    facts = _parse("repeat_disclosure")
    resolved = resolve_as_of(facts, "Revenues", "2025-06-01T00:00:00Z")
    assert resolved is not None
    assert resolved.filing_accession == "0000320193-25-000001"
    assert resolved.value == 100.0


# ================================================ 3. the four distinct cases


def test_changed_value_in_a_later_filing_is_a_restatement():
    facts = _parse("changed_value")
    original = _by_accession(facts, "0000320193-25-000001")
    restated = _by_accession(facts, "0000320193-25-000002")

    assert original.value == 100.0
    assert restated.value == 150.0
    assert restated.metadata["disclosure_relation"] == "restated"
    assert restated.kind == FactKind.RESTATEMENT
    assert restated.supersedes == original.id
    assert restated.metadata["prior_value"] == 100.0


def test_amendment_form_is_labelled_amended_and_supersedes():
    facts = _parse("amended_form")
    original = _by_accession(facts, "0000320193-25-000001")
    amended = _by_accession(facts, "0000320193-25-000009")

    assert amended.metadata["disclosure_relation"] == "amended"
    assert amended.kind == FactKind.RESTATEMENT
    assert amended.supersedes == original.id
    assert amended.source_location["form"] == "10-K/A"


def test_initial_disclosure_is_labelled_and_does_not_supersede():
    facts = _parse("base")
    fact = facts[0]
    assert fact.metadata["disclosure_relation"] == "initial_disclosure"
    assert fact.kind == FactKind.REPORTED
    assert fact.supersedes is None


def test_restatement_is_invisible_before_it_was_filed():
    """
    A restatement published after the cutoff must not leak backwards.

    Before the amended filing's availability, resolution must return the
    original value; only afterwards the restated value.
    """
    facts = _parse("changed_value")

    before = resolve_as_of(facts, "Revenues", "2025-06-01T00:00:00Z")
    assert before is not None
    assert before.value == 100.0

    after = resolve_as_of(facts, "Revenues", "2025-12-01T00:00:00Z")
    assert after is not None
    assert after.value == 150.0


def test_same_value_amendment_form_is_still_labelled_amended():
    """
    An /A form that re-reports the same number is still an amendment.

    The form itself is the explicit statement that the filing was amended; the
    classifier must not downgrade it to a plain repeat because the number
    happens to match.
    """
    payload = {
        "cik": 320193,
        "entityName": "TEST CORP",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100.0,
                                "accn": "0000320193-25-000001",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100.0,
                                "accn": "0000320193-25-000009",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K/A",
                                "filed": "2025-08-01",
                            },
                        ]
                    }
                }
            }
        },
    }
    facts = parse_sec_company_facts(payload, ticker="TEST")
    amended = _by_accession(facts, "0000320193-25-000009")
    assert amended.metadata["disclosure_relation"] == "amended"
    assert amended.kind == FactKind.RESTATEMENT


# ============================================================== provenance


def test_every_fact_retains_full_provenance():
    facts = _parse("changed_value")
    for fact in facts:
        assert fact.filing_accession
        assert fact.availability_time
        assert fact.retrieval_time
        assert fact.unit == "USD"
        assert fact.value is not None
        assert fact.extractor_version == "sec_companyfacts_v1"
        assert fact.source_location["url"].startswith("https://data.sec.gov/")
        assert fact.source_location["accession"] == fact.filing_accession
        assert fact.source_location["form"]
        assert fact.source_location["filed"]
        assert fact.period_start == "2024-01-01"
        assert fact.period_end == "2024-12-31"
        assert fact.accounting_basis is not None


def test_fixtures_are_valid_companyfacts_documents():
    """The deterministic fixtures must be real CompanyFacts shape."""
    for path in sorted(FIXTURES.glob("companyfacts_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "facts" in payload
        assert "cik" in payload or "entityName" in payload
        assert isinstance(payload["facts"], dict)


def test_as_of_filtering_in_parser_respects_conservative_availability():
    facts = parse_sec_company_facts(
        FIXTURES / "companyfacts_changed_value.json",
        ticker="TEST",
        as_of="2025-02-01T00:00:00Z",
    )
    # Nothing is available at midnight on the first filing day.
    assert facts == []
    facts = parse_sec_company_facts(
        FIXTURES / "companyfacts_changed_value.json",
        ticker="TEST",
        as_of="2025-03-01T00:00:00Z",
    )
    assert len(facts) == 1