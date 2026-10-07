"""Offline web-intake coverage. Never reads local keys or performs paid calls."""
import io
import json
import urllib.error
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import controller, evidence, web_research
from src.agents import model as model_module
from src.agents.model import GeminiApiBackend
from src.evidence import EvidencePack
from src.utils.checksum import sha256_digest


@pytest.fixture(autouse=True)
def isolate_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(model_module, "_GEMINI_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setattr(evidence, "_ENV_PATH", tmp_path / "missing.env")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "FUWFORGE_STRICT"):
        monkeypatch.delenv(key, raising=False)


def interaction(urls=("https://news.example/article",)):
    return {"status": "completed", "id": "research_001", "usage": {"input_tokens": 100}, "steps": [
        {"type": "google_search_call", "arguments": {"queries": ["TEST recent news"]}},
        {"type": "google_search_result", "result": [{"search_suggestions": "<p>Search suggestions</p>"}]},
        {"type": "model_output", "content": [{"type": "text", "text": "PROVIDER PROSE IS NOT EVIDENCE. https://uncited.example/fake", "annotations": [
            {"type": "url_citation", "url": url, "title": "Public news", "start_index": 0, "end_index": 10} for url in urls
        ]}]},
    ]}


def page(published="2025-01-30", modified=None):
    metadata = {"@type": "NewsArticle", "datePublished": published}
    if modified:
        metadata["dateModified"] = modified
    return ('<html><script type="application/ld+json">' + json.dumps(metadata) + '</script><p>TEST announced a real source event.</p></html>').encode()


def baseline(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"ticker": "TEST", "documents": [{"url": "https://issuer.example/financials", "doc_date": "2025-01-01"}]}))
    # URL parsing remains active; fake only DNS resolution for synthetic hosts.
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(evidence, "download_public_document", lambda u: (b"Financials 123 shares 100", "text/plain", "2026-10-06T12:00:00Z"))
    return evidence.fetch_evidence(manifest, tmp_path / "base")


def collect(tmp_path, monkeypatch, result=None, content=None, max_sources=4):
    path = baseline(tmp_path, monkeypatch)
    backend = GeminiApiBackend(api_key="fake-offline-key")
    calls, downloads = [], []

    def research(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return result if result is not None else interaction()

    def download(url):
        downloads.append(url)
        return content if content is not None else (page(), "text/html", "2026-10-06T12:00:00Z")

    monkeypatch.setattr(backend, "research", research)
    monkeypatch.setattr(web_research, "download_public_document", download)
    out = web_research.collect_web_sources(ticker="TEST", company="Test Co", analysis_date="2025-01-31", evidence_path=path,
        output_dir=tmp_path / "web", backend=backend, max_sources=max_sources)
    pack = EvidencePack(out, ticker="TEST", analysis_date="2025-01-31")
    trace = json.loads((out.parent / pack.attachments[-1]["file"]).read_text())
    return path, pack, trace, calls, downloads


@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_research_request_stateless_no_schema_or_deep_research(monkeypatch, transport):
    calls = []
    body = interaction()
    if transport == "requests":
        def post(url, *, headers, json, timeout):
            calls.append((url, headers, json))
            return SimpleNamespace(status_code=200, json=lambda: body)
        monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=post))
    else:
        monkeypatch.setattr(model_module, "requests", None)
        def urlopen(req, timeout):
            calls.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
            return io.BytesIO(json.dumps(body).encode())
        monkeypatch.setattr(model_module.urllib.request, "urlopen", urlopen)
    result = GeminiApiBackend(api_key="fake-offline-key").research("Public ticker TEST")
    assert result == body
    assert len(calls) == 1
    url, headers, payload = calls[0]
    assert url.endswith("/v1beta/interactions") and "fake-offline-key" not in url
    assert {k.lower(): v for k, v in headers.items()}["x-goog-api-key"] == "fake-offline-key"
    assert payload["tools"] == [{"type": "google_search"}, {"type": "url_context"}]
    assert payload["store"] is False and payload["background"] is False
    assert payload["generation_config"]["max_output_tokens"] == 4096
    assert "agent" not in payload and "response_format" not in payload


def test_research_error_is_redacted_and_not_retried(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=429, text="secret-offline-key quota exhausted")
    monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=post))
    with pytest.raises(RuntimeError) as error:
        GeminiApiBackend(api_key="secret-offline-key").research("Public query")
    assert len(calls) == 1 and "secret-offline-key" not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_url_context_can_be_disabled_and_echoed_keys_are_removed(monkeypatch):
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs["json"])
        result = interaction()
        result["extra"] = "secret-offline-key"
        return SimpleNamespace(status_code=200, json=lambda: result)
    monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=post))
    result = GeminiApiBackend(api_key="secret-offline-key").research("Public query", url_context=False)
    assert captured[0]["tools"] == [{"type": "google_search"}]
    assert result["extra"] == "[REDACTED]"


