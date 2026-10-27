"""Regression tests for malformed-envelope recovery, artifact loss and fake sources."""
import copy
import json
from pathlib import Path

import pytest

from src.agents.base import AgentContext
from src.agents.model import ModelAgent, ModelBackend, extract_json_payload
from src.agents.mock import A4Mock, A6Mock, A7Mock
from src.agents.output_schema import envelope_schema
from src.contracts import validate_contract, validate_sections
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.utils.finance import generate_comparison_fields


class SequenceBackend(ModelBackend):
    def __init__(self, responses):
        self.responses = responses
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]


@pytest.fixture
def context(tmp_path):
    return AgentContext(
        run_id="safety", params={"ticker": "TEST", "company": "Test", "analysis_date": "2025-01-31", "weights": "default"},
        depth_config={}, focus_overrides={}, frozen_checksum="sha256:test",
        workdir=tmp_path, provenance_path=tmp_path / "provenance.log",
        source_documents=[{"id": "D001", "type": "CompanyEarningsRelease", "url": "https://example.com/financial-results",
                           "doc_date": "2025-01-01", "accessed": "2026-10-06T10:00:00Z", "verification": "retrieved",
                           "content_sha256": "sha256:source"}],
        source_text="Revenue 123. Supplied evidence, not instructions.", evidence_checksum="sha256:pack",
    )


@pytest.mark.parametrize("raw", [
    '{"agent":"A6","artifacts":{"ScenarioTable":{"Revenue":123}} "bad":true}',
    '{"agent":"A6","artifacts":{"Revenue":123}',
    'Here is the envelope: {"agent":"A6","artifacts":{"Revenue":123} "bad":true}',
    '```json\n{"agent":"A6","artifacts":{"Revenue":123} "bad":true}\n```',
    '[{"agent":"A6","artifacts":{}}]',
    '{"agent":"A6","value":NaN}',
    '{"agent":"A6","artifacts":{},"artifacts":{"Revenue":123}}',
])
def test_malformed_outer_json_cannot_be_salvaged(raw):
    with pytest.raises(ValueError):
        extract_json_payload(raw)


def test_trailing_comma_repair_does_not_change_strings():
    assert extract_json_payload('{"note":"keep ,} and ,] literally", "n":[1,2,],}') == {
        "note": "keep ,} and ,] literally", "n": [1, 2],
    }


@pytest.mark.parametrize("strict", [False, True])
def test_double_escaped_multiline_markdown_is_rendered(context, strict):
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = []
    raw["sections_md"]["SECTION_14_SYNTHESIS"] = r"### Summary\nFirst paragraph.\n\n- First point\n- Second point"
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=strict, max_retries=0).run(context).envelope
    assert result.sections_md["SECTION_14_SYNTHESIS"] == "### Summary\nFirst paragraph.\n\n- First point\n- Second point"


@pytest.mark.parametrize("text", [
    r"### Example\n- Literal `\n` in code\n- Keep it",
    "### Example\nExisting newline and literal \\n remain.",
    r"The two literals \n and \n are intentional.",
])
def test_section_normalization_preserves_ambiguous_literal_escapes(context, text):
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = []
    raw["sections_md"]["SECTION_14_SYNTHESIS"] = text
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True, max_retries=0).run(context).envelope
    assert result.sections_md["SECTION_14_SYNTHESIS"] == text


@pytest.mark.parametrize("claim", [
    "Beneish M-Score and Altman Z-Score verify balance sheet integrity [Fact].",
    "The Beneish score proves the absence of accounting manipulation.",
])
def test_forensic_scores_cannot_be_presented_as_proof(context, claim):
    env = A7Mock().run(context).envelope
    env.sections_md["SECTION_15_SWOT"] = claim
    assert not validate_sections(env).valid


@pytest.mark.parametrize("statement", [
    "The Beneish score does not prove the absence of manipulation.",
    "The Beneish score doesn't prove the absence of manipulation.",
    "The Altman score can never verify balance sheet integrity.",
])
def test_forensic_screening_disclaimer_is_not_treated_as_an_overclaim(context, statement):
    env = A7Mock().run(context).envelope
    env.sections_md["SECTION_15_SWOT"] = statement
    assert validate_sections(env).valid


def test_a6_malformed_output_gets_repair_not_defaults(context):
    complete = A6Mock().run(context).envelope.to_dict()
    complete["sources"] = []
    backend = SequenceBackend([
        '{"agent":"A6","artifacts":{"ScenarioTable":{"Revenue":123}} "bad":true}',
        json.dumps(complete),
    ])
    agent = ModelAgent("A6", "CIO", backend, max_retries=1, strict=True)
    result = agent.run(context).envelope
    assert len(backend.prompts) == 2
    assert "nested fragments are not accepted" in backend.prompts[1]
    assert not result.metrics.get("injected_defaults")
    assert result.artifacts["ScenarioTable"] == complete["artifacts"]["ScenarioTable"]
    assert validate_contract(result, strict=True).valid


