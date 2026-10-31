"""Snapshotting preserves the cutoff and therefore the date-bounded fact prompt."""
import json

from src.evidence import EvidencePack
from src.facts import Fact
from src.utils.checksum import sha256_digest


def test_snapshot_preserves_analysis_cutoff_and_eligible_typed_facts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    body = b"Public annual filing."
    (source / "filing.bytes").write_bytes(body)
    (source / "filing.txt").write_bytes(body)
    document = {
        "id": "D001", "type": "Filing", "title": "Annual filing",
        "url": "https://example.com/filing", "doc_date": "2025-01-01",
        "date_basis": "supplied_manifest", "accessed": "2025-06-01T10:00:00Z",
        "content_type": "text/plain", "verification": "retrieved",
        "content_sha256": sha256_digest(body), "text_sha256": sha256_digest(body),
        "raw_file": "filing.bytes", "text_file": "filing.txt",
    }
    def fact(fact_id, available):
        return Fact(
            id=fact_id, company="TEST", concept="Revenues", unit="USD", currency="USD",
            filing_accession="0000320193-25-000001", availability_time=available,
            retrieval_time="2025-06-01T10:00:00Z", source_location={"url": "https://example.com/filing"},
            accounting_basis="US-GAAP", extractor_version="test", value=123.0,
            period_start="2024-01-01", period_end="2024-12-31",
        ).to_dict()
    payload = {
        "schema_version": "1", "ticker": "TEST", "documents": [document],
        "facts": [fact("eligible", "2025-02-01T23:59:59Z"), fact("future", "2025-07-01T23:59:59Z")],
    }
    path = source / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-06-01")
    snapshot = pack.snapshot(tmp_path / "snapshot")
    assert snapshot.analysis_date == pack.analysis_date == "2025-06-01"
    assert snapshot.checksum == pack.checksum
    assert snapshot.prompt_text() == pack.prompt_text()
    assert '"id": "eligible"' in snapshot.prompt_text()
    assert '"id": "future"' not in snapshot.prompt_text()
    # Re-loading for resume must see exactly the same source context.
    resumed = EvidencePack(snapshot.path, ticker="TEST", analysis_date="2025-06-01")
    assert resumed.prompt_text() == snapshot.prompt_text()


def test_document_dated_on_cutoff_is_accepted_next_day_rejected(tmp_path):
    import pytest

    body = b"Public filing."
    for doc_date, ok in (("2025-06-01", True), ("2025-06-02", False)):
        root = tmp_path / doc_date
        root.mkdir()
        (root / "D001.bytes").write_bytes(body)
        (root / "D001.txt").write_bytes(body)
        document = {
            "id": "D001", "type": "Filing", "title": "Filing", "url": "https://example.com/f",
            "doc_date": doc_date, "date_basis": "supplied_manifest", "accessed": "2025-06-03T10:00:00Z",
            "content_type": "text/plain", "verification": "retrieved",
            "content_sha256": sha256_digest(body), "text_sha256": sha256_digest(body),
            "raw_file": "D001.bytes", "text_file": "D001.txt",
        }
        path = root / "evidence.json"
        path.write_text(json.dumps({"schema_version": "1", "ticker": "TEST", "documents": [document]}))
        if ok:
            EvidencePack(path, ticker="TEST", analysis_date="2025-06-01")
        else:
            with pytest.raises(ValueError, match="look-ahead"):
                EvidencePack(path, ticker="TEST", analysis_date="2025-06-01")


def test_pack_facts_filed_on_cutoff_day_reach_the_prompt(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    body = b"Public annual filing."
    (source / "filing.bytes").write_bytes(body)
    (source / "filing.txt").write_bytes(body)
    document = {
        "id": "D001", "type": "Filing", "title": "Annual filing",
        "url": "https://example.com/filing", "doc_date": "2025-06-01",
        "date_basis": "supplied_manifest", "accessed": "2025-06-02T10:00:00Z",
        "content_type": "text/plain", "verification": "retrieved",
        "content_sha256": sha256_digest(body), "text_sha256": sha256_digest(body),
        "raw_file": "filing.bytes", "text_file": "filing.txt",
    }
    fact = Fact(
        id="same_day", company="TEST", concept="Revenues", unit="USD", currency="USD",
        filing_accession="0000320193-25-000001", availability_time="2025-06-01T23:59:59Z",
        retrieval_time="2025-06-02T10:00:00Z", source_location={"url": "https://example.com/filing"},
        accounting_basis="US-GAAP", extractor_version="test", value=123.0,
        period_start="2024-01-01", period_end="2024-12-31",
    ).to_dict()
    path = source / "evidence.json"
    path.write_text(json.dumps({"schema_version": "1", "ticker": "TEST", "documents": [document], "facts": [fact]}))
    pack = EvidencePack(path, ticker="TEST", analysis_date="2025-06-01")
    assert [f.id for f in pack.get_facts()] == ["same_day"]
    assert '"id": "same_day"' in pack.prompt_text()
