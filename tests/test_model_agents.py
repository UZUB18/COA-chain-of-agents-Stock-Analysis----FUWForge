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


@pytest.mark.parametrize("agent_id", ["A2", "A6", "A6R", "A7"])
def test_prompt_is_stable_when_upstream_dict_keys_are_reordered(tmp_path, agent_id):
    upstream = {
        "A3": {"AltmanZ": {"score": 1.0, "classification": "x"},
               "BeneishM": {"score": -2.0, "indices": {"sgai": 1.0, "dsri": 1.1}},
               "ordered_values": ["first", "second"]},
        "A5": {"SentimentSplit": {"pos": 0.4, "neg": 0.2},
               "InsiderNet12m": {"direction": "NetBuy", "value": 10}},
    }
    context = AgentContext(
        run_id="prompt-order", params={"ticker": "TEST", "company": "Test", "weights": "default"},
        depth_config={}, focus_overrides={}, frozen_checksum="sha256:fixed", workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        envelopes={key: Envelope.from_dict({"agent": {"id": key}, "artifacts": value})
                   for key, value in upstream.items()},
        challenge_initial={"phase": "reconciliation", "envelope": {"z": 1, "a": 2}},
        forensic_inputs={"indices": {"dsri": 1.1}, "periods": {"z": 1, "a": 2}},
    )
    agent = ModelAgent(agent_id, "Test", DummyBackend([]))
    original = agent.build_prompt(context)
    context.envelopes = {
        key: Envelope.from_dict(json.loads(json.dumps(env.to_dict(), sort_keys=True)))
        for key, env in reversed(list(context.envelopes.items()))
    }
    context.challenge_initial = json.loads(json.dumps(context.challenge_initial, sort_keys=True))
    context.forensic_inputs = json.loads(json.dumps(context.forensic_inputs, sort_keys=True))
    assert agent.build_prompt(context) == original
    context.envelopes["A3"].artifacts["ordered_values"].reverse()
    assert agent.build_prompt(context) != original
    context.envelopes["A3"].artifacts["ordered_values"].reverse()
    context.envelopes["A3"].artifacts["AltmanZ"]["score"] = 2.0
    assert agent.build_prompt(context) != original


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
        assert env.metrics["tokens"] is None  # The model's own token claim is not provider usage.

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


def test_opencode_backend():
    from src.agents.model import OpenCodeGoBackend
    from src.agents.registry import default_registry

    go = OpenCodeGoBackend(api_key="dummy_go_key", model="opencode-go/glm-5.3-flash")
    assert go.base_url == "https://opencode.ai/zen/go/v1"
    assert go.model == "glm-5.3-flash"
    assert "x-opencode-session" in go.extra_headers

    reg_go = default_registry(provider="opencode")
    assert reg_go.provider == "opencode"


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