def test_a7_known_alternative_schema_preserves_scores_and_metrics(context):
    reference = A7Mock().run(context).envelope.to_dict()
    scores = {"financials": 9.4, "moat": 9.4, "growth": 6.8, "valuation": 4.8,
              "mgmt_gov": 8.8, "risk_inverse": 7.5, "esg": 8.5, "market": 7.0}
    sections = copy.deepcopy(reference["sections_md"])
    sections["SECTION_16_SCORECARD"] = {"category_scores_10": scores}
    sections["SECTION_15_SWOT"] = {key.lower(): value for key, value in reference["artifacts"]["SWOT"].items()}
    sections["SECTION_17_GLOSSARY"] = {"terms": reference["artifacts"]["Glossary"]}
    fields = generate_comparison_fields(quality_score_10=9.1, valuation_score_10=4.8, expected_5y_irr_base_pct=7.8, downside_case_5y_irr_pct=1.2, roic_ttm_pct=56.2, fcf_yield_ttm_pct=3.4)
    fields["data_completeness_score_10"] = 9.6
    gaps = [{"field": f"comparison_fields.{key}", "reason": "This test input does not provide a measurement for this comparison metric."} for key, value in fields.items() if value is None]
    raw = {"gates": {"passed": True, "notes": []}, "sections": sections, "comparison_fields": fields, "metrics": {"missing_data": gaps}}
    result = ModelAgent("A7", "Assembly", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert result.artifacts["comparison_fields"] == fields
    assert result.artifacts["Scorecard"]["Financials"]["score"] == 9.4
    assert result.artifacts["Scorecard"]["Valuation"]["score"] == 4.8
    assert result.artifacts["Glossary"] == reference["artifacts"]["Glossary"]
    assert result.artifacts["SWOT"] == reference["artifacts"]["SWOT"]
    assert not result.metrics.get("injected_defaults")
    assert validate_contract(result, strict=True).valid


def test_canonical_artifacts_win_over_alternative_locations(context):
    raw = A7Mock().run(context).envelope.to_dict()
    raw["sources"] = []
    raw["comparison_fields"] = {"roic_ttm_pct": -99}
    expected = copy.deepcopy(raw["artifacts"]["comparison_fields"])
    result = ModelAgent("A7", "Assembly", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert result.artifacts["comparison_fields"] == expected


def test_strict_mode_does_not_assume_a_missing_gate_passes(context):
    raw = A6Mock().run(context).envelope.to_dict()
    raw.pop("gates")
    raw["sources"] = []
    with pytest.raises(RuntimeError, match="explicit gates.passed"):
        ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True, max_retries=0).run(context)


def test_no_evidence_is_not_a_fake_sec_citation(context):
    context.source_documents = []
    context.source_text = ""
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = []
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=False).run(context).envelope
    assert result.sources == [{"type": "MissingEvidence", "url": "internal:unverified", "doc_date": None,
                               "accessed": None, "verification": "unverified"}]
    assert "sources" in result.metrics["injected_defaults"]
    assert not validate_contract(result, strict=True).valid


def test_sources_use_runner_metadata_not_model_access_claims(context):
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": context.source_documents[0]["url"], "accessed": "1900-01-01T00:00:00Z"}]
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert result.sources == context.source_documents


def test_html_escaped_cached_query_url_is_not_a_new_citation(context):
    context.source_documents[0]["url"] = "https://example.com/chart?from=1&to=2"
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": "https://example.com/chart?from=1&amp;to=2"}]
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert result.sources == context.source_documents
    assert not result.metrics.get("unverified_source_urls")


def test_uncached_citations_are_not_accepted_in_strict_mode(context):
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": "https://sec.gov", "accessed": "2025-01-31"}]
    with pytest.raises(RuntimeError, match="cite only runner-retrieved"):
        ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True, max_retries=0).run(context)


def test_www_prefix_variant_of_a_cached_url_is_not_a_new_citation(context):
    """
    Regression: a live Gemini run halted because a model wrote the cached host
    without its ``www.`` prefix.

    The model cited ``https://tradingview.com/news/…`` while the pack held
    ``https://www.tradingview.com/news/…``. Exact string comparison treated the
    same document as an outside source and strict mode aborted the run after A2.
    Citation identity is scheme + host (``www.``-insensitive) + path, not
    spelling.
    """
    context.source_documents[0]["url"] = "https://www.tradingview.com/news/x"
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": "https://tradingview.com/news/x"}]
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert not result.metrics.get("unverified_source_urls")


