"""Provider routing and local credential handling without live requests."""

from types import SimpleNamespace
import os
import sys
import traceback

import pytest

from src.agents import model as model_module
from src.agents.registry import default_registry
from src.controller import build_parser
from src import sentry


def test_opencode_uses_project_key_with_explicit_and_environment_precedence(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENCODE_GO_API_KEY=project-key\n", encoding="utf-8")
    monkeypatch.setattr(model_module, "_GEMINI_ENV_PATH", path)
    monkeypatch.setattr(model_module.OpenCodeGoBackend, "_load_local_key", staticmethod(lambda: "opencode-auth-key"))
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)

    assert model_module.OpenCodeGoBackend().api_key == "project-key"
    path.write_text("", encoding="utf-8")
    assert model_module.OpenCodeGoBackend().api_key == "opencode-auth-key"
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "environment-key")
    assert model_module.OpenCodeGoBackend().api_key == "environment-key"
    assert model_module.OpenCodeGoBackend(api_key="explicit-key").api_key == "explicit-key"


def test_opencode_never_uses_a_different_provider_key(monkeypatch, tmp_path):
    monkeypatch.setattr(model_module, "_GEMINI_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setattr(model_module.OpenCodeGoBackend, "_load_local_key", staticmethod(lambda: ""))
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "different-provider-key")

    assert model_module.OpenCodeGoBackend().api_key == ""


def test_openai_default_never_routes_a_generic_key_to_an_unconfigured_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "compatible-key")
    backend = model_module.OpenAICompatibleBackend()
    assert backend.base_url == "https://api.openai.com/v1"
    assert backend.api_key == ""

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert model_module.OpenAICompatibleBackend().api_key == "compatible-key"
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY")
    assert model_module.OpenAICompatibleBackend().api_key == ""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert model_module.OpenAICompatibleBackend().api_key == "openai-key"


def test_opencode_error_does_not_echo_its_key(monkeypatch):
    monkeypatch.setattr(model_module, "requests", SimpleNamespace(
        post=lambda *args, **kwargs: SimpleNamespace(status_code=403, text="Rejected secret-example")
    ))
    backend = model_module.OpenCodeGoBackend(api_key="secret-example", fallback_model=None)
    with pytest.raises(RuntimeError) as error:
        backend.generate("test")
    assert "secret-example" not in str(error.value)


def test_opencode_timeout_does_not_dispatch_another_model(monkeypatch):
    calls = []

    def timeout(backend, prompt):
        calls.append(backend.model)
        raise TimeoutError("outcome unknown")

    monkeypatch.setattr(model_module.OpenAICompatibleBackend, "generate", timeout)
    backend = model_module.OpenCodeGoBackend(api_key="test", model="primary")
    with pytest.raises(TimeoutError):
        backend.generate("test")
    assert calls == ["primary"]
    assert backend.model == "primary"


def test_opencode_rejects_automatic_fallback_configuration():
    with pytest.raises(ValueError, match="automatic fallback"):
        model_module.OpenCodeGoBackend(api_key="test", model="primary", fallback_model="fallback")


def test_typesafe_reads_project_key_without_changing_environment(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("TYPESAFE_API_KEY=project-key\n", encoding="utf-8")
    monkeypatch.setattr(sentry, "_ENV_PATH", path, raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(sentry, "_build_typesafe_questions", lambda _: {})
    used = []

    class Client:
        def __init__(self, api_key):
            used.append(api_key)

        def system_one(self, **kwargs):
            return SimpleNamespace(answers={
                "contradicts_B1": SimpleNamespace(noul=0.1),
                "materiality": SimpleNamespace(choice="noise", probabilities={"noise": 0.9}),
                "recommended_action": SimpleNamespace(choice="ignore", probabilities={"ignore": 0.9}),
            })

    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=Client))
    assert sentry.evaluate_document(tmp_path, "Routine update", provider="auto").provider == "jev"
    assert "TYPESAFE_API_KEY" not in os.environ
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-key")
    assert sentry.evaluate_document(tmp_path, "Routine update", provider="jev").provider == "jev"
    assert sentry.evaluate_document(tmp_path, "Routine update", provider="jev", api_key="explicit-key").provider == "jev"
    assert used == ["project-key", "environment-key", "explicit-key"]


