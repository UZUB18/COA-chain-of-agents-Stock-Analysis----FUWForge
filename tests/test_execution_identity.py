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
    EXECUTION_IDENTITY_VERSION,
    OUTPUT_SHAPING_MODULES,
    UpstreamRef,
    agent_code_identity,
    capture_upstream,
    execution_key,
    framework_code_digest,
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


# ----------------------------------------- Task 3.1: code identity & versioning


def test_execution_identity_version_and_declared_modules():
    expected_modules = (
        "agents/base.py",
        "agents/mock.py",
        "agents/model.py",
        "agents/output_format.py",
        "agents/output_schema.py",
        "agents/registry.py",
        "calculation_witness.py",
        "contracts.py",
        "data_gaps.py",
        "fact_binding.py",
        "forensic_inputs.py",
        "utils/beneish.py",
        "utils/finance.py",
    )
    assert EXECUTION_IDENTITY_VERSION == 2
    assert tuple(OUTPUT_SHAPING_MODULES) == expected_modules
    d1 = framework_code_digest()
    d2 = framework_code_digest()
    assert d1 == d2
    assert len(d1) == 64


@pytest.mark.parametrize(
    "module_rel_path",
    (
        "agents/base.py",
        "agents/mock.py",
        "agents/model.py",
        "agents/output_format.py",
        "agents/output_schema.py",
        "agents/registry.py",
        "calculation_witness.py",
        "contracts.py",
        "data_gaps.py",
        "fact_binding.py",
        "forensic_inputs.py",
        "utils/beneish.py",
        "utils/finance.py",
    ),
)
def test_declared_core_module_change_invalidates_execution_identity(
    module_rel_path, monkeypatch, tmp_path
):
    import src.execution_identity as ei_mod

    ei_mod.framework_code_digest.cache_clear()
    base_digest = ei_mod.framework_code_digest()
    orig_reader = ei_mod._read_package_module_bytes

    run1 = tmp_path / "run1"
    _run(run1, ticker="MODKEY")
    raw1 = json.loads((run1 / "a1_raw.json").read_text(encoding="utf-8"))
    assert raw1["identity_version"] == 2
    assert raw1["code_sha256"] == base_digest

    def patched_reader(rel_path: str) -> bytes:
        data = orig_reader(rel_path)
        if rel_path == module_rel_path:
            return data + b"\n# mutated byte\n"
        return data

    monkeypatch.setattr(ei_mod, "_read_package_module_bytes", patched_reader)
    ei_mod.framework_code_digest.cache_clear()
    try:
        mutated_digest = ei_mod.framework_code_digest()
        assert mutated_digest != base_digest

        run2 = tmp_path / "run2"
        _run(run2, ticker="MODKEY")
        raw2 = json.loads((run2 / "a1_raw.json").read_text(encoding="utf-8"))
        assert raw2["code_sha256"] == mutated_digest
        assert raw2["request_sha256"] != raw1["request_sha256"]
    finally:
        ei_mod.framework_code_digest.cache_clear()


def test_unlisted_module_change_does_not_invalidate_execution_identity(monkeypatch):
    import src.execution_identity as ei_mod

    ei_mod.framework_code_digest.cache_clear()
    base_digest = ei_mod.framework_code_digest()
    orig_reader = ei_mod._read_package_module_bytes

    def patched_reader(rel_path: str) -> bytes:
        if rel_path == "runtime_metrics.py":
            return b"# mutated unlisted module"
        return orig_reader(rel_path)

    monkeypatch.setattr(ei_mod, "_read_package_module_bytes", patched_reader)
    ei_mod.framework_code_digest.cache_clear()
    try:
        assert ei_mod.framework_code_digest() == base_digest
    finally:
        ei_mod.framework_code_digest.cache_clear()


