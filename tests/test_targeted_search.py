"""Offline unit and integration tests for targeted web search and execution bundles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.agents.base import AgentContext
from src.agents.model import GeminiApiBackend, ModelAgent
from src.contracts import ValidationResult
from src.envelope import Envelope
from src.run_semantic_audit import build_run_manifest, checksum_file, semantic_audit
from src.targeted_search import (
    SearchConflictError,
    SearchOutcome,
    SearchTrigger,
    detect_search_trigger,
    format_untrusted_search_sources,
    perform_targeted_search,
    verify_search_conflict,
    write_search_bundle,
)


def _fake_interaction(urls=("https://news.example/item",)):
    return {
        "status": "completed",
        "usage": {"totalTokenCount": 150},
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "text": "Analysis prose...",
                        "annotations": [
                            {"type": "url_citation", "url": u, "title": "Verified source"}
                            for u in urls
                        ],
                    }
                ],
            }
        ],
    }


def _fake_html(date_str="2025-01-15"):
    meta = {"@type": "NewsArticle", "datePublished": date_str}
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(meta)}</script></head>'
        f"<body><p>Public company disclosure information.</p></body></html>"
    ).encode("utf-8")


def test_search_trigger_to_dict():
    trigger = SearchTrigger(
        agent_id="A1",
        field_paths=("market_cap_check",),
        reason="Market cap deviates from price * shares",
        source_type_sought="market_quote",
        query_intents=("latest closing quote",),
        category="market_binding_conflict",
    )
    d = trigger.to_dict()
    assert d["agent_id"] == "A1"
    assert d["field_paths"] == ["market_cap_check"]
    assert d["category"] == "market_binding_conflict"


def test_detect_search_trigger_structured_request():
    draft = {
        "search_request": {
            "problem": "Uncertain litigation settlement amount",
            "fields": ["MajorLitigation"],
            "source_type": "court_record",
            "query_intents": ["legal settlement details"],
        }
    }
    trigger = detect_search_trigger("A5", draft, [])
    assert trigger is not None
    assert trigger.category == "agent_request"
    assert "MajorLitigation" in trigger.field_paths
    assert trigger.reason == "Uncertain litigation settlement amount"


def test_detect_search_trigger_contract_errors():
    draft = {"artifacts": {}}
    errors = ["PEER_LIST must contain at least three ticker strings"]
    trigger = detect_search_trigger("A2", draft, errors)
    assert trigger is not None
    assert trigger.category == "validation_defect"
    assert "PEER_LIST" in trigger.field_paths


def test_detect_search_trigger_market_binding():
    draft = {"artifacts": {}}
    errors = ["market_cap_check.cap deviates from price × shares by more than 5%"]
    trigger = detect_search_trigger("A1", draft, errors)
    assert trigger is not None
    assert trigger.category == "market_binding_conflict"


def test_detect_search_trigger_clean_path():
    draft = {"artifacts": {"some_field": 123}}
    trigger = detect_search_trigger("A3", draft, [])
    assert trigger is None


def test_perform_targeted_search_disabled_and_guards(tmp_path):
    trigger = SearchTrigger("A1", ("f1",), "reason", "source", ("intent",))
    # 1. Disabled by user
    outcome, b_dir, b_cksum, usage, sources = perform_targeted_search(
        trigger, tmp_path, "exec_1", "TST", "Test Co", "2025-01-31", web_search_enabled=False
    )
    assert outcome == SearchOutcome.DISABLED_BY_USER
    assert b_dir is None

    # 2. Mock mode
    outcome, _, _, _, _ = perform_targeted_search(
        trigger, tmp_path, "exec_1", "TST", "Test Co", "2025-01-31", is_mock=True
    )
    assert outcome == SearchOutcome.NOT_PERFORMED

    # 3. Round limit reached
    outcome, _, _, _, _ = perform_targeted_search(
        trigger, tmp_path, "exec_1", "TST", "Test Co", "2025-01-31", existing_round=1
    )
    assert outcome == SearchOutcome.ROUND_LIMIT_REACHED

    # 4. No credentials
    outcome, _, _, _, _ = perform_targeted_search(
        trigger, tmp_path, "exec_1", "TST", "Test Co", "2025-01-31", api_key=""
    )
    assert outcome == SearchOutcome.NOT_PERFORMED


def test_perform_targeted_search_accepted_and_bundle(tmp_path, monkeypatch):
    import src.targeted_search as ts_module

    # Fake DNS and downloader
    monkeypatch.setattr(ts_module, "_public_url", lambda u, resolve=True: u)
    monkeypatch.setattr(
        ts_module,
        "download_public_document",
        lambda u, max_bytes=512*1024, timeout=15: (_fake_html("2025-01-20"), "text/html", "2025-01-20T10:00:00Z"),
    )

    class FakeBackend:
        model = "gemini-test"
        api_key = "fake-key"
        def research(self, prompt, **kwargs):
            return _fake_interaction(["https://verified.example/doc1"])

    trigger = SearchTrigger("A2", ("PEER_LIST",), "missing peers", "sec", ("find competitors",))
    outcome, b_dir, b_cksum, usage, sources = perform_targeted_search(
        trigger, tmp_path, "exec_test", "TST", "Test Co", "2025-01-31",
        gemini_backend=FakeBackend()
    )
    assert outcome == SearchOutcome.ACCEPTED_SOURCES
    assert b_dir.is_dir()
    assert b_cksum.startswith("sha256:")
    assert len(sources) == 1
    assert sources[0]["source_id"] == "S001"
    assert (b_dir / "search_manifest.json").is_file()
    assert (b_dir / "S001.bytes").is_file()
    assert (b_dir / "S001.txt").is_file()

    # Verify formatted untrusted prompt text
    block = format_untrusted_search_sources(sources, b_dir)
    assert "[S001] Verified source" in block
    assert "Public company disclosure information." in block


def test_perform_targeted_search_excludes_post_cutoff_date(tmp_path, monkeypatch):
    import src.targeted_search as ts_module

    monkeypatch.setattr(ts_module, "_public_url", lambda u, resolve=True: u)
    # Return HTML published on 2025-02-15 (after cutoff 2025-01-31)
    monkeypatch.setattr(
        ts_module,
        "download_public_document",
        lambda u, max_bytes=512*1024, timeout=15: (_fake_html("2025-02-15"), "text/html", "2025-02-15T10:00:00Z"),
    )

    class FakeBackend:
        model = "gemini-test"
        api_key = "fake-key"
        def research(self, prompt, **kwargs):
            return _fake_interaction(["https://future.example/doc"])

    trigger = SearchTrigger("A1", ("f1",), "check", "sec", ("query",))
    outcome, b_dir, b_cksum, usage, sources = perform_targeted_search(
        trigger, tmp_path, "exec_future", "TST", "Test Co", "2025-01-31",
        gemini_backend=FakeBackend()
    )
    assert outcome == SearchOutcome.NO_ACCEPTED_SOURCES
    assert len(sources) == 0
    manifest = json.loads((b_dir / "search_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["excluded_sources"]) == 1
    assert "after the analysis cutoff" in manifest["excluded_sources"][0]["reason"]


def test_verify_search_conflict_detects_ticker_and_price_mismatch():
    from src.envelope import Envelope

    a1 = Envelope.from_dict({
        "schema_version": "2.1",
        "agent": {"id": "A1", "name": "Collector"},
        "params": {"ticker": "AAPL"},
        "artifacts": {"market_cap_check": {"price": 150.0, "shares": 1000.0, "cap": 150000.0}},
    })

    # 1. Ticker changed
    revised_bad_ticker = Envelope.from_dict({
        "schema_version": "2.1",
        "agent": {"id": "A2", "name": "Industry"},
        "params": {"ticker": "MSFT"},
        "artifacts": {},
    })
    conflict = verify_search_conflict(revised_bad_ticker, {"A1": a1})
    assert conflict is not None
    assert "ticker changed" in conflict

    # 2. Price changed from A1
    revised_bad_price = Envelope.from_dict({
        "schema_version": "2.1",
        "agent": {"id": "A2", "name": "Industry"},
        "params": {"ticker": "AAPL"},
        "artifacts": {"market_cap_check": {"price": 180.0, "shares": 1000.0, "cap": 180000.0}},
    })
    conflict = verify_search_conflict(revised_bad_price, {"A1": a1})
    assert conflict is not None
    assert "price value changed" in conflict

    # 3. Clean matching values
    revised_ok = Envelope.from_dict({
        "schema_version": "2.1",
        "agent": {"id": "A2", "name": "Industry"},
        "params": {"ticker": "AAPL"},
        "artifacts": {"market_cap_check": {"price": 150.0, "shares": 1000.0, "cap": 150000.0}},
    })
    assert verify_search_conflict(revised_ok, {"A1": a1}) is None


def test_audit_targeted_search_bundle_tamper_fails(tmp_path):
    run_dir = tmp_path / "run_audit_test"
    run_dir.mkdir()
    bundle_dir = run_dir / "targeted_search" / "exec_audit"
    bundle_dir.mkdir(parents=True)

    manifest_data = {
        "manifest_version": "targeted_search_v1",
        "execution_id": "exec_audit",
        "agent_id": "A2",
        "trigger": {"reason": "test"},
        "provider": "gemini",
        "model": "gemini-test",
        "retrieval_timestamp": "2025-01-31T10:00:00Z",
        "request_count": 1,
        "usage": None,
        "sources": [
            {
                "source_id": "S001",
                "raw_path": "S001.bytes",
                "text_path": "S001.txt",
                "raw_sha256": hashlib.sha256(b"raw").hexdigest(),
                "text_sha256": hashlib.sha256(b"text").hexdigest(),
            }
        ],
        "excluded_sources": [],
        "outcome": "accepted_sources",
    }
    write_search_bundle(bundle_dir, manifest_data)
    (bundle_dir / "S001.bytes").write_bytes(b"raw")
    (bundle_dir / "S001.txt").write_text("text", encoding="utf-8")

    # Manifest discovery includes targeted_search
    manifest = build_run_manifest(
        run_dir,
        {},
        run_id="run_audit_test",
        ticker="TST",
        analysis_date="2025-01-31",
        status="completed",
    )
    assert any("targeted_search" in k for k in manifest["outputs"])

    # Tampering with source bytes fails audit
    (bundle_dir / "S001.bytes").write_bytes(b"corrupted_bytes")
    report = semantic_audit(run_dir)
    assert any("raw bytes missing or corrupted" in err for err in report.errors)
