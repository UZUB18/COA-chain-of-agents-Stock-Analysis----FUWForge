"""Batch B: outputs never carry invented values or schema-invalid enums."""
import json
from pathlib import Path

from src.agents.base import AgentContext
from src.agents.model import _context_checksum
from src.agents.registry import mock_registry
from src.controller import _load_a7


def _context(tmp_path: Path, **kwargs) -> AgentContext:
    return AgentContext(
        run_id="r", params={"ticker": "ACME", "company": "Acme", "analysis_date": "2025-01-31"},
        depth_config={}, focus_overrides={}, frozen_checksum="", workdir=tmp_path,
        provenance_path=tmp_path / "prov.log", **kwargs,
    )


def test_mock_a1_data_checksum_covers_the_evidence_pack_like_the_model_path(tmp_path):
    agent = mock_registry().resolve("A1")
    with_pack = _context(tmp_path, evidence_checksum="sha256:" + "a" * 64)
    other_pack = _context(tmp_path, evidence_checksum="sha256:" + "b" * 64)
    first = agent.run(with_pack).envelope.metrics["data_checksum"]
    assert first == _context_checksum(with_pack)
    assert first != agent.run(other_pack).envelope.metrics["data_checksum"]


def test_screen_fallback_leaves_unmeasured_fields_null(tmp_path):
    (tmp_path / "a7_envelope.json").write_text(json.dumps({
        "agent": {"id": "A7"},
        "artifacts": {"Scorecard": {"Overall": {"score": 7.5}, "Valuation": {"score": 6.0}, "Moat": {"score": 8.0}}},
    }), encoding="utf-8")
    _, fields, _ = _load_a7(tmp_path)
    assert fields["quality_score_10"] == 7.5
    for key in ("expected_5y_irr_base_pct", "downside_case_5y_irr_pct", "fcf_yield_ttm_pct",
                "evidence_quality_score_10", "data_completeness_score_10"):
        assert fields[key] is None, key


def test_screen_fallback_never_invents_missing_scorecard_scores(tmp_path):
    (tmp_path / "a7_envelope.json").write_text(json.dumps({
        "agent": {"id": "A7"},
        "artifacts": {"Scorecard": {"Overall": {"score": 7.5}, "Valuation": {"score": "n/a"}}},
    }), encoding="utf-8")
    _, fields, _ = _load_a7(tmp_path)
    assert fields["quality_score_10"] == 7.5
    assert fields["valuation_score_10"] is None
    assert fields["moat_score_10"] is None
