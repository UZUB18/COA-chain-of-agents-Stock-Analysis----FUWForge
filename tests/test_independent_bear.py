"""
The independent-bear challenge must actually work as an independent pass.

These tests render the REAL prompt (not just the Python context) because the
failure mode this guards against is a contradiction that only appears after the
context is interpolated into the text sent to the model.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.model import ModelAgent, ModelBackend
from src.agents.registry import model_registry


class RecordingBackend(ModelBackend):
    def __init__(self) -> None:
        self.model = "rec-v1"
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return json.dumps({"schema_version": "2.1"})


@pytest.fixture
def a6r_agent():
    backend = RecordingBackend()
    return model_registry(backend, provider="recording").resolve("A6R")


def _context(tmp_path, challenge_initial, envelopes=None):
    return AgentContext(
        run_id="bear_prompt",
        params={"ticker": "AAPL", "company": "Apple", "analysis_date": "2026-02-01", "weights": "default"},
        depth_config={},
        focus_overrides={},
        frozen_checksum="abc123",
        workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        source_documents=[],
        source_text="",
        evidence_checksum="sha256:pack",
        envelopes=envelopes or {},
        challenge_initial=challenge_initial,
    )


def test_independent_phase_prompt_never_asks_to_match_bull_ids(a6r_agent, tmp_path):
    """
    The blind pass has no bull case. Telling it to "match upstream BullCase id"
    contradicts the supplied context and invites invented ids, so that
    instruction must not appear in the independent prompt.
    """
    prompt = a6r_agent.build_prompt(
        _context(tmp_path, {"phase": "independent", "instructions": "be blind"})
    )

    assert "INDEPENDENT BLIND PHASE" in prompt
    # No instruction to match/emit upstream bull ids in the blind phase.
    assert "matching upstream BullCase id exactly" not in prompt
    # It should instead tell the model to use its own local ids.
    assert "local hypothesis identifier" in prompt
    # And explicitly say the ids are not B1/B2/B3 yet.
    assert "not B1/B2/B3" in prompt


def test_challenge_directive_is_outside_untrusted_source_data(a6r_agent, tmp_path):
    """
    EPISTEMIC_RULES tells the model to ignore instructions inside
    <untrusted_source_data>. A runner phase directive wrapped in those tags would
    be silently voided, so the directive must sit in the authoritative prompt body.
    """
    prompt = a6r_agent.build_prompt(
        _context(tmp_path, {"phase": "independent", "instructions": "be blind"})
    )

    # The directive is present as authoritative text.
    assert "RUNNER PHASE DIRECTIVE (independent)" in prompt
    # And it must NOT be inside an untrusted block.
    open_tag = prompt.index('<untrusted_source_data label="initial-bear">')
    close_tag = prompt.index("</untrusted_source_data>", open_tag)
    directive_pos = prompt.index("RUNNER PHASE DIRECTIVE (independent)")
    assert not (open_tag < directive_pos < close_tag), (
        "phase directive must not be wrapped in untrusted_source_data"
    )


def test_reconciliation_phase_prompt_allows_bull_matching(a6r_agent, tmp_path):
    """With a bull case supplied, the reconcile pass must be told to match ids."""
    from src.contracts import Envelope

    a6b = Envelope.from_dict({
        "schema_version": "2.1",
        "agent": {"id": "6B", "name": "Bull"},
        "params": {"ticker": "AAPL"},
        "gates": {"passed": True, "notes": []},
        "sections_md": {},
        "artifacts": {"BullCase": [{"id": "B1"}, {"id": "B2"}]},
        "sources": [],
        "warnings": [],
        "metrics": {"data_checksum": "abc123"},
        "confidence": 0.9,
    })
    prompt = a6r_agent.build_prompt(
        _context(tmp_path, {"phase": "initial_bear", "envelope": {}}, {"A6B": a6b})
    )

    # Reconcile pass legitimately references bull ids and the prior bear view.
    assert "matching the upstream BullCase id exactly" in prompt
    assert "RUNNER PHASE DIRECTIVE (reconciliation)" in prompt
    assert "Reconcile against the independent bear view" in prompt
    # It must NOT show the blind-phase-only instructions.
    assert "INDEPENDENT BLIND PHASE" not in prompt


def test_independent_phase_preserves_model_local_ids(a6r_agent, tmp_path):
    """
    The blind pass assigns its own local ids (H1, H2, ...). The normalizer must
    not overwrite them with B1/B2/B3 — that would fabricate a mapping to bull
    theses that do not exist yet and destroy the independence.
    """
    agent = a6r_agent
    context = _context(tmp_path, {"phase": "independent", "instructions": "be blind"})
    payload = {
        "schema_version": "2.1",
        "agent": {"id": "A6R", "name": "Bear"},
        "params": {"ticker": "AAPL"},
        "gates": {"passed": True, "notes": []},
        "sections_md": {"SECTION_11_BEAR_CASE": "bear"},
        "artifacts": {
            "BearRebuttals": [
                {"bull_id": "H1", "probability": "High", "severity": "High", "counter": "r1"},
                {"bull_id": "H2", "probability": "Low", "severity": "Med", "counter": "r2"},
            ]
        },
        "sources": [],
        "warnings": [],
        "metrics": {"data_checksum": "abc123"},
        "confidence": 0.8,
    }
    normalize = getattr(agent, "_normalize_envelope", None)
    if normalize is None:
        pytest.skip("ModelAgent has no _normalize_envelope in this build")

    out = normalize(payload, context)
    ids = [item["bull_id"] for item in out["artifacts"]["BearRebuttals"]]
    assert ids == ["H1", "H2"], f"local independent ids were rewritten to {ids}"

# --------------------------------------------- orchestration-level independence


def test_independent_bear_is_opt_in_and_capped():
    """
    The blind bear pass is an experiment, not a default.

    It must cost exactly one extra call, be off unless requested, and stay
    labelled as pending evaluation. Promoting it automatically would assert a
    benefit (better research) that has not been demonstrated.
    """
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    params = OrchestratorParams(
        company="Probe Co", ticker="PRB", analysis_date="2026-02-01",
        depth="Standard", focus=[], weights="default",
    )

    off = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    assert off.independent_bear is False, "the blind bear pass must be opt-in"

    on = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", independent_bear=True)
    assert on.independent_bear is True


def test_blind_phase_cannot_see_the_bull_case_and_has_distinct_identity(tmp_path):
    """
    The blind pass must not see the bull thesis, and must be a distinct,
    separately-identified execution from the reconciled pass.

    Independence is structural: the blind envelope's upstream set excludes A6B,
    so its execution identity differs from the reconciled pass and a receipt
    can never be reused across the two.
    """
    import shutil

    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    out = tmp_path / "bear"
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", independent_bear=True)
    orchestrator.run(
        "bear_probe",
        OrchestratorParams(
            company="Probe Co", ticker="PRB", analysis_date="2026-02-01",
            depth="Standard", focus=[], weights="default",
        ),
        output_root=out,
    )

    a6r = json.loads((out / "a6r_envelope.json").read_text(encoding="utf-8"))
    record = a6r["artifacts"]["_independent_bear_reconciliation"]

    blind = record["initial_envelope"]
    blind_parents = {e["agent"] for e in blind["upstream"]}
    final_parents = {e["agent"] for e in a6r["upstream"]}

    # Independence: the blind pass never sees the bull case.
    assert "A6B" not in blind_parents
    assert "A6B" in final_parents

    # Distinct identity: the blind phase is separately identified.
    assert blind["content_checksum"] == record["initial_checksum"]
    assert blind["content_checksum"] != a6r["content_checksum"]

    # Cost is bounded and the feature remains experimental.
    assert record["extra_calls_max"] == 1
    assert record["promotion_status"] == "experimental_pending_cost_and_review_evaluation"


def test_beneish_version_and_coefficients_are_frozen():
    """
    The calculator's published coefficients and version are a contract.

    Changing them silently would invalidate every recorded witness, so they are
    pinned here.
    """
    from src.utils.beneish import (
        BENEISH_COEFFICIENTS,
        BENEISH_SCREENING_THRESHOLD,
        BENEISH_VARIANT,
        CALCULATOR_VERSION,
    )

    assert CALCULATOR_VERSION == "2.0.0"
    assert BENEISH_VARIANT == "m-score-8-variable"
    assert BENEISH_COEFFICIENTS["tata"] == 4.679
    assert BENEISH_COEFFICIENTS["lvgi"] == -0.327
    assert BENEISH_COEFFICIENTS["intercept"] == -4.84
    assert BENEISH_SCREENING_THRESHOLD == -1.78


def test_beneish_threshold_is_not_described_as_proof_of_fraud():
    """
    Crossing the screening threshold is a signal, not a finding.

    The module that implements it must keep saying so, and must not use
    fraud-proof language.
    """
    import inspect

    from src.utils import beneish

    source = inspect.getsource(beneish).lower()
    assert "screening signal" in source
    for phrase in ("proves fraud", "proof of fraud", "confirms fraud", "fraudulent"):
        assert phrase not in source, f"unfounded fraud claim in beneish module: {phrase}"