def test_model_agent_a3_altman_z_sanitization(tmp_path):
    backend = DummyBackend([])
    agent = ModelAgent("A3", "Quant-Valuation-Technicals", backend=backend, strict=False)
    context = AgentContext(
        run_id="test_altman_sanitization",
        params={"ticker": "AAPL", "company": "Apple Inc.", "analysis_date": "2025-01-31"},
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:testchecksum",
        workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
    )

    base_payload = {
        "schema_version": "2.1",
        "agent": {"id": "A3", "name": "Quant-Valuation-Technicals"},
        "timestamp": "2025-01-31T16:00:00Z",
        "params": {"ticker": "AAPL"},
        "gates": {"passed": True, "notes": []},
        "sections_md": {
            "SECTION_4_FINANCIAL_FORENSICS": "Forensics",
            "SECTION_5_VALUATION": "Valuation",
            "SECTION_12_TECHNICALS": "Technicals",
        },
        "artifacts": {
            "BeneishM": {"score": -2.5, "classification": "Unlikely Manipulator"},
            "OCF_vs_NI_Flag": "Aligned",
            "PEG_TTM": 1.2,
            "PEG_Fwd": 1.1,
            "HypeFlag": "Reality",
            "PeerTable": [],
            "TechnicalSet": {"SMA_50": 100.0, "SMA_200": 95.0, "RSI_14": 55.0},
        },
        "sources": [],
        "warnings": [],
        "metrics": {},
        "confidence": 0.9,
    }

    # 1. Missing AltmanZ -> Indeterminate, feasible=False, no 3.8 Safe Zone hallucination
    payload1 = json.loads(json.dumps(base_payload))
    norm1 = agent._normalize_envelope(payload1, context)
    az1 = norm1["artifacts"]["AltmanZ"]
    assert az1["score"] is None
    assert az1["classification"] == "Indeterminate"
    assert az1["feasible"] is False
    assert az1["unavailable_reason"] == "No Altman Z score was supplied."
    assert "AltmanZ" in norm1["metrics"]["injected_defaults"]

    # 2. Non-numeric string AltmanZ -> Indeterminate
    payload2 = json.loads(json.dumps(base_payload))
    payload2["artifacts"]["AltmanZ"] = "unparseable_string"
    norm2 = agent._normalize_envelope(payload2, context)
    az2 = norm2["artifacts"]["AltmanZ"]
    assert az2["score"] is None
    assert az2["classification"] == "Indeterminate"
    assert az2["feasible"] is False
    assert az2["unavailable_reason"] == "No Altman Z score was supplied."

    # 3. Dict with score None or unparseable score -> Indeterminate
    payload3 = json.loads(json.dumps(base_payload))
    payload3["artifacts"]["AltmanZ"] = {"score": None}
    norm3 = agent._normalize_envelope(payload3, context)
    az3 = norm3["artifacts"]["AltmanZ"]
    assert az3["score"] is None
    assert az3["classification"] == "Indeterminate"
    assert az3["feasible"] is False
    assert az3["unavailable_reason"] == "No Altman Z score was supplied."

    # 4. Custom unavailable reason preserved
    payload4 = json.loads(json.dumps(base_payload))
    payload4["artifacts"]["AltmanZ"] = {
        "score": None,
        "feasible": False,
        "unavailable_reason": "Incomplete balance sheet for financial services firm.",
    }
    norm4 = agent._normalize_envelope(payload4, context)
    az4 = norm4["artifacts"]["AltmanZ"]
    assert az4["score"] is None
    assert az4["classification"] == "Indeterminate"
    assert az4["feasible"] is False
    assert az4["unavailable_reason"] == "Incomplete balance sheet for financial services firm."

    # 5. Dict with numeric score properly classified
    # > 2.99 -> Safe Zone
    payload5a = json.loads(json.dumps(base_payload))
    payload5a["artifacts"]["AltmanZ"] = {"score": 3.85}
    norm5a = agent._normalize_envelope(payload5a, context)
    assert norm5a["artifacts"]["AltmanZ"]["score"] == 3.85
    assert norm5a["artifacts"]["AltmanZ"]["classification"] == "Safe Zone"
    assert norm5a["artifacts"]["AltmanZ"]["feasible"] is True

    # >= 1.81 and <= 2.99 -> Grey Zone
    payload5b = json.loads(json.dumps(base_payload))
    payload5b["artifacts"]["AltmanZ"] = {"score": 2.10}
    norm5b = agent._normalize_envelope(payload5b, context)
    assert norm5b["artifacts"]["AltmanZ"]["score"] == 2.10
    assert norm5b["artifacts"]["AltmanZ"]["classification"] == "Grey Zone"
    assert norm5b["artifacts"]["AltmanZ"]["feasible"] is True

    # < 1.81 -> Distress Zone
    payload5c = json.loads(json.dumps(base_payload))
    payload5c["artifacts"]["AltmanZ"] = {"score": 1.25}
    norm5c = agent._normalize_envelope(payload5c, context)
    assert norm5c["artifacts"]["AltmanZ"]["score"] == 1.25
    assert norm5c["artifacts"]["AltmanZ"]["classification"] == "Distress Zone"
    assert norm5c["artifacts"]["AltmanZ"]["feasible"] is True

    # 6. Scalar numbers properly parsed and classified
    payload6a = json.loads(json.dumps(base_payload))
    payload6a["artifacts"]["AltmanZ"] = 4.2
    norm6a = agent._normalize_envelope(payload6a, context)
    assert norm6a["artifacts"]["AltmanZ"]["score"] == 4.2
    assert norm6a["artifacts"]["AltmanZ"]["classification"] == "Safe Zone"

    payload6b = json.loads(json.dumps(base_payload))
    payload6b["artifacts"]["AltmanZ"] = "1.50"
    norm6b = agent._normalize_envelope(payload6b, context)
    assert norm6b["artifacts"]["AltmanZ"]["score"] == 1.50
    assert norm6b["artifacts"]["AltmanZ"]["classification"] == "Distress Zone"

    # 7. Exact boundary classifications
    # 2.99 is not > 2.99, but is >= 1.81 -> Grey Zone
    payload7a = json.loads(json.dumps(base_payload))
    payload7a["artifacts"]["AltmanZ"] = {"score": 2.99}
    norm7a = agent._normalize_envelope(payload7a, context)
    assert norm7a["artifacts"]["AltmanZ"]["classification"] == "Grey Zone"

    # 1.81 is >= 1.81 -> Grey Zone
    payload7b = json.loads(json.dumps(base_payload))
    payload7b["artifacts"]["AltmanZ"] = {"score": 1.81}
    norm7b = agent._normalize_envelope(payload7b, context)
    assert norm7b["artifacts"]["AltmanZ"]["classification"] == "Grey Zone"

    # 1.80 is < 1.81 -> Distress Zone
    payload7c = json.loads(json.dumps(base_payload))
    payload7c["artifacts"]["AltmanZ"] = {"score": 1.80}
    norm7c = agent._normalize_envelope(payload7c, context)
    assert norm7c["artifacts"]["AltmanZ"]["classification"] == "Distress Zone"

    # 8. NaN / Inf scores -> Indeterminate
    payload8a = json.loads(json.dumps(base_payload))
    payload8a["artifacts"]["AltmanZ"] = {"score": float("nan")}
    norm8a = agent._normalize_envelope(payload8a, context)
    assert norm8a["artifacts"]["AltmanZ"]["score"] is None
    assert norm8a["artifacts"]["AltmanZ"]["classification"] == "Indeterminate"
    assert norm8a["artifacts"]["AltmanZ"]["feasible"] is False

    # 9. Clean up leftover unavailable_reason when score is valid
    payload9 = json.loads(json.dumps(base_payload))
    payload9["artifacts"]["AltmanZ"] = {"score": 3.5, "unavailable_reason": "Old note"}
    norm9 = agent._normalize_envelope(payload9, context)
    assert "unavailable_reason" not in norm9["artifacts"]["AltmanZ"]
    assert norm9["artifacts"]["AltmanZ"]["classification"] == "Safe Zone"

    # 10. Whitespace-only unavailable_reason falls back to standard text
    payload10 = json.loads(json.dumps(base_payload))
    payload10["artifacts"]["AltmanZ"] = {"score": None, "unavailable_reason": "   "}
    norm10 = agent._normalize_envelope(payload10, context)
    assert norm10["artifacts"]["AltmanZ"]["unavailable_reason"] == "No Altman Z score was supplied."

    # 11. Preserves model-provided inputs dictionary
    payload11 = json.loads(json.dumps(base_payload))
    payload11["artifacts"]["AltmanZ"] = {"score": 3.2, "inputs": {"X1": 0.4, "X2": 0.2}}
    norm11 = agent._normalize_envelope(payload11, context)
    assert norm11["artifacts"]["AltmanZ"]["inputs"] == {"X1": 0.4, "X2": 0.2}