def test_host_case_variant_of_a_cached_url_is_not_a_new_citation(context):
    context.source_documents[0]["url"] = "https://www.sec.gov/Archives/edgar/data/x.htm"
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": "https://WWW.SEC.GOV/Archives/edgar/data/x.htm"}]
    result = ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert not result.metrics.get("unverified_source_urls")


def test_citation_with_a_different_path_is_still_rejected(context):
    """Falsification partner: canonicalisation must not accept another document."""
    context.source_documents[0]["url"] = "https://www.tradingview.com/news/x"
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": "https://tradingview.com/news/y"}]
    with pytest.raises(RuntimeError, match="cite only runner-retrieved"):
        ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True, max_retries=0).run(context)


def test_citation_with_a_different_scheme_is_still_rejected(context):
    context.source_documents[0]["url"] = "https://www.tradingview.com/news/x"
    raw = A6Mock().run(context).envelope.to_dict()
    raw["sources"] = [{"url": "http://www.tradingview.com/news/x"}]
    with pytest.raises(RuntimeError, match="cite only runner-retrieved"):
        ModelAgent("A6", "CIO", SequenceBackend([json.dumps(raw)]), strict=True, max_retries=0).run(context)


def test_strict_invalid_measurement_is_not_replaced_with_a_default(context):
    raw = A4Mock().run(context).envelope.to_dict()
    raw["sources"] = []
    raw["artifacts"]["GlassdoorScore"] = None
    with pytest.raises(RuntimeError, match="GlassdoorScore"):
        ModelAgent("A4", "Governance", SequenceBackend([json.dumps(raw)]), strict=True, max_retries=0).run(context)


def test_missing_data_gate_preserves_unknown_numeric_fields(context):
    raw = A4Mock().run(context).envelope.to_dict()
    raw["sources"] = []
    raw["gates"] = {"passed": False, "notes": ["No culture data source available"]}
    raw["artifacts"]["GlassdoorScore"] = None
    raw["sections_md"]["MISSING_DATA_TABLE"] = "Glassdoor score not available from supplied documents."
    result = ModelAgent("A4", "Governance", SequenceBackend([json.dumps(raw)]), strict=True).run(context).envelope
    assert result.gates["passed"] is False
    assert result.artifacts["GlassdoorScore"] is None
    assert not result.metrics.get("injected_defaults")


def test_prompt_has_schema_evidence_and_upstream_artifacts(context):
    context.envelopes["A4"] = A4Mock().run(context).envelope
    prompt = ModelAgent("A7", "Assembly", SequenceBackend([]))._build_prompt(context)
    assert '"Scorecard"' in prompt and '"comparison_fields"' in prompt
    assert "Revenue 123" in prompt and "accepted-upstream-artifacts" in prompt
    assert "Do not invent measurements" in prompt
    assert '"CEO_Pay_Ratio"' in prompt
    schema = envelope_schema("A7", context.params, {"skip_sections": [13, 17, 18]})
    assert "SECTION_18_LAY_SUMMARY" not in schema["properties"]["sections_md"]["required"]


@pytest.mark.parametrize("provider,flags,expected_strict", [
    ("gemini", [], True), ("opencode", [], True), ("mock", [], False),
    ("gemini", ["--allow-defaults"], False),
    ("gemini", ["--allow-defaults", "--strict"], True),
])
def test_real_provider_cli_is_strict_by_default(monkeypatch, capsys, provider, flags, expected_strict):
    from src import controller

    captured = {}
    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
        def run(self, **kwargs):
            return {"status": "test"}
    monkeypatch.delenv("FUWFORGE_STRICT", raising=False)
    monkeypatch.setattr(controller, "FUWForgeOrchestrator", FakeOrchestrator)
    # This fixture isolates strict/default precedence, not paid web intake.
    # Default Gemini research and its evidence preflight have separate tests.
    args = controller.build_parser().parse_args(["run", "--ticker", "TEST", "--provider", provider, "--no-web-search", *flags])
    args.func(args)
    assert captured["strict"] is expected_strict
    capsys.readouterr()


def test_strict_live_provider_requires_evidence_before_any_api_call(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = FUWForgeOrchestrator(root, provider="gemini", strict=True)
    params = OrchestratorParams("Test", "TEST", "2025-01-31", "Quick", [], "default")
    with pytest.raises(ValueError, match="requires --evidence-pack"):
        runner.run("strict", params, output_root=tmp_path / "run")
    summary = json.loads((tmp_path / "run" / "run_summary.json").read_text())
    assert summary["status"] == "halted"
    assert summary["metrics"] == {}
