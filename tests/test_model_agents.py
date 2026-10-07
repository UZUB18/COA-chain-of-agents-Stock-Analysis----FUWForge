"""
Unit and integration tests for ModelAgent and model backends.
Validates:
1. JSON payload extraction across markdown fences and raw text.
2. ModelAgent lifecycle, normalization, checksum matching, retries.
3. OpenAI-compatible backend interface.
4. Live Google AGY CLI execution with cheap model (gemini-3.8-flash-low).
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.model import (
    AgyCliBackend,
    ModelAgent,
    ModelBackend,
    OpenAICompatibleBackend,
    extract_json_payload,
)
from src.envelope import Envelope


class DummyBackend(ModelBackend):
    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    def generate(self, prompt: str) -> str:
        resp = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return resp


def test_extract_json_payload():
    # 1. Clean JSON
    res1 = extract_json_payload('{"key": "value", "num": 42}')
    assert res1 == {"key": "value", "num": 42}

    # 2. Markdown fenced JSON with trailing whitespace on fence lines
    fenced = '```json \n{\n  "status": "success"\n}\n``` '
    res2 = extract_json_payload(fenced)
    assert res2 == {"status": "success"}

    # 3. Leading and trailing chatter with braces in chatter
    chatter = 'Note for {company}: here is the data:\n\n```json\n{"id": "A1", "count": 10,}\n``` \nHope this helps!'
    res3 = extract_json_payload(chatter)
    assert res3 == {"id": "A1", "count": 10}

    # 4. Outermost brace fallback with trailing commas
    raw_embedded = 'Analysis complete. Results: {"data": [1, 2, 3,], "ok": true,} End of transmission.'
    res4 = extract_json_payload(raw_embedded)
    assert res4 == {"data": [1, 2, 3], "ok": True}

    # 5. ANSI escape sequences
    ansi_text = '\x1b[32m```json\n{"color": "green"}\n```\x1b[0m'
    res5 = extract_json_payload(ansi_text)
    assert res5 == {"color": "green"}

    # 6. Multiple blocks: pick the one with schema_version / agent
    multi_block = """Here is a dummy schema:
```json
{"dummy": true}
```
And here is the actual envelope:
```json
{
  "schema_version": "2.1",
  "agent": {"id": "A1", "name": "Harvester"}
}
```"""
    res6 = extract_json_payload(multi_block)
    assert res6["schema_version"] == "2.1"
    assert res6["agent"]["id"] == "A1"

    # 7. Invalid JSON throws ValueError
    try:
        extract_json_payload("No json here whatsoever.")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    print("  [PASS] test_extract_json_payload")


def test_model_agent_a7_scorecard_and_sections_normalization():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_norm_test_"))
    try:
        context = AgentContext(
            run_id="a7_norm_test",
            params={"ticker": "AAPL", "company": "Apple Inc."},
            depth_config={},
            focus_overrides={},
            frozen_checksum="sha256:normchecksum",
            workdir=temp_dir,
            provenance_path=temp_dir / "test.log",
        )

        # Non-string sections_md and partial raw scorecard
        raw_output = json.dumps(
            {
                "schema_version": "2.1",
                "agent": {"id": "A7", "name": "Final-Assembly"},
                "sections_md": {
                    "SECTION_0_EXEC_SUMMARY": [
                        "# Executive Summary",
                        "Key point 1",
                        "Key point 2",
                    ],
                    "SECTION_13_IPO_HISTORY": "# Section 13 Capital History",
                    "SECTION_15_SWOT": "# Section 15 SWOT",
                    "SECTION_16_SCORECARD": "# Section 16 Scorecard",
                    "SECTION_17_GLOSSARY": "# Section 17 Glossary",
                    "SECTION_18_LAY_SUMMARY": "# Section 18 Lay Summary",
                },
                "artifacts": {
                    "Scorecard": {
                        "Financials": {"score": 9.0},
                        "Moat": 8.0,
                        "Growth": 7.0,
                        "Valuation": 6.0,
                        "MgmtGov": 8.0,
                        "RiskInverse": 8.0,
                        "ESG": 7.0,
                        "Market": 7.0,
                    }
                },
                "confidence": "0.98",
                "metrics": {
                    "latency_ms": "120",
                    "tokens": "450",
                },
            }
        )

        backend = DummyBackend([raw_output])
        agent = ModelAgent(agent_id="A7", name="Final-Assembly", backend=backend)
        output = agent.run(context)
        env = output.envelope

        # Sections must be strings
        assert isinstance(env.sections_md["SECTION_0_EXEC_SUMMARY"], str)
        assert "Key point 1" in env.sections_md["SECTION_0_EXEC_SUMMARY"]

        # Scorecard math must be complete and accurate
        sc = env.artifacts["Scorecard"]
        for cat in [
            "Financials",
            "Moat",
            "Growth",
            "Valuation",
            "MgmtGov",
            "RiskInverse",
            "ESG",
            "Market",
        ]:
            assert cat in sc
            assert "score" in sc[cat]
            assert "weight" in sc[cat]
            assert "weighted" in sc[cat]
            assert (
                abs(sc[cat]["score"] * sc[cat]["weight"] - sc[cat]["weighted"]) < 0.001
            )

        expected_total = sum(sc[cat]["weighted"] for cat in sc if cat != "Overall")
        assert abs(sc["Overall"]["score"] - round(expected_total, 2)) < 0.01
        assert sc["Overall"]["rating_band"] == "Buy"

        # Types of metrics and confidence
        assert isinstance(env.confidence, float)
        assert env.confidence == 0.98
        assert isinstance(env.metrics["latency_ms"], float)
        assert isinstance(env.metrics["tokens"], float)

        print("  [PASS] test_model_agent_a7_scorecard_and_sections_normalization")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_model_agent_records_injected_defaults():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_defaults_test_"))
    try:
        context = AgentContext(
            run_id="defaults_test",
            params={"ticker": "AAPL", "company": "Apple Inc."},
            depth_config={},
            focus_overrides={},
            frozen_checksum="sha256:defaultschecksum",
            workdir=temp_dir,
            provenance_path=temp_dir / "test.log",
        )
        minimal = json.dumps(
            {
                "schema_version": "2.1",
                "agent": {"id": "A2", "name": "Business-Market-Peers"},
                "sections_md": {
                    "SECTION_1_COMPANY_PROFILE": "# Profile",
                    "SECTION_2_BUSINESS_MODEL": "# Model",
                    "SECTION_3_MARKET_AND_PEERS": "# Peers",
                },
                "artifacts": {},
                "metrics": {},
            }
        )
        backend = DummyBackend([minimal])
        agent = ModelAgent(agent_id="A2", name="Business-Market-Peers", backend=backend)
        envelope = agent.run(context).envelope

        assert envelope.metrics["injected_defaults"], (
            "injected defaults must be recorded"
        )
        assert any("injected" in warning.lower() for warning in envelope.warnings)

        # Strict mode makes injected defaults fatal.
        strict_agent = ModelAgent(
            agent_id="A2",
            name="Business-Market-Peers",
            backend=DummyBackend([minimal]),
            max_retries=0,
            strict=True,
        )
        try:
            strict_agent.run(context)
            raise AssertionError("strict mode should reject injected defaults")
        except RuntimeError as exc:
            assert "strict mode" in str(exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_model_agent_normalization_and_retry():
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_model_test_"))
    try:
        prov_path = temp_dir / "test_prov.log"
        context = AgentContext(
            run_id="model_test_01",
            params={"ticker": "NVDA", "company": "NVIDIA Corp"},
            depth_config={},
            focus_overrides={},
            frozen_checksum="sha256:fixedchecksum12345",
            workdir=temp_dir,
            provenance_path=prov_path,
        )

        # First attempt: invalid text; Second attempt: valid partial json
        bad_response = "I am an AI, I cannot produce JSON right now."
        valid_response = json.dumps(
            {
                "schema_version": "2.1",
                "agent": {"id": "A6B", "name": "Bull-Case"},
                "sections_md": {
                    "SECTION_10_BULL_CASE": "### Bull Theses\n1. AI accelerator leadership."
                },
                "artifacts": {
                    "BullCase": [
                        {
                            "id": "B1",
                            "thesis": "Datacenter revenue growth",
                            "evidence": ["A3.AltmanZ"],
                            "impact": "EPS +20%",
                        }
                    ]
                },
            }
        )

        backend = DummyBackend([bad_response, valid_response])
        agent = ModelAgent(
            agent_id="A6B",
            name="Bull-Case",
            backend=backend,
            max_retries=2,
        )

        output = agent.run(context)
        assert backend.call_count == 2, (
            f"Expected 2 calls due to retry, got {backend.call_count}"
        )
        envelope = output.envelope

        # Verify normalization
        assert envelope.agent.id == "A6B"
        assert envelope.schema_version == "2.1"
        assert envelope.timestamp is not None
        assert envelope.gates["passed"] is True
        assert envelope.metrics["data_checksum"] == "sha256:fixedchecksum12345"
        assert len(envelope.sources) >= 1
        assert "SECTION_10_BULL_CASE" in envelope.sections_md
        assert len(envelope.artifacts["BullCase"]) == 1

        print("  [PASS] test_model_agent_normalization_and_retry")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_openrouter_and_opencode_backends():
    from src.agents.model import OpenCodeGoBackend, OpenRouterBackend
    from src.agents.registry import default_registry

    # 1. OpenRouter initialization
    router = OpenRouterBackend(api_key="dummy_key", model="google/gemini-2.0-flash-001")
    assert router.base_url == "https://openrouter.ai/api/v1"
    assert router.extra_headers["X-Title"] == "FUWForge"

    # 2. OpenCode Go initialization & model prefix strip
    go = OpenCodeGoBackend(api_key="dummy_go_key", model="opencode-go/glm-5.3-flash")
    assert go.base_url == "https://opencode.ai/zen/go/v1"
    assert go.model == "glm-5.3-flash"
    assert "x-opencode-session" in go.extra_headers

    # 3. Registry resolution
    reg_go = default_registry(provider="opencode")
    assert reg_go.provider == "opencode"
    reg_router = default_registry(provider="openrouter")
    assert reg_router.provider == "openrouter"


@pytest.mark.live
def test_live_agy_cli_execution():
    """
    Live test dispatching a contract prompt via the Google AGY CLI using gemini-3.8-flash-low.

    Excluded from the default test run (see pyproject addopts); run with
    ``pytest -m live`` when the AGY CLI and network access are available.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="fuwforge_live_agy_"))
    try:
        backend = AgyCliBackend(model="gemini-3.8-flash-low", timeout_seconds=60)
        agent = ModelAgent(
            agent_id="A6B",
            name="Bull-Case",
            backend=backend,
            max_retries=1,
        )

        context = AgentContext(
            run_id="live_agy_run",
            params={"ticker": "AAPL", "company": "Apple Inc."},
            depth_config={},
            focus_overrides={},
            frozen_checksum="sha256:livechecksum789",
            workdir=temp_dir,
            provenance_path=temp_dir / "live_prov.log",
        )

        print("    Dispatching live prompt to AGY CLI (gemini-3.8-flash-low)...")
        output = agent.run(context)
        envelope = output.envelope

        assert envelope.agent.id == "A6B"
        assert envelope.schema_version == "2.1"
        assert envelope.metrics["data_checksum"] == "sha256:livechecksum789"
        assert "SECTION_10_BULL_CASE" in envelope.sections_md
        assert "BullCase" in envelope.artifacts
        assert len(envelope.artifacts["BullCase"]) >= 1

        print(
            f"    Live response successfully validated! Bull theses count: {len(envelope.artifacts['BullCase'])}"
        )
        print("  [PASS] test_live_agy_cli_execution")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("Running FUWForge ModelAgent & backend tests...")
    test_extract_json_payload()
    test_model_agent_normalization_and_retry()
    test_live_agy_cli_execution()
    print("ALL MODEL AGENT TESTS PASSED!")