if __name__ == "__main__":
    print("Running FUWForge ModelAgent & backend tests...")
    test_extract_json_payload()
    test_model_agent_normalization_and_retry()
    test_live_agy_cli_execution()
    print("ALL MODEL AGENT TESTS PASSED!")


def test_source_coverage_directive_is_a_runner_directive_only_when_gaps_exist(tmp_path):
    context = AgentContext(
        run_id="coverage", params={"ticker": "TEST", "company": "Test", "weights": "default"},
        depth_config={}, focus_overrides={}, frozen_checksum="sha256:fixed", workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        source_documents=[{"id": "D001"}], source_text="Revenue 123",
    )
    agent = ModelAgent("A2", "Test", DummyBackend([]))
    assert "SOURCE COVERAGE" not in agent.build_prompt(context)
    context.source_coverage = [{"field": "annual_filings", "detail": "Requested 10 annual filings; found 10; retrieved 8."}]
    prompt = agent.build_prompt(context)
    directive = prompt.index("RUNNER SOURCE COVERAGE")
    assert "Requested 10 annual filings; found 10; retrieved 8." in prompt[directive:]
    # Outside <untrusted_source_data>: the closing tag of the documents block precedes it.
    assert prompt.rfind("</untrusted_source_data>", 0, directive) > prompt.rfind("<untrusted_source_data", 0, directive)