@pytest.mark.parametrize("body", [{}, {"status": "in_progress", "steps": []}, {"status": "completed", "steps": {}}])
def test_incomplete_interaction_is_rejected(monkeypatch, body):
    monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: body)))
    with pytest.raises(RuntimeError, match="completed interaction"):
        GeminiApiBackend(api_key="fake-offline-key").research("Public query")


def test_shared_intake_preserves_financials_and_caches_only_source_bytes(tmp_path, monkeypatch):
    path, pack, trace, calls, downloads = collect(tmp_path, monkeypatch)
    old = EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
    assert pack.documents[0] == old.documents[0]
    assert len(calls) == 1 and len(downloads) == 1
    assert "GEMINI_API_KEY" not in calls[0][0] and "SEC_USER_AGENT" not in calls[0][0]
    assert pack.documents[1]["doc_date"] == "2025-01-30"
    assert trace["request_count"] == 1 and trace["paid_retries"] == 0
    assert trace["interaction"]["usage"]["input_tokens"] == 100
    text = pack.prompt_text()
    assert "real source event" in text
    assert "PROVIDER PROSE IS NOT EVIDENCE" not in text
    assert "Search suggestions" not in text and "uncited.example" not in text
    assert pack.checksum != old.checksum
    snapshot = pack.snapshot(tmp_path / "snapshot")
    assert snapshot.checksum == pack.checksum
    assert (snapshot.path.parent / snapshot.attachments[0]["file"]).is_file()
    (snapshot.path.parent / snapshot.attachments[0]["file"]).write_text("tampered")
    with pytest.raises(ValueError, match="attachment checksum"):
        EvidencePack(snapshot.path, ticker="TEST", analysis_date="2025-01-31")


@pytest.mark.parametrize("content,reason", [
    ((page("2025-02-01"), "text/html", "2026-10-06T12:00:00Z"), "cutoff"),
    ((page(modified="2025-02-01"), "text/html", "2026-10-06T12:00:00Z"), "cutoff"),
    ((b"<p>2025-01-30 in prose is not publication metadata</p>", "text/html", "2026-10-06T12:00:00Z"), "missing"),
    ((page("2024-01-01"), "text/html", "2026-10-06T12:00:00Z"), "window"),
    ((b"%PDF-1.0", "application/pdf", "2026-10-06T12:00:00Z"), "HTML"),
    ((page() + b"x" * web_research.MAX_WEB_BYTES, "text/html", "2026-10-06T12:00:00Z"), "512 KiB"),
])
def test_bad_or_undated_pages_stay_gaps(tmp_path, monkeypatch, content, reason):
    _, pack, trace, _, _ = collect(tmp_path, monkeypatch, content=content)
    assert len(pack.documents) == 1 and not trace["accepted"]
    assert reason in trace["excluded"][0]["reason"]
    assert "No additional dated pages" in pack.prompt_text()


@pytest.mark.parametrize("url", ["http://news.example/article", "https://localhost/a", "https://127.0.0.1/a", "https://u:p@news.example/a", "https://news.example/a?api_key=secret"])
def test_unsafe_urls_never_downloaded(tmp_path, monkeypatch, url):
    _, pack, trace, _, downloads = collect(tmp_path, monkeypatch, result=interaction([url]))
    assert not downloads and len(pack.documents) == 1 and trace["excluded"]


def test_candidate_and_accepted_caps(tmp_path, monkeypatch):
    urls = [f"https://news.example/article{i}" for i in range(30)]
    _, pack, trace, calls, downloads = collect(tmp_path, monkeypatch, result=interaction(urls), max_sources=2)
    assert len(calls) == 1 and len(downloads) == len(trace["accepted"]) == 2
    assert len(pack.documents) == 3
    assert len(web_research._citations(interaction(urls))) == 6


def test_duplicates_and_uncited_prose_are_not_downloaded(tmp_path, monkeypatch):
    _, pack, trace, _, downloads = collect(tmp_path, monkeypatch, result=interaction(["https://issuer.example/financials", "https://news.example/article", "https://news.example/article"]))
    assert downloads == ["https://news.example/article"]
    assert len(pack.documents) == 2