def test_source_backed_custom_agent_identity_has_no_absolute_path_and_tracks_edits(
    tmp_path,
):
    import importlib.util

    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    code_v1 = (
        "from src.agents.base import BaseAgent\n"
        "class MyCustomA1(BaseAgent):\n"
        "    id = 'A1'\n"
        "    name = 'Harvester-&-Validator'\n"
        "    def run(self, context):\n"
        "        return None\n"
    )
    mod_a = dir_a / "custom_mod_a.py"
    mod_b = dir_b / "custom_mod_b.py"
    mod_a.write_text(code_v1, encoding="utf-8")
    mod_b.write_text(code_v1, encoding="utf-8")

    def _load(path: Path, mod_name: str):
        spec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    m1 = _load(mod_a, "test_custom_mod_a")
    m2 = _load(mod_b, "test_custom_mod_b")
    try:
        digest_a, hashable_a = agent_code_identity(m1.MyCustomA1())
        digest_b, hashable_b = agent_code_identity(m2.MyCustomA1())
        assert hashable_a is True
        assert hashable_b is True
        # Identical source at different filesystem paths must produce the same digest.
        assert digest_a == digest_b
        assert digest_a != framework_code_digest()

        # Modifying the source file changes the custom agent's code digest.
        mod_a.write_text(code_v1 + "\n# extra comment\n", encoding="utf-8")
        m1_v2 = _load(mod_a, "test_custom_mod_a_v2")
        digest_a_v2, hashable_a_v2 = agent_code_identity(m1_v2.MyCustomA1())
        assert hashable_a_v2 is True
        assert digest_a_v2 != digest_a
    finally:
        for name in ("test_custom_mod_a", "test_custom_mod_b", "test_custom_mod_a_v2"):
            sys.modules.pop(name, None)


def test_dynamic_factory_and_zipimported_custom_agents_are_unhashable(tmp_path):
    import zipfile
    from src.agents.base import BaseAgent

    dyn_cls = type(
        "DynamicCustomA1",
        (BaseAgent,),
        {"id": "A1", "name": "Harvester-&-Validator", "run": lambda self, ctx: None},
    )
    d_dyn1, h_dyn1 = agent_code_identity(dyn_cls())
    d_dyn2, h_dyn2 = agent_code_identity(dyn_cls())
    assert h_dyn1 is False
    assert h_dyn2 is False
    assert d_dyn1 == d_dyn2  # deterministic, never random

    # Factory-registered agent is marked unhashable even if it returns a built-in class.
    from src.agents.registry import make_mock_agent_cls

    mock_a1 = make_mock_agent_cls("A1", "Harvester-&-Validator")()
    _, h_builtin = agent_code_identity(mock_a1, is_factory=False)
    _, h_factory = agent_code_identity(mock_a1, is_factory=True)
    assert h_builtin is True
    assert h_factory is False

    # Zip-imported custom agent fails closed (hashable=False) without crashing.
    zip_path = tmp_path / "custom_agents.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "zip_custom_agent.py",
            "from src.agents.base import BaseAgent\n"
            "class ZipAgent(BaseAgent):\n"
            "    id = 'A1'\n"
            "    name = 'Harvester-&-Validator'\n"
            "    def run(self, context):\n"
            "        return None\n",
        )
    sys.path.insert(0, str(zip_path))
    try:
        import zip_custom_agent  # type: ignore

        _, h_zip = agent_code_identity(zip_custom_agent.ZipAgent())
        assert h_zip is False
    finally:
        sys.path.remove(str(zip_path))
        sys.modules.pop("zip_custom_agent", None)


def test_installed_mode_prompt_uses_relative_contract_doc_path(monkeypatch, tmp_path):
    import src.resources as res_mod
    from src.agents.base import AgentContext
    from src.agents.model import ModelAgent

    class _DummyBackend:
        model = "dummy-model"

        def complete(self, prompt: str) -> str:
            return "{}"

    monkeypatch.setattr(res_mod, "_CHECKOUT_ROOT", tmp_path / "no_checkout")
    assert not res_mod.is_checkout_workspace()

    agent = ModelAgent("A1", "Harvester-&-Validator", backend=_DummyBackend())
    ctx = AgentContext(
        run_id="r1",
        params={"company": "Test", "ticker": "TST", "analysis_date": "2026-01-01"},
        depth_config={},
        focus_overrides={},
        frozen_checksum="",
        workdir=tmp_path,
        provenance_path=tmp_path / "p.log",
        envelopes={},
    )
    prompt = agent.build_prompt(ctx)
    assert "Authoritative Contract: docs/contracts/A1_GOD_Gate.md" in prompt
    assert "_packaged_resources" not in prompt