"""
Consumed-parent lineage: record what an agent actually read, not what happened
to exist when its result was accepted.

The defect this guards against is subtle and was live in the orchestrator. In the
parallel core phase, A2-A5 all receive an envelope snapshot containing A1. All
four results are collected, then accepted in canonical order A2 -> A5. Lineage
was built during acceptance from the *shared, growing* accepted-results
dictionary, so:

- A2 accepted first -> correctly records A1.
- A3 accepted second -> sees {A1, A2} and wrongly records A2 as a parent, even
  though A2 was never in A3's prompt.
- A4, A5 accumulate further false dependencies.

That makes the recorded graph differ from the executed graph, which breaks
incremental recomputation, audit reconstruction and prompt debugging at once.

These tests pin the pure capture utility and then the end-to-end behaviour of a
real (mock) parallel phase.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.execution_identity import (
    UpstreamRef,
    capture_upstream,
    execution_key,
)
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.utils.checksum import envelope_content_checksum

CHECKSUM_A1 = "sha256:" + "a" * 64
CHECKSUM_A2 = "sha256:" + "b" * 64
CHECKSUM_A3 = "sha256:" + "c" * 64
CHECKSUM_A4 = "sha256:" + "d" * 64


def _serialized(checksum: str, agent: str = "A1") -> dict:
    return {
        "agent": {"id": agent, "name": agent},
        "content_checksum": checksum,
        "sections_md": {},
    }


# ------------------------------------------------------------- pure capture


def test_capture_is_immutable_and_a1_only():
    """The captured refs must not follow later mutation of the input mapping."""
    envelopes = {"A1": _serialized(CHECKSUM_A1)}
    captured = capture_upstream(envelopes)
    assert captured == (UpstreamRef("A1", CHECKSUM_A1),)

    # Simulate the parallel phase: A2 is accepted and A1's entry is rewritten.
    envelopes["A2"] = _serialized(CHECKSUM_A2, "A2")
    envelopes["A1"] = _serialized(CHECKSUM_A3)  # different A1 hash now

    # The earlier capture is untouched: still A1-only, original hash.
    assert captured == (UpstreamRef("A1", CHECKSUM_A1),)


def test_capture_is_sorted_and_deduplicated_by_construction():
    refs = capture_upstream(
        {"A3": _serialized(CHECKSUM_A3, "A3"), "A1": _serialized(CHECKSUM_A1)}
    )
    assert [ref.agent for ref in refs] == ["A1", "A3"]


def test_capture_rejects_missing_or_malformed_checksum():
    with pytest.raises(ValueError):
        capture_upstream({"A1": {"content_checksum": None}})
    with pytest.raises(ValueError):
        capture_upstream({"A1": {"content_checksum": "not-a-sha256"}})


def test_capture_rejects_blank_agent_ids():
    with pytest.raises(ValueError):
        capture_upstream({"  ": _serialized(CHECKSUM_A1)})


# --------------------------------------------------------- execution identity


def test_execution_key_changes_with_upstream_and_prompt():
    base = dict(
        provider="mock",
        model="test-model",
        prompt_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
        upstream=(UpstreamRef("A1", CHECKSUM_A1),),
    )
    key = execution_key(**base)
    assert execution_key(**base) == key  # deterministic
    changed_prompt = {**base, "prompt_sha256": "sha256:" + "9" * 64}
    changed_parent = {**base, "upstream": (UpstreamRef("A1", CHECKSUM_A2),)}
    assert execution_key(**changed_prompt) != key
    assert execution_key(**changed_parent) != key


def test_execution_key_rejects_duplicate_upstream_agents():
    with pytest.raises(ValueError):
        execution_key(
            provider="mock",
            model="m",
            prompt_sha256="sha256:" + "1" * 64,
            evidence_sha256="sha256:" + "2" * 64,
            upstream=(UpstreamRef("A1", CHECKSUM_A1), UpstreamRef("A1", CHECKSUM_A2)),
        )


# ------------------------------------------------- end-to-end parallel phase


def _run(tmp: Path, ticker: str = "LINEAGE") -> None:
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Lineage Co",
        ticker=ticker,
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("lineage_run", params, output_root=tmp)


def _upstream_map(run_dir: Path, agent: str) -> list[str]:
    data = json.loads((run_dir / f"{agent.lower()}_envelope.json").read_text(encoding="utf-8"))
    return [entry["agent"] for entry in data.get("upstream", [])]


def test_core_parallel_agents_record_only_what_they_actually_consumed():
    """
    A2, A3, A4 and A5 must each record exactly {A1} as their consumed parents.

    Against the old acceptance-order logic this fails for A3/A4/A5, which gain
    their earlier accepted siblings as false parents.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_lineage_"))
    try:
        _run(tmp)

        # All four parallel agents saw only A1 in their prompt.
        for agent in ("A2", "A3", "A4", "A5"):
            assert _upstream_map(tmp, agent) == ["A1"], (
                f"{agent} recorded false parents: {_upstream_map(tmp, agent)}"
            )

        # Downstream agents consume everything accepted before them; that is
        # genuine because they actually receive those envelopes.
        assert set(_upstream_map(tmp, "A6B")) == {"A1", "A2", "A3", "A4", "A5"}
        assert set(_upstream_map(tmp, "A6R")) == {"A1", "A2", "A3", "A4", "A5", "A6B"}
        assert set(_upstream_map(tmp, "A6")) == {"A1", "A2", "A3", "A4", "A5", "A6B", "A6R"}
        assert set(_upstream_map(tmp, "A7")) == {
            "A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recorded_upstream_checksums_match_the_referenced_envelopes():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_lineage_ck_"))
    try:
        _run(tmp, ticker="LINEAGE2")
        digests = {}
        for agent in ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
            data = json.loads(
                (tmp / f"{agent.lower()}_envelope.json").read_text(encoding="utf-8")
            )
            digests[agent] = data["content_checksum"]

        for agent in ("A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
            data = json.loads(
                (tmp / f"{agent.lower()}_envelope.json").read_text(encoding="utf-8")
            )
            for entry in data["upstream"]:
                assert entry["content_checksum"] == digests[entry["agent"]], (
                    f"{agent} recorded a stale/incorrect checksum for {entry['agent']}"
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_upstream_order_is_canonical_not_acceptance_raced():
    """Recorded parents are sorted, so parallel completion order cannot leak in."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_lineage_ord_"))
    try:
        _run(tmp, ticker="LINEAGE3")
        for agent in ("A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
            agents = _upstream_map(tmp, agent)
            assert agents == sorted(agents), f"{agent} upstream is not canonical: {agents}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_content_checksum_covers_upstream_lineage():
    """Re-pointing a parent must change the sealed envelope checksum."""
    base = {
        "schema_version": "2.1",
        "agent": {"id": "A3", "name": "Quant-Valuation-Technicals"},
        "sections_md": {"SECTION_4_FINANCIAL_FORENSICS": "x"},
        "artifacts": {},
        "sources": [],
        "upstream": [{"agent": "A1", "content_checksum": CHECKSUM_A1}],
    }
    first = envelope_content_checksum(base)
    retargeted = {**base, "upstream": [{"agent": "A1", "content_checksum": CHECKSUM_A3}]}
    assert envelope_content_checksum(retargeted) != first


def test_recorded_execution_lineage_is_covered_by_the_content_checksum():
    """
    The in-envelope execution record must be tamper-evident.

    ``_execution_lineage`` is what `controller seal` trusts to rebuild stripped
    lineage, so it has to sit inside the hashed payload rather than in the
    excluded metrics block.
    """
    base = {
        "schema_version": "2.1",
        "agent": {"id": "A3", "name": "Quant-Valuation-Technicals"},
        "sections_md": {},
        "artifacts": {
            "_execution_lineage": {
                "consumed_upstream": ["A1"],
                "execution_key": "a" * 64,
            }
        },
        "sources": [],
        "upstream": [{"agent": "A1", "content_checksum": CHECKSUM_A1}],
    }
    tampered = json.loads(json.dumps(base))
    tampered["artifacts"]["_execution_lineage"]["consumed_upstream"] = ["A1", "A2"]
    assert envelope_content_checksum(tampered) != envelope_content_checksum(base)


def test_seal_restores_stripped_lineage_from_recorded_execution_evidence():
    """
    An envelope sealed without lineage must be restorable from recorded evidence.

    This reconstructs a fact the runner already wrote into
    ``_execution_lineage``; it must not invent a parent list from acceptance
    order. (The envelope is re-sealed from the stripped state, which is the
    legacy-import case; a *modified* already-sealed envelope is refused unless
    ``--force`` is given.)
    """
    import subprocess

    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_lineage_seal_"))
    try:
        _run(tmp, ticker="SEALED")
        # Simulate legacy envelopes that were sealed while carrying no lineage:
        # clear BOTH the upstream and the stale checksum before re-sealing.
        for path in tmp.glob("*_envelope.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("upstream", None)
            data.pop("content_checksum", None)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        seal = subprocess.run(
            [str(PROJECT_ROOT / ".venv/bin/python"), "-m", "src.controller", "seal", str(tmp)],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert seal.returncode == 0, seal.stderr

        # A3 restored to exactly the parent it actually consumed.
        a3 = json.loads((tmp / "a3_envelope.json").read_text(encoding="utf-8"))
        a1 = json.loads((tmp / "a1_envelope.json").read_text(encoding="utf-8"))
        assert [entry["agent"] for entry in a3["upstream"]] == ["A1"]
        assert a3["upstream"][0]["content_checksum"] == a1["content_checksum"]

        # Every restored parent reference converges to the real sealed digest.
        digests = {}
        for agent in ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
            data = json.loads((tmp / f"{agent.lower()}_envelope.json").read_text(encoding="utf-8"))
            digests[agent] = data["content_checksum"]
        for agent in digests:
            data = json.loads((tmp / f"{agent.lower()}_envelope.json").read_text(encoding="utf-8"))
            for entry in data.get("upstream", []):
                assert entry["content_checksum"] == digests[entry["agent"]], (
                    f"{agent} -> {entry['agent']} did not converge to the sealed digest"
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)