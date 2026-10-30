"""
Contract enforcement tests:
1. Structural artifact checks reject wrong types and shapes.
2. Semantic gates: scorecard math, bull/bear linkage, scenario completeness.
3. Section coverage and unknown-section warnings.
4. Injected-default warnings and strict mode.
5. docs/contracts drift test against src/contracts.py.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.mock import A6BMock, A6RMock, A6Mock, A7Mock
from src.contracts import (
    CONTRACT_DOCS,
    REQUIRED_ARTIFACTS,
    SECTION_CONTRACTS,
    validate_contract,
)


def make_context(temp_dir: Path, params=None) -> AgentContext:
    return AgentContext(
        run_id="contract_test",
        params={"ticker": "TEST", "company": "Test Co", "depth": "Standard"}
        if params is None
        else params,
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:contracttest",
        workdir=temp_dir,
        provenance_path=temp_dir / "prov.log",
    )


@pytest.fixture()
def temp_dir():
    path = Path(tempfile.mkdtemp(prefix="fuwforge_contract_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_mock_envelopes_satisfy_contracts(temp_dir):
    context = make_context(temp_dir)
    for agent_cls, upstream in [
        (A6BMock, {}),
        (A6RMock, {}),
        (A6Mock, {}),
        (A7Mock, {}),
    ]:
        env = agent_cls().run(context).envelope
        result = validate_contract(
            env, depth_config={"require_lay_summary": True}, envelopes=upstream
        )
        assert result.valid, f"{env.agent.id} failed contract: {result.errors}"


def test_wrong_artifact_type_is_rejected(temp_dir):
    context = make_context(temp_dir)
    env = A7Mock().run(context).envelope
    env.artifacts["Scorecard"]["Overall"]["score"] = (
        9.99  # no longer sums to weighted total
    )
    result = validate_contract(env)
    assert not result.valid
    assert any("Overall.score" in err for err in result.errors)


@pytest.mark.parametrize("cell", [8.0, None, "invalid", []])
def test_malformed_scorecard_cell_returns_errors_not_an_exception(temp_dir, cell):
    env = A7Mock().run(make_context(temp_dir)).envelope
    env.artifacts["Scorecard"]["Moat"] = cell
    result = validate_contract(env)
    assert not result.valid
    assert any("Scorecard.Moat must be an object" in error for error in result.errors)


def test_numeric_overall_score_string_does_not_crash_validator(temp_dir):
    env = A7Mock().run(make_context(temp_dir)).envelope
    env.artifacts["Scorecard"]["Overall"]["score"] = str(env.artifacts["Scorecard"]["Overall"]["score"])
    assert validate_contract(env).valid


def test_scorecard_weight_sum_is_enforced(temp_dir):
    context = make_context(temp_dir)
    env = A7Mock().run(context).envelope
    env.artifacts["Scorecard"]["Financials"]["weight"] = 0.10
    result = validate_contract(env)
    assert not result.valid
    assert any("weights must sum to 1.0" in err for err in result.errors)


def test_bull_bear_linkage_is_enforced(temp_dir):
    context = make_context(temp_dir)
    bull = A6BMock().run(context).envelope
    bear = A6RMock().run(context).envelope
    bear.artifacts["BearRebuttals"][0]["bull_id"] = "B999"
    result = validate_contract(bear, envelopes={"A6B": bull})
    assert not result.valid
    assert any("does not match any BullCase id" in err for err in result.errors)


def test_scenario_and_danger_zone_completeness(temp_dir):
    context = make_context(temp_dir)
    env = A6Mock().run(context).envelope
    env.artifacts["ScenarioTable"]["Bear"] = {}
    env.artifacts["DangerZoneDashboard"] = []
    result = validate_contract(env)
    assert not result.valid
    errors = "\n".join(result.errors)
    assert "ScenarioTable.Bear" in errors
    assert "DangerZoneDashboard" in errors


def test_section_coverage_and_unknown_sections(temp_dir):
    context = make_context(temp_dir)
    env = A6BMock().run(context).envelope
    env.sections_md = {"SECTION_99_FOREIGN": "not mine"}
    result = validate_contract(env)
    assert not result.valid
    assert any(
        "missing required section 'SECTION_10_BULL_CASE'" in err
        for err in result.errors
    )
    assert any("SECTION_99_FOREIGN" in warning for warning in result.warnings)


@pytest.mark.parametrize("agent_cls", [A6BMock, A6RMock, A6Mock])
def test_schema_supported_missing_data_table_is_not_unknown(temp_dir, agent_cls):
    env = agent_cls().run(make_context(temp_dir)).envelope
    env.sections_md["MISSING_DATA_TABLE"] = (
        "| Metric | Required | Found | Remediation |\n"
        "|---|---|---|---|\n"
        "| Peer evidence | Yes | None | Provide a supporting source. |"
    )

    result = validate_contract(env, strict=True)

    assert result.valid, result.errors
    assert not any(
        "MISSING_DATA_TABLE" in warning for warning in result.warnings
    )


def test_quick_depth_relaxes_lay_summary(temp_dir):
    context = make_context(temp_dir)
    env = A7Mock().run(context).envelope
    del env.sections_md["SECTION_18_LAY_SUMMARY"]
    result = validate_contract(
        env,
        depth_config={"skip_sections": [13, 17, 18], "require_lay_summary": False},
    )
    assert result.valid, result.errors


def test_injected_defaults_warn_then_fail_strict(temp_dir):
    context = make_context(temp_dir)
    env = A7Mock().run(context).envelope
    env.metrics["injected_defaults"] = ["Scorecard"]
    lenient = validate_contract(env)
    assert lenient.valid
    assert any("injected defaults" in warning for warning in lenient.warnings)
    strict = validate_contract(env, strict=True)
    assert not strict.valid
    assert any("strict mode" in err for err in strict.errors)


def test_contract_docs_match_code():
    """Drift test: required artifacts and sections must appear in the contract docs."""
    for agent_id, artifacts in REQUIRED_ARTIFACTS.items():
        doc = PROJECT_ROOT / CONTRACT_DOCS[agent_id]
        assert doc.exists(), f"missing contract doc for {agent_id}: {doc}"
        text = doc.read_text(encoding="utf-8")
        for artifact in artifacts:
            assert f'"{artifact}"' in text, (
                f"{agent_id}: artifact '{artifact}' missing from {doc.name}"
            )
        for section in SECTION_CONTRACTS.get(agent_id, []):
            assert section in text, (
                f"{agent_id}: section '{section}' missing from {doc.name}"
            )


def test_model_agent_repairs_contract_violations(monkeypatch, temp_dir):
    """A model that returns a contract-invalid envelope gets one repair prompt."""
    import json as jsonlib

    from src.agents.model import ModelAgent

    invalid = jsonlib.dumps(
        {
            "schema_version": "2.1",
            "agent": {"id": "A2", "name": "Business-Market-Peers"},
            "sections_md": {
                "SECTION_1_COMPANY_PROFILE": "x",
                "SECTION_2_BUSINESS_MODEL": "x",
                "SECTION_3_MARKET_AND_PEERS": "x",
            },
            "artifacts": {
                "PEER_LIST": ["ONLY_ONE"],
                "MoatTrend": "Expanding",
                "TopCustomerPct": "Not disclosed",
                "TopSupplierPct": "Not disclosed",
            },
            "metrics": {},
        }
    )
    valid = jsonlib.dumps(
        {
            "schema_version": "2.1",
            "agent": {"id": "A2", "name": "Business-Market-Peers"},
            "sections_md": {
                "SECTION_1_COMPANY_PROFILE": "x",
                "SECTION_2_BUSINESS_MODEL": "x",
                "SECTION_3_MARKET_AND_PEERS": "x",
            },
            "artifacts": {
                "PEER_LIST": ["AAA", "BBB", "CCC"],
                "MoatTrend": "Expanding",
                "TopCustomerPct": "Not disclosed",
                "TopSupplierPct": "Not disclosed",
            },
            "metrics": {},
        }
    )

    class SequenceBackend:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        def generate(self, prompt: str) -> str:
            self.prompts.append(prompt)
            self.calls += 1
            return invalid if self.calls == 1 else valid

    backend = SequenceBackend()
    agent = ModelAgent(
        agent_id="A2", name="Business-Market-Peers", backend=backend, max_retries=1
    )
    output = agent.run(make_context(temp_dir))
    assert backend.calls == 2, "contract violation should trigger a repair attempt"
    assert "Contract violations" in backend.prompts[1]
    assert output.envelope.artifacts["PEER_LIST"] == ["AAA", "BBB", "CCC"]
