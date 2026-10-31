"""
Independent semantic and calculation audit (Task 4).

The existing integrity audit answers one question: *do the stored bytes match
their recorded hashes?* That is necessary and it is not sufficient. Every case
below is a run that passes hash verification and still must not be reported as
fully verified:

- duplicate role IDs, and missing roles in an otherwise "completed" run
- a parent hash swapped to another valid envelope
- a final report edited after sealing
- a source unit changed underneath a recorded hash
- a quantitative artifact that is perfectly well-shaped but arithmetically wrong

The audit therefore reports independent levels, and ``verify_run`` may not claim
success at the calculation level just because envelope hashes passed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run_audit import (
    AUDIT_LEVELS,
    AuditReport,
    verify_run,
    verify_run_semantic,
    semantic_audit,
)
from src.agents.base import BaseAgent
from src.agents.mock import A7Mock
from src.utils.beneish import beneish_score

ALL_AGENTS = ["A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"]


def _good_run(tmp: Path, ticker: str = "SEM") -> Path:
    """Produce a real mock run; every damaged variant copies it first."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Semantic Co",
        ticker=ticker,
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    orchestrator.run("semantic_run", params, output_root=tmp)
    return tmp


def _reseal(path: Path) -> None:
    """Recompute the content checksum after a deliberate edit.

    Used to model a *consistent* tamper: the attacker edits content and
    re-seals, so hash verification passes and only semantic checks can catch it.
    """
    from src.utils.checksum import envelope_checksum

    data = json.loads(path.read_text(encoding="utf-8"))
    data["content_checksum"] = envelope_checksum(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _envelope_path(run_dir: Path, agent: str) -> Path:
    return run_dir / f"{agent.lower()}_envelope.json"


def _run_seal(run_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "src.controller",
            "seal",
            str(run_dir),
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _snapshot_run(run_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(run_dir)): path.read_bytes()
        for path in run_dir.rglob("*")
        if path.is_file()
    }


def _run_with_injected_default(tmp: Path) -> Path:
    _good_run(tmp)
    path = _envelope_path(tmp, "A7")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("metrics", {})["injected_defaults"] = ["test-default"]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reseal(path)
    manifest_path = tmp / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["roles"]["A7"] = json.loads(path.read_text(encoding="utf-8"))["content_checksum"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tmp


def _run_with_unexpected_section(tmp: Path) -> Path:
    _good_run(tmp)
    path = _envelope_path(tmp, "A7")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sections_md"]["SECTION_999_UNEXPECTED"] = "Unexpected content."
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reseal(path)
    manifest_path = tmp / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["roles"]["A7"] = json.loads(path.read_text(encoding="utf-8"))["content_checksum"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tmp


def _run_with_documented_data_gap(tmp: Path) -> Path:
    _good_run(tmp)
    path = _envelope_path(tmp, "A7")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["artifacts"]["comparison_fields"]["quality_score_10"] = None
    data.setdefault("metrics", {})["missing_data"] = [
        {
            "field": "comparison_fields.quality_score_10",
            "reason": "The comparison quality score was not disclosed.",
        }
    ]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reseal(path)
    manifest_path = tmp / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["roles"]["A7"] = json.loads(path.read_text(encoding="utf-8"))[
        "content_checksum"
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tmp


def _halt_before_a6(tmp: Path) -> Path:
    """Interrupt a real pipeline after A1-A5/A6B/A6R have been accepted."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    class LateFailureAgent(BaseAgent):
        id = "A6"
        name = "CIO-Synthesis"

        def run(self, context):
            raise RuntimeError("late dispatch failure")

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    orchestrator.registry.register_instance(LateFailureAgent())
    params = OrchestratorParams(
        company="Halt Co",
        ticker="HALT",
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    with pytest.raises(RuntimeError, match="late dispatch failure"):
        orchestrator.run("halted_partial", params, output_root=tmp)
    return tmp


def _halt_at_a1_gate(tmp: Path) -> Path:
    """Produce the real unaccepted A1 diagnostic written by a gate rejection."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    params = OrchestratorParams(
        company="Halt Co",
        ticker="HALT",
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    params.__dict__["fail_gate"] = True
    with pytest.raises(RuntimeError, match="A1 gate failure halts the run"):
        orchestrator.run("halted_a1", params, output_root=tmp)
    return tmp


def _halt_at_a7_gate(tmp: Path) -> Path:
    """Leave an unaccepted rejected A7 diagnostic after all earlier roles passed."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    class RejectedFinalAgent(A7Mock):
        def run(self, context):
            output = super().run(context)
            output.envelope.gates = {
                "passed": False,
                "notes": ["Final assembly contract rejected."],
            }
            return output

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    orchestrator.registry.register_instance(RejectedFinalAgent())
    params = OrchestratorParams(
        company="Halt Co",
        ticker="HALT",
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    with pytest.raises(RuntimeError, match="A7 hard gate failure halts the run"):
        orchestrator.run("halted_a7", params, output_root=tmp)
    return tmp


def _halt_during_a1_dispatch(tmp: Path) -> Path:
    """Halt after dispatch starts but before A1 can produce any envelope."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    class FirstAgentFailure(BaseAgent):
        id = "A1"
        name = "Harvester-&-Validator"

        def run(self, context):
            raise RuntimeError("failure before first envelope")

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")
    orchestrator.registry.register_instance(FirstAgentFailure())
    params = OrchestratorParams(
        company="Halt Co",
        ticker="HALT",
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    with pytest.raises(RuntimeError, match="failure before first envelope"):
        orchestrator.run("halted_empty", params, output_root=tmp)
    return tmp


def _halt_during_output_assembly(tmp: Path) -> Path:
    """Accept all agents, then halt before final report/artifact assembly."""
    from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams

    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock")

    def fail_assembly(*args, **kwargs):
        raise RuntimeError("final output assembly failed")

    orchestrator._write_outputs = fail_assembly
    params = OrchestratorParams(
        company="Halt Co",
        ticker="HALT",
        analysis_date="2026-02-01",
        depth="Standard",
        focus=[],
        weights="default",
    )
    with pytest.raises(RuntimeError, match="final output assembly failed"):
        orchestrator.run("halted_assembly", params, output_root=tmp)
    return tmp


# ------------------------------------------------------------------ good run


def test_good_run_is_clean_at_every_level():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_good_"))
    try:
        _good_run(tmp)
        report = semantic_audit(tmp)
        assert report.valid, report.errors
        assert set(report.levels) == set(AUDIT_LEVELS)
        # A mock run supplies no evidence pack, so source reconstruction is
        # honestly "not applicable" rather than a pass. Every level that has
        # something to check must actually pass.
        assert report.levels["source_reconstruction"] == "not_applicable"
        for level in (
            "envelope_integrity",
            "graph_and_contracts",
            "calculation_replay",
            "final_artifacts",
            "completeness",
        ):
            assert report.levels[level] == "passed", (level, report.levels)
        assert report.overall_state == "verified"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_completed_run_requires_the_exact_expected_role_set():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_roles_"))
    try:
        _good_run(tmp)
        complete = semantic_audit(tmp)
        assert complete.complete
        assert not complete.levels["graph_and_contracts"] != "passed"

        # Remove a role from an otherwise completed run.
        _envelope_path(tmp, "A5").unlink()
        report = semantic_audit(tmp)
        assert not report.valid
        assert any("A5" in err for err in report.errors)
        assert "graph_and_contracts" not in report.levels or report.levels[
            "graph_and_contracts"
        ] != "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- graph level


def test_duplicate_role_ids_are_rejected():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_dup_"))
    try:
        _good_run(tmp)
        # Write a second envelope claiming to be A3.
        original = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        (tmp / "a3_copy_envelope.json").write_text(
            json.dumps(original, indent=2), encoding="utf-8"
        )
        report = semantic_audit(tmp)
        assert not report.valid
        assert any("duplicate" in err.lower() and "A3" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_swapped_parent_hash_is_caught_by_graph_validation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_swap_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        # Point A3 at A2's real checksum: a plausible-looking, self-consistent
        # lie that hash verification alone cannot detect.
        a2 = json.loads(_envelope_path(tmp, "A2").read_text(encoding="utf-8"))
        a3["upstream"] = [
            {"agent": "A2", "content_checksum": a2["content_checksum"]}
        ]
        _envelope_path(tmp, "A3").write_text(json.dumps(a3, indent=2), encoding="utf-8")
        _reseal(_envelope_path(tmp, "A3"))

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("graph_and_contracts") != "passed"
        # A3 only ever consumed A1 in the parallel phase.
        assert any("A3" in err and "A2" in err for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_parent_is_caught_by_graph_validation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_missing_parent_"))
    try:
        _good_run(tmp)
        path = _envelope_path(tmp, "A3")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["upstream"] = [{"agent": "Z9", "content_checksum": "sha256:" + "0" * 64}]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["graph_and_contracts"] == "failed"
        assert any(
            "upstream Z9 referenced but its envelope is missing" in error
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("bad_agent", [42, []], ids=["integer", "list"])
def test_malformed_agent_container_never_raises(bad_agent):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_agent_shape_"))
    try:
        _good_run(tmp)
        path = _envelope_path(tmp, "A3")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["agent"] = bad_agent
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        integrity = verify_run(tmp)
        assert not integrity.valid
        assert any(
            "agent" in error.lower() and "object" in error.lower()
            for error in integrity.errors
        ), integrity.errors

        report = semantic_audit(tmp)
        assert report.levels["envelope_integrity"] == "failed"
        assert any(
            "agent" in error.lower() and "object" in error.lower()
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_OBJECT_FIELDS = ("params", "gates", "sections_md", "artifacts", "metrics")
_ARRAY_FIELDS = ("sources", "warnings", "upstream")
# ``dict()``/``list()`` raise on the first three of each and silently reshape the
# rest (a list of pairs becomes a dict, a string becomes its characters).
_WRONG_FOR_OBJECT = {
    "integer": 42,
    "null": None,
    "list": [],
    "pairs": [["key", "value"]],
    "string": "text",
}
_WRONG_FOR_ARRAY = {
    "integer": 42,
    "null": None,
    "object": {},
    "keys": {"key": "value"},
    "string": "text",
}
_MALFORMED_CONTAINER_CASES = [
    pytest.param(field, bad, id=f"{field}-{label}")
    for field, wrong in (
        *((name, _WRONG_FOR_OBJECT) for name in _OBJECT_FIELDS),
        *((name, _WRONG_FOR_ARRAY) for name in _ARRAY_FIELDS),
    )
    for label, bad in wrong.items()
]


def _run_with_malformed_a1_field(run_dir: Path, field: str, bad) -> Path:
    _good_run(run_dir)
    path = _envelope_path(run_dir, "A1")
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = bad
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return run_dir


def _assert_malformed_field_reported(errors, field: str) -> None:
    assert any(
        error.startswith("a1_envelope.json: ")
        and field in error
        and "must be an" in error
        for error in errors
    ), errors
    # The schema's own finding is kept, not replaced by the structural one.
    assert any(
        error.startswith("a1_envelope.json: ") and "is not of type" in error
        for error in errors
    ), errors


@pytest.mark.parametrize("field,bad", _MALFORMED_CONTAINER_CASES)
def test_verify_run_reports_malformed_envelope_container(tmp_path, field, bad):
    run_dir = _run_with_malformed_a1_field(tmp_path / "run", field, bad)

    result = verify_run(run_dir)

    assert not result.valid
    _assert_malformed_field_reported(result.errors, field)


@pytest.mark.parametrize("field,bad", _MALFORMED_CONTAINER_CASES)
def test_semantic_audit_reports_malformed_envelope_container(tmp_path, field, bad):
    run_dir = _run_with_malformed_a1_field(tmp_path / "run", field, bad)

    report = semantic_audit(run_dir)

    assert report.levels["envelope_integrity"] == "failed"
    assert "envelope_integrity" in report.failed_levels()
    assert report.overall_state == "failed"
    _assert_malformed_field_reported(report.errors, field)


@pytest.mark.parametrize("entry_point", [verify_run, semantic_audit])
def test_malformed_envelope_container_does_not_mask_independent_failures(
    tmp_path, entry_point
):
    run_dir = _run_with_malformed_a1_field(tmp_path / "run", "metrics", 42)
    a3_path = _envelope_path(run_dir, "A3")
    a3 = json.loads(a3_path.read_text(encoding="utf-8"))
    a3["sections_md"]["TAMPERED"] = "changed after sealing"
    a3_path.write_text(json.dumps(a3, indent=2), encoding="utf-8")
    prov_path = next(run_dir.glob("*_provenance.log"))
    lines = prov_path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("accepted", "rewritten")
    prov_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    outcome = entry_point(run_dir)
    errors = outcome.errors

    assert any(
        error.startswith("a1_envelope.json: ") and "metrics" in error
        for error in errors
    ), errors
    assert any(
        "a3_envelope.json" in error and "content checksum mismatch" in error
        for error in errors
    ), errors
    assert any(
        "provenance" in error and "mismatch" in error for error in errors
    ), errors


def _set_first_section(data, value):
    data["sections_md"][sorted(data["sections_md"])[0]] = value


_MALFORMED_ENTRY_CASES = [
    pytest.param(lambda d: d.update(sources=[42]), id="sources-integer-entry"),
    pytest.param(lambda d: d.update(sources=[None]), id="sources-null-entry"),
    pytest.param(lambda d: d.update(sources=["text"]), id="sources-string-entry"),
    pytest.param(lambda d: d["sources"].append(42), id="sources-extra-integer-entry"),
    pytest.param(lambda d: d.update(upstream=[42]), id="upstream-integer-entry"),
    pytest.param(lambda d: d.update(upstream=[None]), id="upstream-null-entry"),
    pytest.param(lambda d: d.update(warnings=[42]), id="warnings-integer-entry"),
    pytest.param(lambda d: _set_first_section(d, 42), id="sections-integer-value"),
    pytest.param(lambda d: _set_first_section(d, None), id="sections-null-value"),
]


@pytest.mark.parametrize("entry_point", [verify_run, semantic_audit])
@pytest.mark.parametrize("mutate", _MALFORMED_ENTRY_CASES)
def test_malformed_envelope_entries_are_reported_not_raised(
    tmp_path, entry_point, mutate
):
    run_dir = _good_run(tmp_path / "run")
    path = _envelope_path(run_dir, "A1")
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    outcome = entry_point(run_dir)

    assert any(
        error.startswith("a1_envelope.json: ") and "is not of type" in error
        for error in outcome.errors
    ), outcome.errors
    if entry_point is semantic_audit:
        assert outcome.levels["envelope_integrity"] == "failed"
    else:
        assert not outcome.valid


def _beneish_block_with_section(section):
    def mutate(data):
        data["metrics"]["runner_beneish_block"] = True
        data["sections_md"]["SECTION_4_FINANCIAL_FORENSICS"] = section

    return mutate


# Values inside otherwise well-shaped containers. Each used to raise from deep
# inside an audit; ``expected`` is the finding that must be reported instead.
_INNER_CORRUPTION_CASES = [
    pytest.param(
        "A3",
        lambda d: d["artifacts"]["_execution_lineage"].update(consumed_upstream=42),
        "consumed_upstream",
        id="lineage-consumed-upstream-integer",
    ),
    pytest.param(
        "A3",
        lambda d: d["artifacts"]["_execution_lineage"].update(consumed_upstream=True),
        "consumed_upstream",
        id="lineage-consumed-upstream-boolean",
    ),
    pytest.param(
        "A3",
        lambda d: d["upstream"][0].update(agent=[1]),
        "is not of type 'string'",
        id="upstream-agent-list",
    ),
    pytest.param(
        "A3",
        lambda d: d["upstream"][0].update(agent={"a": 1}),
        "is not of type 'string'",
        id="upstream-agent-object",
    ),
    pytest.param(
        "A3",
        _beneish_block_with_section(42),
        "report Beneish block does not match",
        id="beneish-section-integer",
    ),
    pytest.param(
        "A3",
        _beneish_block_with_section(None),
        "report Beneish block does not match",
        id="beneish-section-null",
    ),
    pytest.param(
        "A2",
        lambda d: d["metrics"].update(injected_defaults=42),
        "injected defaults",
        id="injected-defaults-integer",
    ),
]


@pytest.mark.parametrize("role,mutate,expected", _INNER_CORRUPTION_CASES)
def test_malformed_values_inside_envelope_containers_are_reported_not_raised(
    tmp_path, role, mutate, expected
):
    run_dir = _good_run(tmp_path / "run")
    path = _envelope_path(run_dir, role)
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _reseal(path)

    integrity = verify_run(run_dir, strict=True)
    report = semantic_audit(run_dir, strict=True)

    assert any(expected in error for error in integrity.errors + report.errors), (
        integrity.errors,
        report.errors,
    )
    assert report.overall_state == "failed"


def test_malformed_run_summary_is_not_an_audit_crash(tmp_path):
    payloads = ("[]", "42", "null", '{"params": 42}', '{"params": [1]}')
    for index, payload in enumerate(payloads):
        run_dir = _good_run(tmp_path / f"run{index}")
        (run_dir / "run_summary.json").write_text(payload, encoding="utf-8")
        verify_run(run_dir)
        report = semantic_audit(run_dir)
        # The manifest pinned the summary's bytes, so the edit is still caught.
        assert report.levels["final_artifacts"] == "failed", payload
    run_dir = _good_run(tmp_path / "binary")
    (run_dir / "run_summary.json").write_bytes(b"\xff\xfe not utf-8")
    assert semantic_audit(run_dir).levels["final_artifacts"] == "failed"


def test_malformed_envelope_field_is_attributed_to_the_damaged_file(tmp_path):
    """Two damaged envelopes are both named; neither hides the other."""
    run_dir = _good_run(tmp_path / "run")
    for agent, field, bad in (("A2", "gates", 42), ("A4", "sources", "text")):
        path = _envelope_path(run_dir, agent)
        data = json.loads(path.read_text(encoding="utf-8"))
        data[field] = bad
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    for errors in (verify_run(run_dir).errors, semantic_audit(run_dir).errors):
        assert any(
            e.startswith("a2_envelope.json: ") and "gates must be an object" in e
            for e in errors
        ), errors
        assert any(
            e.startswith("a4_envelope.json: ") and "sources must be an array" in e
            for e in errors
        ), errors


def test_unsealed_parent_is_warned_by_graph_validation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_unsealed_"))
    try:
        _good_run(tmp)
        a1_path = _envelope_path(tmp, "A1")
        a1 = json.loads(a1_path.read_text(encoding="utf-8"))
        a1.pop("content_checksum", None)
        a1_path.write_text(json.dumps(a1, indent=2), encoding="utf-8")

        integrity = verify_run(tmp)
        expected_unsealed = [
            warning
            for warning in integrity.warnings
            if "upstream A1 referenced but unsealed" in warning
        ]
        assert expected_unsealed

        report = semantic_audit(tmp)
        unsealed = [
            warning
            for warning in report.warnings
            if "upstream A1 referenced but unsealed" in warning
        ]
        assert unsealed == expected_unsealed
        assert "graph_and_contracts" not in report.failed_levels()

        strict_report = semantic_audit(tmp, strict=True)
        assert "envelope_integrity" in strict_report.failed_levels()
        assert "graph_and_contracts" in strict_report.failed_levels()

        strict_wrapper = verify_run_semantic(tmp, strict=True)
        assert not strict_wrapper.valid
        assert any(
            "upstream A1 referenced but unsealed" in warning
            for warning in strict_wrapper.warnings
        )

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cli.returncode != 0, cli.stdout + cli.stderr
        audit = json.loads(cli.stdout)
        assert {"envelope_integrity", "graph_and_contracts"}.issubset(
            audit["failed_levels"]
        ), audit
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mismatched_frozen_data_checksum_fails_graph_validation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_frozen_"))
    try:
        _good_run(tmp)
        path = _envelope_path(tmp, "A5")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["metrics"]["data_checksum"] = "sha256:not-the-frozen-checksum"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _reseal(path)

        report = semantic_audit(tmp)
        assert report.levels["graph_and_contracts"] == "failed"
        assert any(
            "A5: metrics.data_checksum" in error
            and "does not echo A1 frozen checksum" in error
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- final-artifact level


def test_changed_report_after_sealing_is_caught():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_report_"))
    try:
        _good_run(tmp)
        report_path = next(tmp.glob("*_report.md"))
        original = report_path.read_text(encoding="utf-8")
        report_path.write_text(original + "\n\n## Injected conclusion\n", encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("final_artifacts") != "passed"
        assert any("report" in err.lower() for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_changed_artifact_bundle_after_sealing_is_caught():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_bundle_"))
    try:
        _good_run(tmp)
        bundle_path = next(tmp.glob("*_artifacts.json"))
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        payload["A7"]["artifacts"]["comparison_fields"]["quality_score_10"] = 9.9
        bundle_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("final_artifacts") != "passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "invalid JSON"),
        (json.dumps([]), "expected object"),
    ],
    ids=["invalid-json", "list"],
)
def test_malformed_manifest_fails_final_artifacts_without_raising(payload, message):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_manifest_shape_"))
    try:
        _good_run(tmp)
        (tmp / "run_manifest.json").write_text(payload, encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["final_artifacts"] == "failed"
        assert "final_artifacts" in report.failed_levels()
        assert any(
            "run_manifest.json is malformed" in error and message in error
            for error in report.errors
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize(
    "shape",
    ["missing-run-id", "non-string-status", "non-list-graph-row"],
)
def test_manifest_schema_error_is_attributed_to_final_artifacts(shape):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_manifest_schema_"))
    try:
        _good_run(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if shape == "missing-run-id":
            manifest.pop("run_id")
        elif shape == "non-string-status":
            manifest["status"] = []
        else:
            manifest["graph"]["A2"] = 42
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["final_artifacts"] == "failed"
        assert report.failed_levels() == ["final_artifacts"]
        assert any("run manifest:" in error for error in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize(
    "label", ["report", "artifacts", "summary", "provenance"]
)
@pytest.mark.parametrize("remove_file", [False, True], ids=["file-present", "file-absent"])
def test_completed_run_requires_manifest_bound_outputs(label, remove_file):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_required_output_"))
    try:
        _good_run(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["outputs"].pop(label)
        if remove_file:
            (tmp / record["path"]).unlink()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["final_artifacts"] == "failed"
        assert any(
            error.startswith(f"{label}: completed run is missing")
            and "manifest-bound output record" in error
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("label", ["report", "artifacts"])
def test_completed_run_requires_recorded_output_file(label):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_required_file_"))
    try:
        _good_run(tmp)
        manifest = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
        (tmp / manifest["outputs"][label]["path"]).unlink()

        report = semantic_audit(tmp)
        assert report.levels["final_artifacts"] == "failed"
        assert any(
            f"{label}: recorded output is missing" in error
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------- calculation level


def test_well_shaped_but_arithmetically_wrong_beneish_is_rejected():
    """
    Schema-valid, in-range, correctly shaped - and still wrong.

    This is the case that a "finite number in a permissible range" check passes
    and a real recomputation does not.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_math_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        beneish = a3["artifacts"]["BeneishM"]
        # Perturb the score while keeping its shape, range and classification.
        # -2.0121 is the true value for these indices; -1.71 is not.
        beneish["raw_score"] = -1.71
        beneish["score"] = -1.71
        _envelope_path(tmp, "A3").write_text(json.dumps(a3, indent=2), encoding="utf-8")
        _reseal(_envelope_path(tmp, "A3"))

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels.get("calculation_replay") != "passed"
        assert any("beneish" in err.lower() for err in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_beneish_witness_missing_is_reported_not_assumed():
    """A Beneish with a deterministic claim but no inputs cannot be replayed."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_wit_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        a3["artifacts"]["BeneishM"].pop("calculation_witness", None)
        _envelope_path(tmp, "A3").write_text(json.dumps(a3, indent=2), encoding="utf-8")
        _reseal(_envelope_path(tmp, "A3"))

        report = semantic_audit(tmp)
        assert report.levels.get("calculation_replay") != "passed"
        assert any(
            "witness" in err.lower() or "not replayable" in err.lower()
            for err in report.errors
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_correct_beneish_witness_replays_exactly():
    """The good fixture must actually be replayable, or the check is vacuous."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_replay_"))
    try:
        _good_run(tmp)
        a3 = json.loads(_envelope_path(tmp, "A3").read_text(encoding="utf-8"))
        witness = a3["artifacts"]["BeneishM"]["calculation_witness"]
        expected = beneish_score(**witness["inputs"])
        assert a3["artifacts"]["BeneishM"]["raw_score"] == pytest.approx(expected)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------- source reconstruction


def test_missing_source_bytes_are_unavailable_not_success():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_src_"))
    try:
        _good_run(tmp)
        # A run with no evidence pack must not claim source verification.
        report = semantic_audit(tmp)
        assert report.levels["source_reconstruction"] in (
            "not_applicable",
            "source_bytes_unavailable",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ halted / levels


def test_genuine_halted_subgraph_is_incomplete_without_missing_role_error():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_halt_"))
    try:
        _halt_before_a6(tmp)

        report = semantic_audit(tmp)
        assert not report.complete, "a halted run must not be reported as complete"
        assert report.halted
        assert report.levels["completeness"] == "incomplete"
        assert report.levels["final_artifacts"] == "passed"
        assert report.overall_state == "incomplete"
        assert report.failed_levels() == []
        assert not any("missing required role" in error for error in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_incomplete_status_uses_halted_role_and_output_policy():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_incomplete_status_"))
    try:
        _halt_before_a6(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "incomplete"
        manifest["complete"] = False
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.halted is False
        assert report.complete is False
        assert report.levels["completeness"] == "incomplete"
        assert report.levels["final_artifacts"] == "passed"
        assert report.overall_state == "incomplete"
        assert report.failed_levels() == []
        assert not any("missing required role" in error for error in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rejected_a1_diagnostic_halt_is_incomplete():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_rejected_a1_"))
    try:
        _halt_at_a1_gate(tmp)
        manifest = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
        a1 = json.loads(_envelope_path(tmp, "A1").read_text(encoding="utf-8"))
        assert manifest["status"] == "halted"
        assert manifest["roles"] == {}
        assert a1["gates"]["passed"] is False
        assert a1["content_checksum"] is None

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "incomplete"
        assert report.overall_state == "incomplete"
        assert report.failed_levels() == []
        assert not any(error.startswith("A1 contract:") for error in report.errors)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cli.returncode != 0, cli.stdout + cli.stderr
        audit = json.loads(cli.stdout)
        assert audit["overall_state"] == "incomplete"
        assert audit["failed_levels"] == []
        assert "no assembled report found" in audit["warnings"]
        assert "no artifacts bundle found" in audit["warnings"]
        assert any("a1_envelope.json: no content_checksum" in w for w in audit["warnings"])
        assert not any(error.startswith("A1 contract:") for error in audit["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rejected_diagnostic_does_not_fill_missing_final_role():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_rejected_a7_"))
    try:
        _halt_at_a7_gate(tmp)
        manifest = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
        a7 = json.loads(_envelope_path(tmp, "A7").read_text(encoding="utf-8"))
        assert manifest["status"] == "halted"
        assert "A7" not in manifest["roles"]
        assert a7["gates"]["passed"] is False

        report = semantic_audit(tmp)
        assert report.complete is False
        assert report.levels["completeness"] == "incomplete"
        assert report.overall_state == "incomplete"
        assert report.failed_levels() == []
        assert not any(
            "missing required role" in error for error in report.errors
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize(
    ("build_halted_run", "diagnostic_role"),
    [
        (_halt_at_a1_gate, "A1"),
        (_halt_at_a7_gate, "A7"),
    ],
    ids=["a1", "a7"],
)
@pytest.mark.parametrize("manifest_status", ["halted", "incomplete"])
def test_seal_leaves_rejected_halted_diagnostics_unaccepted(
    tmp_path, build_halted_run, diagnostic_role, manifest_status
):
    build_halted_run(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["status"] = manifest_status
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    diagnostic_path = _envelope_path(tmp_path, diagnostic_role)
    diagnostic_before = diagnostic_path.read_bytes()
    manifest_before = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    tree_before = _snapshot_run(tmp_path)
    assert diagnostic_role not in manifest_before["roles"]
    assert json.loads(diagnostic_before)["gates"]["passed"] is False

    for extra_args in ([], ["--force"]):
        sealed = _run_seal(tmp_path, *extra_args)
        assert sealed.returncode == 0, sealed.stdout + sealed.stderr
        assert _snapshot_run(tmp_path) == tree_before
        manifest_after = json.loads(
            (tmp_path / "run_manifest.json").read_text(encoding="utf-8")
        )
        assert diagnostic_role not in manifest_after["roles"]
        assert diagnostic_path.read_bytes() == diagnostic_before


def test_sealed_unrecorded_rejected_envelope_is_not_a_diagnostic(tmp_path):
    from src.utils.checksum import envelope_checksum

    _halt_at_a7_gate(tmp_path)
    a7_path = _envelope_path(tmp_path, "A7")
    a7 = json.loads(a7_path.read_text(encoding="utf-8"))
    a7["content_checksum"] = envelope_checksum(a7)
    a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
    before = _snapshot_run(tmp_path)

    refused = _run_seal(tmp_path)

    assert refused.returncode != 0
    assert "A7" in refused.stdout + refused.stderr
    assert _snapshot_run(tmp_path) == before

    forced = _run_seal(tmp_path, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    manifest = json.loads(
        (tmp_path / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert "A7" in manifest["roles"]
    assert manifest["resealed"]["added_roles"] == ["A7"]


def test_seal_partial_halted_run_does_not_invent_absent_outputs(tmp_path):
    _halt_before_a6(tmp_path)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_before = json.loads(manifest_path.read_text(encoding="utf-8"))
    tree_before = _snapshot_run(tmp_path)
    assert manifest_before["status"] == "halted"
    assert not list(tmp_path.glob("*_report.md"))
    assert not list(tmp_path.glob("*_artifacts.json"))

    sealed = _run_seal(tmp_path)

    assert sealed.returncode == 0, sealed.stdout + sealed.stderr
    assert _snapshot_run(tmp_path) == tree_before
    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest_after["outputs"]) == set(manifest_before["outputs"])
    assert not list(tmp_path.glob("*_report.md"))
    assert not list(tmp_path.glob("*_artifacts.json"))


def test_empty_halted_run_is_incomplete_with_not_applicable_integrity():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_empty_halt_"))
    try:
        _halt_during_a1_dispatch(tmp)
        manifest = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "halted"
        assert manifest["roles"] == {}
        assert not list(tmp.glob("*_envelope.json"))

        report = semantic_audit(tmp)
        assert report.complete is False
        assert report.levels["envelope_integrity"] == "not_applicable"
        assert report.levels["completeness"] == "incomplete"
        assert report.overall_state == "incomplete"
        assert report.errors == []
        for warning in (
            "no provenance log found",
            "no assembled report found",
            "no artifacts bundle found",
        ):
            assert warning in report.warnings

        for extra_args in ([], ["--strict"]):
            cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.controller",
                    "verify-run",
                    str(tmp),
                    "--json",
                    *extra_args,
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert cli.returncode != 0, cli.stdout + cli.stderr
            audit = json.loads(cli.stdout)
            assert audit["overall_state"] == "incomplete"
            assert audit["levels"]["envelope_integrity"] == "not_applicable"
            assert audit["errors"] == []
            assert "no provenance log found" in audit["warnings"]
            assert "no assembled report found" in audit["warnings"]
            assert "no artifacts bundle found" in audit["warnings"]

        integrity = verify_run(tmp)
        assert not integrity.valid
        assert "no *_envelope.json files found" in integrity.errors

        integrity_cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--integrity-only",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert integrity_cli.returncode != 0, (
            integrity_cli.stdout + integrity_cli.stderr
        )
        assert "no *_envelope.json files found" in integrity_cli.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_halted_manifest_with_recorded_role_still_fails():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_empty_recorded_"))
    try:
        _halt_during_a1_dispatch(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["roles"] = {"A1": "sha256:" + "0" * 64}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "incomplete"
        assert report.levels["envelope_integrity"] == "failed"
        assert report.levels["final_artifacts"] == "failed"
        assert report.overall_state == "failed"
        assert any(
            "manifest specifies role 'A1' which is missing" in error
            for error in report.errors
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_halted_gate_true_unrecorded_envelope_is_not_accepted():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_unrecorded_a7_"))
    try:
        _good_run(tmp)
        a7_path = _envelope_path(tmp, "A7")
        a7 = json.loads(a7_path.read_text(encoding="utf-8"))
        a7.pop("content_checksum", None)
        a7["sections_md"].pop("SECTION_0_EXEC_SUMMARY", None)
        a7_path.write_text(json.dumps(a7, indent=2), encoding="utf-8")
        assert a7["gates"]["passed"] is True

        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["roles"].pop("A7")
        manifest["status"] = "halted"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.complete is False
        assert report.levels["completeness"] == "incomplete"
        assert report.levels["graph_and_contracts"] == "failed"
        assert report.overall_state == "failed"
        assert any(
            "A7 contract" in error and "SECTION_0_EXEC_SUMMARY" in error
            for error in report.errors
        ), report.errors

        strict = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert strict.returncode != 0, strict.stdout + strict.stderr
        audit = json.loads(strict.stdout)
        assert audit["complete"] is False
        assert audit["overall_state"] == "failed"
        assert any("A7 contract" in error for error in audit["errors"])
        assert any(
            "a7_envelope.json: no content_checksum" in error
            for error in audit["errors"]
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_post_agent_assembly_halt_is_not_complete():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_assembly_halt_"))
    try:
        _halt_during_output_assembly(tmp)
        manifest = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "halted"
        assert set(manifest["roles"]) == set(ALL_AGENTS)

        report = semantic_audit(tmp)
        assert report.halted is True
        assert report.complete is False
        assert report.levels["completeness"] == "incomplete"
        assert report.overall_state == "incomplete"
        assert report.failed_levels() == []

        for extra_args in ([], ["--strict"]):
            cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.controller",
                    "verify-run",
                    str(tmp),
                    "--json",
                    *extra_args,
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert cli.returncode != 0, cli.stdout + cli.stderr
            audit = json.loads(cli.stdout)
            assert audit["complete"] is False
            assert audit["overall_state"] == "incomplete"
            assert audit["failed_levels"] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_completed_run_with_missing_role_reports_explicit_contradiction():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_missing_completed_"))
    try:
        _good_run(tmp)
        _envelope_path(tmp, "A7").unlink()

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "failed"
        assert report.overall_state == "failed"
        assert "completeness" in report.failed_levels()
        assert any(
            "A7" in error
            and "missing required role" in error
            and "completed manifest" in error
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_role_without_manifest_reports_explicit_contradiction():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_missing_manifest_"))
    try:
        _good_run(tmp)
        _envelope_path(tmp, "A7").unlink()
        (tmp / "run_manifest.json").unlink()

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "failed"
        assert report.overall_state == "failed"
        assert "completeness" in report.failed_levels()
        assert any(
            "A7" in error
            and "missing required role" in error
            and "manifest is absent" in error
            for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_halted_incomplete_state_survives_strict_cli_json():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_halt_cli_"))
    try:
        _halt_before_a6(tmp)
        for extra_args in ([], ["--strict"]):
            cli = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.controller",
                    "verify-run",
                    str(tmp),
                    "--json",
                    *extra_args,
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert cli.returncode != 0, cli.stdout + cli.stderr
            audit = json.loads(cli.stdout)
            assert audit["levels"]["completeness"] == "incomplete"
            assert audit["overall_state"] == "incomplete"
            assert audit["failed_levels"] == []
            assert not any(
                "missing required role" in error for error in audit["errors"]
            )
            assert "strict: run is incomplete" not in audit["errors"]
            assert "no assembled report found" in audit["warnings"]
            assert "no artifacts bundle found" in audit["warnings"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_independent_integrity_failure_still_fails_halted_run():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_halt_tamper_"))
    try:
        _halt_before_a6(tmp)
        path = _envelope_path(tmp, "A6B")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sections_md"]["TAMPERED"] = "changed after sealing"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "incomplete"
        assert report.overall_state == "failed"
        assert "envelope_integrity" in report.failed_levels()
        assert not any("missing required role" in error for error in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_manifest_role_contradiction_still_fails_halted_run():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_halt_manifest_"))
    try:
        _halt_before_a6(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["roles"]["A6B"] = "sha256:" + "0" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "incomplete"
        assert report.overall_state == "failed"
        assert "final_artifacts" in report.failed_levels()
        assert any(
            "manifest checksum for role 'A6B'" in error for error in report.errors
        ), report.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unexpected_role_still_fails_halted_run():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_halt_unexpected_"))
    try:
        _halt_before_a6(tmp)
        data = json.loads(_envelope_path(tmp, "A6B").read_text(encoding="utf-8"))
        data["agent"]["id"] = "Z9"
        path = tmp / "z9_envelope.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _reseal(path)

        report = semantic_audit(tmp)
        assert report.levels["completeness"] == "failed"
        assert report.overall_state == "failed"
        assert any("unexpected role(s): Z9" in error for error in report.errors)
        assert sum(
            "unexpected role" in error.lower() for error in report.errors
        ) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_levels_are_reported_independently():
    """The whole point: one reassuring word is not an audit result."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_levels_"))
    try:
        _good_run(tmp)
        report = semantic_audit(tmp)
        assert isinstance(report, AuditReport)
        for level in AUDIT_LEVELS:
            assert level in report.levels
        assert report.overall_state in {
            "verified",
            "incomplete",
            "failed",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_run_semantic_is_a_thin_compatible_wrapper():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_compat_"))
    try:
        _good_run(tmp)
        result = verify_run_semantic(tmp, strict=False)
        assert result.valid, result.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize(
    "halt",
    [
        _halt_before_a6,
        _halt_at_a1_gate,
        _halt_at_a7_gate,
        _halt_during_a1_dispatch,
    ],
    ids=["partial-halt", "a1-gate-halt", "a7-gate-halt", "empty-halt"],
)
def test_strict_wrapper_rejects_incomplete_run_while_report_stays_honest(
    tmp_path, halt
):
    run_dir = halt(tmp_path / "run")

    report = semantic_audit(run_dir, strict=True)
    assert report.overall_state == "incomplete"
    assert report.failed_levels() == []
    assert "strict: run is incomplete" not in report.errors

    assert verify_run_semantic(run_dir, strict=False).valid

    strict = verify_run_semantic(run_dir, strict=True)
    assert not strict.valid
    assert "strict: run is incomplete" in strict.errors

    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.controller",
            "verify-run",
            str(run_dir),
            "--strict",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert cli.returncode != 0, cli.stdout + cli.stderr
    audit = json.loads(cli.stdout)
    assert audit["overall_state"] == "incomplete"
    assert audit["failed_levels"] == []
    assert "strict: run is incomplete" not in audit["errors"]


def test_strict_wrapper_accepts_a_complete_run(tmp_path):
    run_dir = _good_run(tmp_path / "run")

    result = verify_run_semantic(run_dir, strict=True)

    assert result.valid, result.errors
    assert "strict: run is incomplete" not in result.errors


def test_lenient_audit_reports_contract_warnings_without_failing_graph():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_contract_warn_"))
    try:
        _run_with_injected_default(tmp)
        report = semantic_audit(tmp)

        assert report.levels["graph_and_contracts"] == "passed"
        assert any("injected defaults" in warning for warning in report.warnings)
        assert not any("injected defaults" in error for error in report.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_strict_contract_mode_is_enforced_across_entry_points():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_contract_strict_"))
    try:
        _run_with_injected_default(tmp)

        report = semantic_audit(tmp, strict=True)
        assert report.levels["graph_and_contracts"] == "failed"
        assert any("injected defaults" in error for error in report.errors)

        result = verify_run_semantic(tmp, strict=True)
        assert not result.valid
        assert any("injected defaults" in error for error in result.errors)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cli.returncode != 0, cli.stdout + cli.stderr
        audit = json.loads(cli.stdout)
        assert audit["levels"]["graph_and_contracts"] == "failed"
        assert any("injected defaults" in error for error in audit["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_strict_unexpected_section_warning_fails_graph_across_entry_points():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_contract_warn_strict_"))
    try:
        _run_with_unexpected_section(tmp)
        expected = "section 'SECTION_999_UNEXPECTED' is not part of agent A7's contract"

        report = semantic_audit(tmp, strict=True)
        assert report.levels["graph_and_contracts"] == "failed"
        assert "graph_and_contracts" in report.failed_levels()
        assert any(expected in warning for warning in report.warnings)
        assert any(expected in error for error in report.errors)

        result = verify_run_semantic(tmp, strict=True)
        assert not result.valid
        assert any(expected in warning for warning in result.warnings)
        assert any(expected in error for error in result.errors)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cli.returncode != 0, cli.stdout + cli.stderr
        audit = json.loads(cli.stdout)
        assert "graph_and_contracts" in audit["failed_levels"]
        assert any(expected in warning for warning in audit["warnings"])
        assert any(expected in error for error in audit["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_strict_documented_data_gap_remains_warning_across_entry_points():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_contract_gap_strict_"))
    try:
        _run_with_documented_data_gap(tmp)
        expected = "Explicit unavailable research inputs: comparison_fields.quality_score_10"

        report = semantic_audit(tmp, strict=True)
        assert report.valid, report.errors
        assert report.levels["graph_and_contracts"] == "passed"
        assert any(expected in warning for warning in report.warnings)
        assert not any(expected in error for error in report.errors)

        result = verify_run_semantic(tmp, strict=True)
        assert result.valid, result.errors
        assert any(expected in warning for warning in result.warnings)
        assert not any(expected in error for error in result.errors)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cli.returncode == 0, cli.stdout + cli.stderr
        audit = json.loads(cli.stdout)
        assert audit["levels"]["graph_and_contracts"] == "passed"
        assert any(expected in warning for warning in audit["warnings"])
        assert not any(expected in error for error in audit["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_strict_warning_only_run_fails_its_owner_level_across_entry_points():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_warning_only_"))
    try:
        _halt_before_a6(tmp)
        manifest_path = tmp / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        provenance = manifest["outputs"].pop("provenance", None)
        if isinstance(provenance, dict):
            (tmp / provenance["path"]).unlink(missing_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        report = semantic_audit(tmp, strict=True)
        assert "envelope_integrity" in report.failed_levels()
        assert any("no provenance log found" in error for error in report.errors)

        wrapper = verify_run_semantic(tmp, strict=True)
        assert not wrapper.valid
        assert any("no provenance log found" in error for error in wrapper.errors)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.controller",
                "verify-run",
                str(tmp),
                "--strict",
                "--json",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert cli.returncode != 0, cli.stdout + cli.stderr
        audit = json.loads(cli.stdout)
        assert "envelope_integrity" in audit["failed_levels"]
        assert any("no provenance log found" in error for error in audit["errors"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_PATHOLOGICAL_JSON = {
    "deep-nest": "[" * 200000,
    "huge-int": '{"a": ' + "9" * 6000 + "}",
}


@pytest.mark.parametrize("name", ["run_manifest.json", "run_summary.json"])
@pytest.mark.parametrize("kind", sorted(_PATHOLOGICAL_JSON))
def test_pathological_json_in_manifest_or_summary_fails_without_raising(name, kind):
    """Parser limits (recursion depth, int digit cap) are damage, not crashes."""
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_pathological_"))
    try:
        _good_run(tmp)
        (tmp / name).write_text(_PATHOLOGICAL_JSON[kind], encoding="utf-8")

        report = semantic_audit(tmp)
        assert not report.valid
        assert report.levels["final_artifacts"] == "failed"
        assert "final_artifacts" in report.failed_levels()
        assert not verify_run_semantic(tmp).valid
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pathological_manifest_cli_reports_json_failure_not_a_traceback():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_pathological_cli_"))
    try:
        _good_run(tmp)
        (tmp / "run_manifest.json").write_text(
            _PATHOLOGICAL_JSON["deep-nest"], encoding="utf-8"
        )
        cli = subprocess.run(
            [sys.executable, "-m", "src.controller", "verify-run", str(tmp), "--json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert cli.returncode != 0
        assert "Traceback" not in cli.stderr
        audit = json.loads(cli.stdout)
        assert "final_artifacts" in audit["failed_levels"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("damage", ["invalid-utf8", "directory"])
def test_unreadable_provenance_log_fails_envelope_integrity_without_raising(damage):
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_sem_provenance_unreadable_"))
    try:
        _good_run(tmp)
        log = next(tmp.glob("*_provenance.log"))
        if damage == "invalid-utf8":
            log.write_bytes(b"\xff\xfe\x00 not utf-8\n")
        else:
            log.unlink()
            log.mkdir()

        result = verify_run(tmp)
        assert not result.valid
        assert any(log.name in error for error in result.errors)
        report = semantic_audit(tmp)
        assert report.levels["envelope_integrity"] == "failed"
        assert "envelope_integrity" in report.failed_levels()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
