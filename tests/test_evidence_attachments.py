"""Typed evidence attachment loading fails loudly instead of silently.

The previous loader wrapped every attachment load in ``except Exception: pass``.
That made a *corrupted* fact ledger indistinguishable from "no ledger at all" —
a distinction with opposite meanings. A silently-dropped ledger looks exactly
like a run that legitimately has no facts, and downstream audits then treat
"no source" as "nothing to check" rather than "something is broken."

These tests pin the corrected contract:

1. A genuinely absent optional attachment leaves the pack explicitly unavailable
   (no facts), not broken.
2. A valid typed fact ledger loads successfully.
3. A malformed or incompatible fact ledger raises a meaningful validation error.
4. A corrupted attachment is never silently treated as missing.
5. Raw SEC CompanyFacts JSON is routed through the SEC parser, not the
   typed-ledger loader.

All offline; no network and no credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evidence import EvidencePack
from src.utils.checksum import sha256_digest

TICKER = "TEST"


def _base_pack(tmp_path: Path, attachments=None, embedded_facts=None) -> Path:
    """A minimal valid evidence pack, optionally with attachments/facts."""
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    payload = b"Revenue: 123. Public filing data."
    (root / "D001.bytes").write_bytes(payload)
    (root / "D001.txt").write_bytes(payload)
    document = {
        "id": "D001",
        "type": "Filing",
        "title": "Annual results",
        "url": "https://example.com/annual-results",
        "doc_date": "2025-01-01",
        "date_basis": "supplied_manifest",
        "accessed": "2026-10-06T10:00:00Z",
        "content_type": "text/plain",
        "verification": "retrieved",
        "content_sha256": sha256_digest(payload),
        "text_sha256": sha256_digest(payload),
        "raw_file": "D001.bytes",
        "text_file": "D001.txt",
    }
    pack = {"schema_version": "1", "ticker": TICKER, "documents": [document]}
    if attachments:
        pack["attachments"] = attachments
    if embedded_facts is not None:
        pack["facts"] = embedded_facts
    path = root / "evidence.json"
    path.write_text(json.dumps(pack), encoding="utf-8")
    return path


def _valid_fact_dict(fact_id="fact_test_revenue_1") -> dict:
    return {
        "schema_version": "financial_fact_v1",
        "id": fact_id,
        "company": TICKER,
        "concept": "Revenues",
        "unit": "USD",
        "currency": "USD",
        "value": 123.0,
        "filing_accession": "0000320193-25-000079",
        "availability_time": "2025-01-01T00:00:00Z",
        "retrieval_time": "2025-01-02T00:00:00Z",
        "source_location": {"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"},
        "accounting_basis": "US-GAAP",
        "extractor_version": "sec_companyfacts_v1",
        "kind": "reported",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "instant": None,
    }


def _write_attachment(root: Path, filename: str, content: bytes, kind: str) -> dict:
    (root / filename).write_bytes(content)
    return {"file": filename, "sha256": sha256_digest(content), "kind": kind}


# ============================================================== 1. absent = ok


def test_absent_fact_attachment_is_explicitly_unavailable_not_broken(tmp_path):
    """
    An optional fact ledger that is simply not there is a legitimate state.

    The pack must load and report zero facts. It must not raise, because "no
    facts attached" is different from "facts corrupted".
    """
    path = _base_pack(tmp_path)
    pack = EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")
    assert pack.facts == []
    # Explicitly available as "no facts", and get_facts works.
    assert pack.get_facts(as_of="2025-01-31") == []


def test_no_facts_key_at_all_is_fine(tmp_path):
    path = _base_pack(tmp_path)
    data = json.loads(path.read_text())
    assert "facts" not in data
    pack = EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")
    assert pack.facts == []


# ============================================================ 2. valid loads


def test_valid_typed_fact_ledger_loads_successfully(tmp_path):
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    attachment = _write_attachment(
        root, "ledger.facts.json", json.dumps([_valid_fact_dict()]).encode(), "fact_ledger"
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    pack = EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")
    assert len(pack.facts) == 1
    fact = pack.facts[0]
    assert fact.value == 123.0
    assert fact.concept == "Revenues"
    assert fact.company == TICKER


def test_embedded_facts_load_successfully(tmp_path):
    path = _base_pack(tmp_path, embedded_facts=[_valid_fact_dict()])
    pack = EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")
    assert len(pack.facts) == 1
    assert pack.facts[0].value == 123.0


# ========================================================= 3/4. malformed = raise


def test_malformed_fact_ledger_raises_rather_than_being_ignored(tmp_path):
    """
    A fact-ledger attachment whose JSON is broken must raise.

    Previously this was swallowed and the pack loaded with zero facts — the
    exact confusion this test exists to prevent.
    """
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    attachment = _write_attachment(
        root, "ledger.facts.json", b"{not valid json", "fact_ledger"
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    with pytest.raises(ValueError, match="not valid JSON|cannot be loaded"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")


def test_incompatible_fact_ledger_raises(tmp_path):
    """A ledger whose entries do not validate as typed facts must raise."""
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    bad_fact = _valid_fact_dict()
    bad_fact["accounting_basis"] = "NOT-A-BASIS"  # fails schema + semantic checks
    attachment = _write_attachment(
        root, "ledger.facts.json", json.dumps([bad_fact]).encode(), "fact_ledger"
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    with pytest.raises(ValueError, match="invalid fact|accounting_basis|basis"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")


def test_corrupted_attachment_bytes_are_never_treated_as_missing(tmp_path):
    """
    If an attachment is declared and its bytes are corrupt, the checksum check
    fires; it must not be reported as "no attachment".
    """
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    attachment = _write_attachment(
        root, "ledger.facts.json", json.dumps([_valid_fact_dict()]).encode(), "fact_ledger"
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    # Corrupt the bytes after the checksum was recorded.
    (root / "ledger.facts.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="checksum"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")


def test_fact_ledger_with_missing_value_and_no_reason_is_rejected(tmp_path):
    """A fact with a null value but no documented missing_reason is malformed."""
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    broken = _valid_fact_dict()
    broken["value"] = None
    broken["missing_reason"] = None
    attachment = _write_attachment(
        root, "ledger.facts.json", json.dumps([broken]).encode(), "fact_ledger"
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    with pytest.raises(ValueError, match="missing_reason|invalid fact"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")


def test_non_list_facts_key_is_rejected(tmp_path):
    path = _base_pack(tmp_path, embedded_facts={"not": "a list"})
    with pytest.raises(ValueError, match="must be a list"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")


def test_malformed_embedded_fact_is_rejected(tmp_path):
    broken = _valid_fact_dict()
    broken["value"] = "not-a-number"
    path = _base_pack(tmp_path, embedded_facts=[broken])
    with pytest.raises(ValueError, match="malformed|cannot be loaded"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")


# =================================================== 5. SEC routing correctness


def _sec_companyfacts_payload() -> dict:
    """A small, deterministic SEC CompanyFacts document."""
    return {
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
                                "val": 123,
                                "accn": "0000320193-25-000079",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                }
            }
        },
    }


def test_raw_sec_companyfacts_is_routed_through_the_sec_parser(tmp_path):
    """
    A ``sec_companyfacts`` attachment is raw SEC JSON, not a typed ledger.

    It must be parsed by the SEC parser (producing extractor_version
    ``sec_companyfacts_v1`` and SEC-shaped IDs), proving it did NOT go through
    the typed-ledger loader which would reject it as an unknown layout.
    """
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    attachment = _write_attachment(
        root,
        "companyfacts.json",
        json.dumps(_sec_companyfacts_payload()).encode(),
        "sec_companyfacts",
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    pack = EvidencePack(path, ticker=TICKER, analysis_date="2025-06-01")
    assert len(pack.facts) == 1
    fact = pack.facts[0]
    assert fact.extractor_version == "sec_companyfacts_v1"
    assert fact.concept == "Revenues"
    assert fact.value == 123.0
    assert fact.filing_accession == "0000320193-25-000079"
    assert fact.id.startswith("fact_")


def test_sec_companyfacts_kind_with_wrong_payload_is_rejected(tmp_path):
    """
    Declaring ``sec_companyfacts`` but supplying a typed ledger is an
    incompatible attachment. It must raise, not silently load or silently skip.
    """
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    attachment = _write_attachment(
        root,
        "companyfacts.json",
        json.dumps([_valid_fact_dict()]).encode(),
        "sec_companyfacts",
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    with pytest.raises(ValueError, match="not a SEC CompanyFacts"):
        EvidencePack(path, ticker=TICKER, analysis_date="2025-06-01")


def test_non_fact_attachment_kinds_are_ignored(tmp_path):
    """Unrelated attachments (e.g. web-research traces) must not break loading."""
    root = tmp_path / "pack"
    root.mkdir(parents=True, exist_ok=True)
    attachment = _write_attachment(
        root, "web.json", json.dumps({"accepted": [], "limitations": []}).encode(),
        "gemini_web_research",
    )
    path = _base_pack(tmp_path, attachments=[attachment])
    pack = EvidencePack(path, ticker=TICKER, analysis_date="2025-01-31")
    assert pack.facts == []