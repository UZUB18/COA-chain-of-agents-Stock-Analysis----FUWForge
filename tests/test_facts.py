"""
Tests for typed financial facts ledger and filing chronology.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from src.facts import (
    AccountingBasis,
    Fact,
    FactKind,
    check_ledger_consistency,
    flag_concept_unit_mismatch,
    flag_unit_mismatch,
    load_fact_ledger,
    parse_iso_datetime,
    resolve_as_of,
    usable_as_of,
    validate_fact,
    validate_fact_dict,
)
from src.validators import ValidationResult

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "facts"


class TestFactFixturesValidation:
    """Validate fixtures against schema and semantic rules."""

    def test_standard_annual_fixture(self):
        fact_path = FIXTURES_DIR / "standard_annual.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        res = validate_fact(data)
        assert res.valid, f"Validation failed with errors: {res.errors}"

        fact = Fact.from_dict(data)
        assert fact.id == "fact_aapl_rev_2023_10k"
        assert fact.company == "AAPL"
        assert fact.concept == "Revenues"
        assert fact.value == 383285000000.0
        assert fact.unit == "USD"
        assert fact.currency == "USD"
        assert fact.period_start == "2022-10-01"
        assert fact.period_end == "2023-09-30"
        assert fact.is_available()
        assert not fact.is_unavailable()
        assert fact.is_reported()
        assert not fact.is_forecast()
        assert fact.period_key() == "duration:2022-10-01/2023-09-30"

    def test_quarter_fixture(self):
        fact_path = FIXTURES_DIR / "quarter.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        res = validate_fact(data)
        assert res.valid, f"Validation failed with errors: {res.errors}"

        fact = Fact.from_dict(data)
        assert fact.id == "fact_aapl_rev_2024_q1"
        assert fact.value == 119575000000.0
        assert fact.accounting_basis == AccountingBasis.US_GAAP

    def test_restatement_fixture(self):
        fact_path = FIXTURES_DIR / "restatement.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        res = validate_fact(data)
        assert res.valid, f"Validation failed with errors: {res.errors}"

        fact = Fact.from_dict(data)
        assert fact.supersedes == "fact_aapl_rev_2023_10k"
        assert fact.value == 383280000000.0

    def test_custom_dimensional_fixture(self):
        fact_path = FIXTURES_DIR / "custom_dimensional.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        res = validate_fact(data)
        assert res.valid, f"Validation failed with errors: {res.errors}"

        fact = Fact.from_dict(data)
        assert fact.dimensions is not None
        assert fact.dimensions.get("ProductOrServiceAxis") == "iPhoneMember"
        assert isinstance(fact.source_location, dict)
        assert "url" in fact.source_location

    def test_unavailable_input_fixture(self):
        fact_path = FIXTURES_DIR / "unavailable.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        res = validate_fact(data)
        assert res.valid, f"Validation failed with errors: {res.errors}"

        fact = Fact.from_dict(data)
        assert fact.value is None
        assert fact.is_unavailable()
        assert not fact.is_available()
        assert fact.missing_reason == "Company does not disclose direct labor breakdown for R&D in SEC filings"
        # Must not be coerced to 0
        assert fact.value is None
        assert fact.value != 0

    def test_forecast_fixture(self):
        fact_path = FIXTURES_DIR / "forecast.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        res = validate_fact(data)
        assert res.valid, f"Validation failed with errors: {res.errors}"

        fact = Fact.from_dict(data)
        assert fact.kind == FactKind.FORECAST
        assert fact.is_forecast()
        assert not fact.is_reported()


class TestUnitConsistencyAndErrors:
    """Test unit checking and detection of scale/basis errors."""

    def test_unit_error_1000x_detected(self):
        fact_path = FIXTURES_DIR / "unit_error_1000x.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        fact = Fact.from_dict(data)

        # Flag mismatch when expected unit is USD
        errors = flag_unit_mismatch(fact, "USD")
        assert len(errors) > 0
        assert "1,000x" in errors[0] or "USD_thousands" in errors[0]

        # Concept registry flag
        concept_errors = flag_concept_unit_mismatch(fact)
        assert len(concept_errors) > 0
        assert "Unit mismatch for concept 'Revenues'" in concept_errors[0]

    def test_share_count_basis_mismatch_detected(self):
        fact_path = FIXTURES_DIR / "share_count_basis_mismatch.json"
        data = json.loads(fact_path.read_text(encoding="utf-8"))
        fact = Fact.from_dict(data)

        # Flagged when expected basic shares
        errors = flag_unit_mismatch(fact, "shares_basic")
        assert len(errors) > 0
        assert "share count basis mismatch" in errors[0] or "shares_diluted" in errors[0]

    def test_ledger_consistency_detects_conflicting_units(self):
        standard = Fact.from_dict(json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8")))
        unit_err = Fact.from_dict(json.loads((FIXTURES_DIR / "unit_error_1000x.json").read_text(encoding="utf-8")))

        # Two facts for same concept/period with different units and no supersedes relationship
        conflicts = check_ledger_consistency([standard, unit_err])
        assert len(conflicts) > 0
        assert any("Conflicting units" in err for err in conflicts)

    def test_ledger_consistency_accepts_valid_supersedes(self):
        standard = Fact.from_dict(json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8")))
        restatement = Fact.from_dict(json.loads((FIXTURES_DIR / "restatement.json").read_text(encoding="utf-8")))

        # Restatement explicitly supersedes standard_annual, so no unlinked conflict
        conflicts = check_ledger_consistency([standard, restatement])
        assert len(conflicts) == 0

    def test_ledger_consistency_detects_circular_supersedes(self):
        f1_dict = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        f2_dict = json.loads((FIXTURES_DIR / "restatement.json").read_text(encoding="utf-8"))

        f1_dict["supersedes"] = f2_dict["id"]
        f2_dict["supersedes"] = f1_dict["id"]

        f1 = Fact.from_dict(f1_dict)
        f2 = Fact.from_dict(f2_dict)

        conflicts = check_ledger_consistency([f1, f2])
        assert any("Circular supersedes" in c for c in conflicts)


class TestChronologyAndAntiLookAhead:
    """Test anti-look-ahead chronology guards."""

    def test_usable_as_of_anti_look_ahead(self):
        # Fact available on 2023-11-03 for period ending 2023-09-30
        fact = Fact.from_dict(json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8")))

        # Cutoff before publication date (e.g. 2023-10-15): NOT usable even though period ended 2023-09-30
        assert not usable_as_of(fact, "2023-10-15T00:00:00Z")
        assert not usable_as_of(fact, "2023-11-03T17:59:59Z")

        # Cutoff on/after publication date: usable
        assert usable_as_of(fact, "2023-11-03T18:00:00Z")
        assert usable_as_of(fact, "2023-11-04T00:00:00Z")
        assert usable_as_of(fact, "2024-01-01T00:00:00Z")

    def test_usable_as_of_with_datetime_objects(self):
        fact = Fact.from_dict(json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8")))
        cutoff_early = datetime(2023, 10, 1, tzinfo=timezone.utc)
        cutoff_late = datetime(2023, 11, 4, tzinfo=timezone.utc)

        assert not usable_as_of(fact, cutoff_early)
        assert usable_as_of(fact, cutoff_late)


class TestRestatementsAndAsOfResolution:
    """Test point-in-time restatement resolution."""

    def test_restatement_resolution_before_and_after_restatement_publication(self):
        original = Fact.from_dict(json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8")))
        restated = Fact.from_dict(json.loads((FIXTURES_DIR / "restatement.json").read_text(encoding="utf-8")))
        facts = [original, restated]

        # 1. As of 2023-12-31: original was published (2023-11-03), restatement was NOT (2024-11-01)
        res_2023 = resolve_as_of(facts, "Revenues", "2023-12-31T00:00:00Z")
        assert res_2023 is not None
        assert res_2023.id == "fact_aapl_rev_2023_10k"
        assert res_2023.value == 383285000000.0

        # 2. As of 2024-11-02: restatement IS published and supersedes original
        res_2024 = resolve_as_of(facts, "Revenues", "2024-11-02T00:00:00Z")
        assert res_2024 is not None
        assert res_2024.id == "fact_aapl_rev_2023_restated_2024_10k"
        assert res_2024.value == 383280000000.0

    def test_forecast_records_never_satisfy_reported_fact_resolution(self):
        forecast = Fact.from_dict(json.loads((FIXTURES_DIR / "forecast.json").read_text(encoding="utf-8")))
        facts = [forecast]

        # Reported lookup must ignore forecast
        res = resolve_as_of(facts, "Revenues", "2024-05-01T00:00:00Z", kind=FactKind.REPORTED)
        assert res is None

        # Even with default kind, allow_forecasts=False excludes it
        res_default = resolve_as_of(facts, "Revenues", "2024-05-01T00:00:00Z")
        assert res_default is None

        # Explicitly asking for forecast finds it
        res_forecast = resolve_as_of(
            facts, "Revenues", "2024-05-01T00:00:00Z", kind=FactKind.FORECAST, allow_forecasts=True
        )
        assert res_forecast is not None
        assert res_forecast.id == forecast.id


class TestSemanticValidationRules:
    """Test validation errors on malformed facts."""

    def test_unavailable_without_reason_fails(self):
        data = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        data["value"] = None
        data["missing_reason"] = None

        errors = validate_fact_dict(data)
        assert any("When 'value' is None, 'missing_reason' must be a non-empty string" in e for e in errors)

    def test_boolean_value_rejected(self):
        data = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        data["value"] = True

        errors = validate_fact_dict(data)
        assert any("boolean" in e for e in errors)

    def test_period_start_after_end_rejected(self):
        data = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        data["period_start"] = "2023-10-01"
        data["period_end"] = "2022-10-01"

        errors = validate_fact_dict(data)
        assert any("cannot be after 'period_end'" in e for e in errors)

    def test_missing_period_and_instant_rejected(self):
        data = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        data["period_start"] = None
        data["period_end"] = None
        data["instant"] = None

        errors = validate_fact_dict(data)
        assert any("either ('period_start' and 'period_end') or 'instant'" in e for e in errors)

    def test_both_period_and_instant_rejected(self):
        data = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        data["instant"] = "2023-09-30"

        errors = validate_fact_dict(data)
        assert any("cannot specify both duration" in e for e in errors)

    def test_invalid_accounting_basis_rejected(self):
        data = json.loads((FIXTURES_DIR / "standard_annual.json").read_text(encoding="utf-8"))
        data["accounting_basis"] = "INVALID_ACCOUNTING_BASIS"

        errors = validate_fact_dict(data)
        assert any("Invalid 'accounting_basis'" in e for e in errors)


class TestLoadFactLedger:
    """Test loading fact files and directories."""

    def test_load_from_directory(self):
        facts = load_fact_ledger(FIXTURES_DIR)
        assert len(facts) >= 6
        ids = {f.id for f in facts}
        assert "fact_aapl_rev_2023_10k" in ids
        assert "fact_aapl_rev_2024_q1" in ids

    def test_load_from_single_file(self):
        facts = load_fact_ledger(FIXTURES_DIR / "quarter.json")
        assert len(facts) == 1
        assert facts[0].id == "fact_aapl_rev_2024_q1"

    def test_load_from_dict_list(self):
        data = json.loads((FIXTURES_DIR / "quarter.json").read_text(encoding="utf-8"))
        facts = load_fact_ledger([data])
        assert len(facts) == 1
        assert isinstance(facts[0], Fact)


class TestDateOnlyCutoffIsEndOfUtcDay:
    """D2: a date-only cutoff D means D 23:59:59 UTC; explicit instants stay exact."""

    @staticmethod
    def _fact(fact_id, available, *, value=100.0, kind="reported", supersedes=None):
        return Fact(
            id=fact_id, company="TEST", concept="Revenues", unit="USD", currency="USD",
            filing_accession="0000000001-25-000001", availability_time=available,
            retrieval_time="2025-06-01T00:00:00Z", source_location="https://www.sec.gov/x",
            accounting_basis="US-GAAP", extractor_version="test", value=value,
            period_start="2024-01-01", period_end="2024-12-31", kind=kind, supersedes=supersedes,
        )

    def test_cutoff_instant_rules(self):
        from datetime import date

        from src.facts import cutoff_instant

        end = datetime(2025, 3, 10, 23, 59, 59, tzinfo=timezone.utc)
        assert cutoff_instant("2025-03-10") == end
        assert cutoff_instant(date(2025, 3, 10)) == end
        assert cutoff_instant("2025-03-10T00:00:00Z") == datetime(2025, 3, 10, tzinfo=timezone.utc)
        assert cutoff_instant(datetime(2025, 3, 10)) == datetime(2025, 3, 10, tzinfo=timezone.utc)
        assert cutoff_instant("2025-03-10T02:00:00+02:00") == datetime(2025, 3, 10, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            cutoff_instant("2025-03-10garbage")
        # Economic periods and other timestamps keep their start-of-day parsing.
        assert parse_iso_datetime("2025-03-10") == datetime(2025, 3, 10, tzinfo=timezone.utc)

    def test_fact_filed_on_cutoff_day_is_usable_next_day_is_not(self):
        assert usable_as_of(self._fact("same_day", "2025-03-10T23:59:59Z"), "2025-03-10")
        assert not usable_as_of(self._fact("next_day", "2025-03-11T23:59:59Z"), "2025-03-10")
        assert not usable_as_of(self._fact("next_midnight", "2025-03-11T00:00:00Z"), "2025-03-10")

    def test_explicit_midnight_cutoff_still_excludes_end_of_day_fact(self):
        fact = self._fact("same_day", "2025-03-10T23:59:59Z")
        assert not usable_as_of(fact, "2025-03-10T00:00:00Z")
        assert not usable_as_of(fact, datetime(2025, 3, 10, tzinfo=timezone.utc))

    def test_resolve_as_of_before_and_on_restatement_day(self):
        original = self._fact("orig", "2025-02-01T23:59:59Z", value=100.0)
        restated = self._fact("restated", "2025-03-10T23:59:59Z", value=90.0,
                              kind="restatement", supersedes="orig")
        assert resolve_as_of([original, restated], "Revenues", "2025-03-09").id == "orig"
        assert resolve_as_of([original, restated], "Revenues", "2025-03-10").id == "restated"
        assert resolve_as_of([original, restated], "Revenues", "2025-03-10T12:00:00Z").id == "orig"
