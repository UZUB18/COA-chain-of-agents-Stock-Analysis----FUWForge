"""Prompt truncation must not hide core financials or retain superseded values."""
from dataclasses import replace

from src.evidence import _select_prompt_facts
from src.facts import Fact


def _fact(fact_id, concept="Incidental", year=2024, **kwargs):
    return Fact(
        id=fact_id, company="ACME", concept=concept, unit="USD", value=100.0,
        filing_accession="0000320193-25-000001", availability_time="2025-02-01T00:00:00Z",
        retrieval_time="2025-02-02T00:00:00Z", source_location={"url": "https://data.sec.gov/filing"},
        accounting_basis="US-GAAP", extractor_version="fixture",
        period_start=f"{year}-01-01", period_end=f"{year}-12-31", **kwargs,
    )


def test_core_current_and_prior_periods_survive_many_incidental_concepts():
    facts = [_fact(f"noise_{i}", concept=f"Incidental{i}") for i in range(500)]
    core = [_fact(f"{concept}_{year}", concept=concept, year=year)
            for concept in ("Revenues", "Assets", "NetIncomeLoss", "AccountsReceivableNetCurrent")
            for year in (2023, 2024)]
    facts.extend(core)
    selected, total = _select_prompt_facts(facts)
    assert len(selected) == 100
    assert total == 508
    assert {f.id for f in core} <= {f.id for f in selected}
    assert [f.id for f in selected] == [f.id for f in _select_prompt_facts(list(reversed(facts)))[0]]


def test_superseded_records_removed_and_recent_annual_periods_ranked_first():
    old = _fact("old", "Revenues", 2024)
    replacement = replace(old, id="amendment", value=120.0, supersedes="old", availability_time="2025-03-01T00:00:00Z")
    prior = _fact("prior", "Revenues", 2023)
    quarter = replace(old, id="quarter", period_start="2025-01-01", period_end="2025-03-31")
    selected, total = _select_prompt_facts([old, quarter, prior, replacement], limit=2)
    assert [f.id for f in selected] == ["amendment", "prior"]
    assert total == 3


def test_duplicate_periods_do_not_crowd_out_prior_comparative():
    old = _fact("original", "Assets", 2024)
    latest = replace(old, id="later_filing", availability_time="2025-03-01T00:00:00Z")
    prior = _fact("prior", "Assets", 2023)
    selected, _ = _select_prompt_facts([old, prior, latest], limit=2)
    assert [f.id for f in selected] == ["later_filing", "prior"]
