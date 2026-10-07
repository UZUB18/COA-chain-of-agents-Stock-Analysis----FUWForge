"""Documented unknowns never become fake numbers, neutral signals or missing keys."""
import copy
import json

import pytest

from src.agents.base import AgentContext
from src.agents.model import ModelAgent, ModelBackend
from src.agents.registry import mock_registry
from src.agents.output_schema import envelope_schema
from src.contracts import validate_contract
from src.data_gaps import ALLOWED_DATA_GAPS, missing_data_map
from src.utils.checksum import envelope_content_checksum


@pytest.fixture
def context(tmp_path):
    return AgentContext(run_id="gap_test", params={"ticker": "TEST", "company": "Test", "analysis_date": "2025-01-31", "weights": "default"},
                        depth_config={}, focus_overrides={}, frozen_checksum="sha256:test", workdir=tmp_path, provenance_path=tmp_path / "prov.log",
                        source_documents=[{"id": "D001", "url": "https://example.com/filing", "verification": "retrieved"}],
                        source_text="Retrieved financial statements. No culture survey, consensus or social dataset.", evidence_checksum="sha256:pack")


def envelope(agent_id, context):
    return mock_registry().resolve(agent_id).run(context).envelope


def set_null(artifacts, path):
    keys = path.split(".")
    target = artifacts
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = None


CASES = [(agent_id, path) for agent_id, paths in ALLOWED_DATA_GAPS.items() for path in sorted(paths)]


@pytest.mark.parametrize("agent_id,path", CASES)
def test_only_documented_explicit_allowlisted_nulls_pass(context, agent_id, path):
    env = envelope(agent_id, context)
    set_null(env.artifacts, path)
    if path == "BeneishM.score":
        env.artifacts["BeneishM"]["feasible"] = False
    if path == "GlassdoorScore":
        env.artifacts["LowSampleSizeCultureData"] = True
    assert not validate_contract(env, strict=True).valid
    env.metrics["missing_data"] = [{"field": path, "reason": "Insufficient coverage: reviewed filings contain no measurement of this field."}]
    result = validate_contract(env, strict=True)
    assert result.valid, result.errors


def test_reason_does_not_excuse_missing_required_key_or_partial_sentiment(context):
    env = envelope("A5", context)
    env.artifacts["SentimentSplit"]["pos"] = None
    env.metrics["missing_data"] = {"SentimentSplit": "No social sample"}
    assert not validate_contract(env, strict=True).valid
    env.artifacts.pop("SentimentSplit")
    assert not validate_contract(env, strict=True).valid


def test_missing_data_does_not_mean_neutral_or_no_events(context):
    env = envelope("A5", context)
    env.metrics["missing_data"] = {"InsiderNet12m": "No form-4 dataset", "MajorLitigation": "Coverage missing"}
    env.artifacts["InsiderNet12m"] = {"direction": "Neutral", "shares": 0, "value": 0}
    env.artifacts["MajorLitigation"] = []
    assert not validate_contract(env, strict=True).valid
    env.artifacts["InsiderNet12m"] = None
    env.artifacts["MajorLitigation"] = None
    assert validate_contract(env, strict=True).valid


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), 10 ** 400])
def test_critical_market_measurement_cannot_be_excused(context, price):
    env = envelope("A1", context)
    env.artifacts["market_cap_check"]["price"] = price
    env.metrics["missing_data"] = {"market_cap_check.price": "Unavailable"}
    assert not validate_contract(env, strict=True).valid


def test_scorecard_and_auditor_are_not_allowlisted(context):
    for agent_id, path in (("A7", "Scorecard.Moat.score"), ("A4", "Auditor")):
        env = envelope(agent_id, context)
        set_null(env.artifacts, path)
        env.metrics["missing_data"] = {path: "Unavailable"}
        assert not validate_contract(env, strict=True).valid


class Backend(ModelBackend):
    def __init__(self, data):
        self.data = data

    def generate(self, prompt):
        return json.dumps(self.data)


@pytest.mark.parametrize("strict", [False, True])
def test_model_preserves_culture_null_without_injecting_default(context, strict):
    data = envelope("A4", context).to_dict()
    data["sources"] = []
    data["artifacts"]["GlassdoorScore"] = None
    data["artifacts"]["LowSampleSizeCultureData"] = True
    data["metrics"]["missing_data"] = [{"field": "GlassdoorScore", "reason": "No dated culture survey in retrieved filings."}]
    output = ModelAgent("A4", "Governance", Backend(data), strict=strict, max_retries=0).run(context).envelope
    assert output.artifacts["GlassdoorScore"] is None
    assert not output.metrics.get("injected_defaults")
    assert "GlassdoorScore" in output.sections_md["MISSING_DATA_TABLE"]
    assert validate_contract(output, strict=True).valid
    data["sources"] = [{"url": "https://unretrieved.example.com"}]
    with pytest.raises(RuntimeError, match="cite only runner-retrieved"):
        ModelAgent("A4", "Governance", Backend(data), strict=True, max_retries=0).run(context)


def test_gap_reason_is_semantic_tamper_evident_content(context):
    data = envelope("A4", context).to_dict()
    data["metrics"]["missing_data"] = {"GlassdoorScore": "No dated source"}
    digest = envelope_content_checksum(data)
    changed = copy.deepcopy(data)
    changed["metrics"]["missing_data"]["GlassdoorScore"] = "Different coverage claim"
    assert envelope_content_checksum(changed) != digest
    changed = copy.deepcopy(data)
    changed["metrics"]["latency_ms"] = 123456
    assert envelope_content_checksum(changed) == digest


def test_report_assembly_merges_agent_gap_tables_without_weakening_conflicts(context):
    from pathlib import Path
    from src.orchestrator import FUWForgeOrchestrator

    root = Path(__file__).resolve().parents[1]
    runner = FUWForgeOrchestrator(root)
    a3, a4 = envelope("A3", context), envelope("A4", context)
    a3.sections_md["MISSING_DATA_TABLE"] = "PEG_Fwd: consensus unavailable"
    a4.sections_md["MISSING_DATA_TABLE"] = "GlassdoorScore: no culture sample"
    merged = runner._assemble_sections({"A3": a3, "A4": a4})
    assert "A3 unavailable inputs" in merged and "A4 unavailable inputs" in merged
    assert "consensus unavailable" in merged and "no culture sample" in merged
    a4.sections_md["SECTION_4_FINANCIAL_FORENSICS"] = "Conflicting numbered section"
    with pytest.raises(RuntimeError, match="Conflicting content"):
        runner._assemble_sections({"A3": a3, "A4": a4})


def test_native_schema_contains_explicit_gap_fields(context):
    schema = envelope_schema("A4", context.params, {})
    entries = schema["properties"]["metrics"]["properties"]["missing_data"]["items"]
    assert "GlassdoorScore" in entries["properties"]["field"]["enum"]
    assert "Auditor" not in entries["properties"]["field"]["enum"]
    assert "missing_data" not in envelope_schema("A1", context.params, {})["properties"]["metrics"]["properties"]


@pytest.mark.parametrize("value", ["unknown", {"GlassdoorScore": ""}, [{"field": "GlassdoorScore", "reason": "a"}, {"field": "GlassdoorScore", "reason": "b"}]])
def test_malformed_gap_ledger_is_rejected(value):
    with pytest.raises(ValueError):
        missing_data_map(value)
