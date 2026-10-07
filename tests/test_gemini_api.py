"""Offline coverage for direct Google AI Studio / Gemini API integration."""

import io
import json
import re
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import model as model_module
from src.agents.model import GeminiApiBackend
from src.agents.registry import default_registry
from src.controller import build_parser
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.run_audit import verify_run
from src.sentry import evaluate_document

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # Never read the real project credential in offline tests.
    monkeypatch.setattr(model_module, "_GEMINI_ENV_PATH", tmp_path / ".env")
    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "FUWFORGE_MODEL",
        "FUWFORGE_PROVIDER", "FUWFORGE_STRICT", "TYPESAFE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def response(text='{"ok":true}', reason="STOP"):
    return {
        "candidates": [{
            "finishReason": reason,
            "content": {"parts": [
                {"text": "Private reasoning, not JSON", "thought": True},
                {"text": text[:len(text) // 2]},
                {"text": text[len(text) // 2:]},
            ]},
        }],
    }


def mock_transport(monkeypatch, transport, data, status=200, body="", calls=None):
    calls = [] if calls is None else calls

    if transport == "requests":
        def post(url, *, headers, json, timeout):
            calls.append((url, headers, json, timeout))
            return SimpleNamespace(status_code=status, text=body, json=lambda: data)
        monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=post))
    else:
        monkeypatch.setattr(model_module, "requests", None)

        def urlopen(req, *, timeout):
            calls.append((req.full_url, dict(req.header_items()), json.loads(req.data), timeout))
            if status != 200:
                raise urllib.error.HTTPError(
                    req.full_url, status, "API error", {}, io.BytesIO(body.encode())
                )
            return io.BytesIO(json.dumps(data).encode())
        monkeypatch.setattr(model_module.urllib.request, "urlopen", urlopen)
    return calls


def test_local_project_config_loads_without_environment_mutation(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text('GEMINI_API_KEY="local$literal-key"\nGEMINI_MODEL=models/gemini-2.5-pro\n')
    backend = GeminiApiBackend()
    assert backend.api_key == "local$literal-key"
    assert backend.model == "gemini-2.5-pro"
    assert "GEMINI_API_KEY" not in model_module.os.environ
    assert default_registry(provider="gemini").resolve("A1").backend.api_key == "local$literal-key"
    monkeypatch.setenv("GOOGLE_API_KEY", "shell-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    assert GeminiApiBackend().api_key == "shell-key"
    assert GeminiApiBackend().model == "gemini-3.8-flash"
    assert GeminiApiBackend(api_key="explicit-key", model="gemini-3.1-pro-preview").api_key == "explicit-key"
    assert GeminiApiBackend(model="gemini-3.1-pro-preview").model == "gemini-3.1-pro-preview"


def test_local_google_key_alias(tmp_path):
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=local-google-key\n")
    assert GeminiApiBackend().api_key == "local-google-key"


def test_cwd_and_parent_credentials_are_not_loaded(monkeypatch, tmp_path):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / ".env").write_text("GEMINI_API_KEY=wrong-key\n")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=parent-key\n")
    monkeypatch.chdir(unrelated)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(model_module, "_GEMINI_ENV_PATH", project / ".env")
    assert GeminiApiBackend().api_key == ""


def test_monitor_loads_project_config(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=local-key\nGEMINI_MODEL=gemini-2.5-pro\n")
    calls = mock_transport(monkeypatch, "urllib", response(json.dumps({
        "materiality": "noise", "action": "ignore", "contradiction_scores": {"B1": 0.05},
    })))
    result = evaluate_document(tmp_path, "Routine update", provider="gemini")
    assert result.provider == "gemini (gemini-2.5-pro)"
    assert {key.lower(): value for key, value in calls[0][1].items()}["x-goog-api-key"] == "local-key"
    assert evaluate_document(tmp_path, "Routine update", provider="auto").provider == "mock"


def test_key_and_model_precedence(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    assert GeminiApiBackend().api_key == "google-key"
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    assert GeminiApiBackend().api_key == "gemini-key"
    assert GeminiApiBackend(api_key="explicit-key").api_key == "explicit-key"
    assert GeminiApiBackend().model == "gemini-3.8-flash"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    assert GeminiApiBackend().model == "gemini-2.5-pro"
    assert GeminiApiBackend(model="models/gemini-3.1-pro-preview").model == "gemini-3.1-pro-preview"


@pytest.mark.parametrize("model", ["google/gemini-3.8-flash", "models/", "bad?key=value", "../other"])
def test_invalid_model_ids(model):
    with pytest.raises(ValueError, match="Gemini API model ID"):
        GeminiApiBackend(model=model)


def test_missing_key_fails_without_network(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Missing key must not make a network request")
    monkeypatch.setattr(model_module.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=no_network))
    with pytest.raises(ValueError, match="Google AI Studio API key"):
        GeminiApiBackend().generate("hello")


@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_request_contract_and_multipart_text(monkeypatch, transport):
    calls = mock_transport(monkeypatch, transport, response())
    backend = GeminiApiBackend(api_key="secret-key", model="models/gemini-3.8-flash", timeout_seconds=42)
    assert backend.generate("Analyze this filing") == '{"ok":true}'
    url, headers, payload, timeout = calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert "secret-key" not in url
    assert {key.lower(): value for key, value in headers.items()}["x-goog-api-key"] == "secret-key"
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "Analyze this filing"}]}]
    assert payload["generationConfig"] == {"responseMimeType": "application/json"}
    assert timeout == 42


@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_native_envelope_schema_is_sent_per_request(monkeypatch, transport):
    calls = mock_transport(monkeypatch, transport, response())
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    backend = GeminiApiBackend(api_key="dummy")
    assert backend.generate_envelope("Return ok", schema) == '{"ok":true}'
    assert calls[0][2]["generationConfig"]["responseJsonSchema"] == schema
    backend.generate("Plain JSON prompt")
    assert "responseJsonSchema" not in calls[1][2]["generationConfig"]


@pytest.mark.parametrize("transport", ["requests", "urllib"])
@pytest.mark.parametrize("status,hint", [(400, "configuration"), (401, "API key"), (403, "permissions"), (404, "model"), (429, "quota"), (503, "availability")])
def test_http_errors_are_actionable_and_redacted(monkeypatch, transport, status, hint):
    key = "private-key-long-enough-to-cross-the-cutoff"
    mock_transport(monkeypatch, transport, {}, status, "x" * 280 + key)
    with pytest.raises(RuntimeError) as raised:
        GeminiApiBackend(api_key=key).generate("hello")
    error = str(raised.value)
    assert str(status) in error and hint in error
    assert key not in error and "private-key" not in error
    assert "[REDACTED]" in error
    assert raised.value.__suppress_context__ or raised.value.__context__ is None


@pytest.mark.parametrize("data,message", [
    ([], "invalid response"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked the prompt"),
    ({}, "no response candidates"),
    (response(reason="MAX_TOKENS"), "truncated"),
    (response(reason="SAFETY"), "did not complete"),
    ({"candidates": [{"content": {"parts": [{"text": "thought", "thought": True}]}}]}, "no usable text"),
    ({"candidates": [{"content": {"parts": [{"inlineData": {}}]}}]}, "no usable text"),
])
@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_unusable_responses_fail(monkeypatch, transport, data, message):
    mock_transport(monkeypatch, transport, data)
    with pytest.raises(RuntimeError, match=message):
        GeminiApiBackend(api_key="dummy").generate("hello")


@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_transport_failures_do_not_leak_keys(monkeypatch, transport):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("secret-key")
    if transport == "requests":
        monkeypatch.setattr(model_module, "requests", SimpleNamespace(post=fail))
    else:
        monkeypatch.setattr(model_module, "requests", None)
        monkeypatch.setattr(model_module.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="transport failed") as raised:
        GeminiApiBackend(api_key="secret-key").generate("hello")
    assert "secret-key" not in str(raised.value)
    assert raised.value.__suppress_context__


@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_invalid_json_response(monkeypatch, transport):
    if transport == "requests":
        def invalid_json():
            raise ValueError("secret-key")
        monkeypatch.setattr(model_module, "requests", SimpleNamespace(
            post=lambda *args, **kwargs: SimpleNamespace(status_code=200, json=invalid_json)
        ))
    else:
        monkeypatch.setattr(model_module, "requests", None)
        monkeypatch.setattr(model_module.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"not-json"))
    with pytest.raises(RuntimeError, match="invalid JSON") as raised:
        GeminiApiBackend(api_key="secret-key").generate("hello")
    assert "secret-key" not in str(raised.value)


def test_registry_uses_gemini_for_every_agent_and_model_overrides(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    registry = default_registry(provider="gemini", api_key="dummy")
    assert registry.provider == "gemini"
    agents = [registry.resolve(aid) for aid in registry.known_agents()]
    assert len(agents) == 9
    assert all(isinstance(agent.backend, GeminiApiBackend) for agent in agents)
    assert all(agent.backend.model == "gemini-2.5-pro" for agent in agents)
    monkeypatch.setenv("FUWFORGE_MODEL", "gemini-3.8-flash")
    assert default_registry(provider="gemini").resolve("A1").backend.model == "gemini-3.8-flash"
    assert default_registry(provider="gemini", model="gemini-3.1-pro-preview").resolve("A1").backend.model == "gemini-3.1-pro-preview"
    monkeypatch.setenv("FUWFORGE_PROVIDER", "gemini")
    assert default_registry().provider == "gemini"


def test_monitor_gemini_through_cli(monkeypatch, tmp_path, capsys):
    (tmp_path / "a6b_envelope.json").write_text(json.dumps({
        "params": {"ticker": "ACME", "company": "Acme"},
        "artifacts": {"BullCase": [{"id": "B1", "thesis": "Cloud revenue growth"}]},
    }))
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    calls = mock_transport(monkeypatch, "urllib", response(json.dumps({
        "materiality": "critical", "materiality_prob": 0.95,
        "action": "trigger_deep_rerun", "action_prob": 0.92,
        "contradiction_scores": {"B1": 0.96},
    })))
    args = build_parser().parse_args([
        "monitor", str(tmp_path), "--doc", "Cloud revenue collapsed.",
        "--provider", "gemini", "--model", "gemini-3.1-pro-preview", "--json",
    ])
    with pytest.raises(SystemExit) as raised:
        args.func(args)
    assert raised.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["provider"] == "gemini (gemini-3.1-pro-preview)"
    assert result["contradiction_scores"] == {"B1": 0.96}
    assert result["threatened_theses"][0]["id"] == "B1"
    assert "gemini-3.1-pro-preview:generateContent" in calls[0][0]


def test_monitor_gemini_env_default_and_no_silent_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    mock_transport(monkeypatch, "urllib", response(json.dumps({
        "materiality": "noise", "action": "ignore", "contradiction_scores": {"B1": 0.05},
    })))
    result = evaluate_document(tmp_path, "Routine update", provider="gemini")
    assert result.provider == "gemini (gemini-2.5-pro)"
    assert not result.requires_rerun
    mock_transport(monkeypatch, "urllib", {}, status=429, body="Quota exhausted")
    with pytest.raises(RuntimeError, match="quota"):
        evaluate_document(tmp_path, "Routine update", provider="gemini")
    # A key alone must not turn offline defaults into paid calls.
    assert evaluate_document(tmp_path, "Routine update", provider="auto").provider == "mock"
    assert build_parser().parse_args(["run", "--ticker", "ACME"]).provider == "mock"


def test_gemini_pipeline_replays_offline_envelopes(monkeypatch, tmp_path):
    """Exercise all nine model agents, parallel calls and integrity validation."""
    params = OrchestratorParams(
        company="Acme Cloud", ticker="ACME", analysis_date="2026-09-15",
        depth="Standard", focus=[], weights="default",
    )
    reference_dir = tmp_path / "reference"
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock").run(
        run_id="reference", params=params, mode="automated", output_root=reference_dir,
    )
    envelopes = {}
    for path in reference_dir.glob("a*_envelope.json"):
        envelope = json.loads(path.read_text())
        envelope.pop("content_checksum", None)
        envelope.pop("upstream_checksums", None)
        envelopes[envelope["agent"]["id"]] = envelope

    calls = []
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def urlopen(req, *, timeout):
        prompt = json.loads(req.data)["contents"][0]["parts"][0]["text"]
        agent_id = re.search(r"You are Agent (A\d[A-Z]?)", prompt).group(1)
        calls.append(agent_id)
        assert "dummy" not in req.full_url
        return io.BytesIO(json.dumps(response(json.dumps(envelopes[agent_id]))).encode())

    monkeypatch.setattr(model_module, "requests", None)
    monkeypatch.setattr(model_module.urllib.request, "urlopen", urlopen)
    output_dir = tmp_path / "gemini"
    summary = FUWForgeOrchestrator(PROJECT_ROOT, provider="gemini").run(
        run_id="gemini", params=params, mode="automated", output_root=output_dir,
    )
    assert sorted(calls) == sorted(envelopes)
    assert all(metric["status"] == "passed" for metric in summary["metrics"].values())
    assert Path(summary["outputs"]["report"]).is_file()
    audit = verify_run(output_dir, strict=True)
    assert audit.valid, audit.errors