def test_typesafe_error_does_not_echo_its_key(monkeypatch, tmp_path):
    monkeypatch.setattr(sentry, "_build_typesafe_questions", lambda _: {})
    key = "secret-" + "example"

    class Client:
        def __init__(self, api_key):
            pass

        def system_one(self, **kwargs):
            raise ValueError(f"Rejected {key}")

    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=Client))
    with pytest.raises(RuntimeError) as error:
        sentry.evaluate_document(tmp_path, "test", provider="jev", api_key=key)
    assert key not in "".join(traceback.format_exception(error.value))


def test_typesafe_system_one_answers_drive_the_monitor(monkeypatch, tmp_path):
    monkeypatch.setattr(sentry, "_build_typesafe_questions", lambda _: {})

    class Client:
        def __init__(self, api_key):
            pass

        def system_one(self, **kwargs):
            return SimpleNamespace(answers={
                "contradicts_B1": SimpleNamespace(noul=0.88),
                "materiality": SimpleNamespace(choice="critical", confidence=0.3, probabilities={"critical": 0.72}),
                "recommended_action": SimpleNamespace(choice="trigger_deep_rerun", confidence=0.4, probabilities={"trigger_deep_rerun": 0.81}),
            })

    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=Client))
    result = sentry.evaluate_document(tmp_path, "A serious issue", provider="jev", api_key="test-key")
    assert result.contradiction_scores == {"B1": 0.88}
    assert result.materiality == "critical" and result.materiality_prob == 0.72
    assert result.action == "trigger_deep_rerun" and result.action_prob == 0.81
    assert result.requires_rerun


def test_typesafe_missing_answer_is_not_treated_as_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(sentry, "_build_typesafe_questions", lambda _: {})

    class Client:
        def __init__(self, api_key):
            pass

        def system_one(self, **kwargs):
            return SimpleNamespace(answers={})

    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(TypeSafeClient=Client))
    with pytest.raises(RuntimeError):
        sentry.evaluate_document(tmp_path, "test", provider="jev", api_key="test-key")


def test_openrouter_is_not_a_supported_provider(tmp_path):
    with pytest.raises(ValueError):
        default_registry(provider="openrouter")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--ticker", "TEST", "--provider", "openrouter"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["monitor", str(tmp_path), "--doc", "test", "--provider", "openrouter"])
    with pytest.raises(ValueError):
        sentry.evaluate_document(tmp_path, "test", provider="openrouter")


def test_agy_cli_backend_passes_prompt_via_stdin(monkeypatch):
    """AgyCliBackend passes prompt through stdin instead of cmdline arguments."""
    captured = {}

    def mock_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0, stdout="test-output", stderr="")

    monkeypatch.setattr(model_module.subprocess, "run", mock_run)
    monkeypatch.setattr(model_module.os.path, "exists", lambda _: True)

    backend = model_module.AgyCliBackend(model="gemini-3.8-flash-low")
    res = backend.generate("very long prompt content that should be in stdin")
    assert res == "test-output"
    assert captured["input"] == "very long prompt content that should be in stdin"
    assert "--print" in captured["cmd"]
    idx = captured["cmd"].index("--print")
    assert captured["cmd"][idx + 1] == "-"


def test_resolve_env_path_with_env_override(monkeypatch, tmp_path):
    from src.resources import resolve_env_path
    custom_env = tmp_path / "custom.env"
    custom_env.write_text("TEST=1\n")
    monkeypatch.setenv("FUWFORGE_ENV_PATH", str(custom_env))
    assert resolve_env_path() == custom_env