def test_ambiguous_publication_dates_rejected():
    payload = page() + b'<meta property="article:published_time" content="2025-01-29">'
    with pytest.raises(ValueError, match="ambiguous"):
        web_research._publication_date(payload, "text/html", date(2025, 1, 31))


def test_search_wrapper_safe_resolution_and_private_target_rejection(monkeypatch):
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", ("93.184.216.34", 443))])
    captured, target = [], ["https://news.example/article"]
    def open_(req, timeout):
        captured.append(req)
        raise urllib.error.HTTPError(req.full_url, 302, "redirect", {"Location": target[0]}, io.BytesIO())
    monkeypatch.setattr(web_research.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=open_))
    wrapper = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    assert web_research._resolve_search_url(wrapper) == target[0]
    assert captured[0].get_header("X-goog-api-key") is None
    assert "@" not in captured[0].get_header("User-agent")
    target[0] = "https://127.0.0.1/private"
    with pytest.raises(ValueError, match="Private IP"):
        web_research._resolve_search_url(wrapper)
    assert len(captured) == 2  # Private target was never probed.


def test_research_preflight_does_not_spend_with_invalid_input(tmp_path, monkeypatch):
    path = baseline(tmp_path, monkeypatch)
    backend = GeminiApiBackend(api_key="fake-offline-key")
    monkeypatch.setattr(backend, "research", lambda *a, **kw: pytest.fail("No paid request allowed"))
    for overrides in ({"max_sources": 0}, {"max_sources": 7}, {"ticker": "bad?key"}, {"company": "bad\nlabel"}, {"analysis_date": "2099-01-01"}):
        params = dict(ticker="TEST", company="Test Co", analysis_date="2025-01-31", evidence_path=path, output_dir=tmp_path / "bad", backend=backend)
        params.update(overrides)
        with pytest.raises(ValueError):
            web_research.collect_web_sources(**params)
    assert not (tmp_path / "bad").exists()
    data = json.loads(path.read_text())
    data["documents"] = [dict(data["documents"][0], id=f"D{i:03d}") for i in range(16)]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="pack is full"):
        web_research.collect_web_sources(ticker="TEST", company="Test Co", analysis_date="2025-01-31", evidence_path=path, output_dir=tmp_path / "bad", backend=backend)
    assert not (tmp_path / "bad").exists()


def test_cli_default_gemini_intake_and_other_providers_remain_opt_out(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(web_research, "collect_web_sources", lambda **kw: seen.append(kw) or kw["evidence_path"])
    class FakeOrchestrator:
        def __init__(self, *a, **kw):
            pass
        def run(self, **kw):
            return {}
    monkeypatch.setattr(controller, "FUWForgeOrchestrator", FakeOrchestrator)
    parser = controller.build_parser()
    base = ["run", str(tmp_path / "run"), "--ticker", "TEST", "--company", "Test Co", "--analysis-date", "2025-01-31", "--evidence-pack", str(tmp_path / "evidence.json")]
    args = parser.parse_args(base + ["--provider", "gemini"])
    args.func(args)
    assert len(seen) == 1 and seen[0]["url_context"] is True and seen[0]["max_sources"] == 4
    seen.clear()
    args = parser.parse_args(base + ["--provider", "gemini", "--no-web-search"])
    args.func(args)
    assert not seen
    args = parser.parse_args(base + ["--provider", "gemini", "--no-url-context", "--web-max-sources", "2"])
    args.func(args)
    assert len(seen) == 1 and seen[0]["url_context"] is False and seen[0]["max_sources"] == 2
    seen.clear()
    args = parser.parse_args(base + ["--provider", "mock"])
    args.func(args)
    assert not seen
    args = parser.parse_args(base + ["--provider", "mock", "--web-search"])
    with pytest.raises(ValueError, match="requires --provider gemini"):
        args.func(args)


def test_cli_missing_evidence_stops_before_research(monkeypatch):
    monkeypatch.setattr(web_research, "collect_web_sources", lambda **kw: pytest.fail("No paid calls without financial evidence"))
    args = controller.build_parser().parse_args(["run", "--ticker", "TEST", "--company", "Test Co", "--provider", "gemini"])
    with pytest.raises(ValueError, match="requires --auto-sources or --evidence-pack"):
        args.func(args)


def test_attachment_cannot_overwrite_document_files(tmp_path, monkeypatch):
    path = baseline(tmp_path, monkeypatch)
    data = json.loads(path.read_text())
    document = data["documents"][0]
    data["attachments"] = [{"file": document["raw_file"], "sha256": document["content_sha256"]}]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unique and separate"):
        EvidencePack(path, ticker="TEST", analysis_date="2025-01-31")
