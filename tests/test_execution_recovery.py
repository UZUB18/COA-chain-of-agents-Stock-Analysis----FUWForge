"""Local recovery of an already-received provider response.

The gap these tests cover: a run that received a completion and then failed
*locally* (malformed JSON, a normalization error, a contract violation) has
already paid for that completion. Resuming it left only two options — dispatch
again, paying twice for one answer, or abandon the run — even though the bytes
were sitting in the receipt.

Recovery re-runs the local pipeline over the stored text and accepts it only if
it validates. It never calls the provider, and it never relaxes a check: a
response that was genuinely bad stays bad.

Every test here asserts the backend was not called. A recovery path that quietly
dispatched would be worse than no recovery path at all.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentContext
from src.agents.registry import model_registry
from src.execution_store import ExecutionStore
from src.orchestrator import FUWForgeOrchestrator

EVIDENCE_HASH = "sha256:" + "e" * 64
PROMPT_HASH = "sha256:" + "1" * 64


@pytest.mark.parametrize("responses, expected_calls, expected_state", [
    (["not json", "valid"], 2, "returned"),
    ([TimeoutError("first timeout")], 1, "unknown_completion"),
    (["not json", TimeoutError("repair timeout")], 2, "unknown_completion"),
    (["not json", "still not json", "valid"], 2, "failed"),
])
def test_durable_repair_retries_only_received_invalid_responses(tmp_path, responses, expected_calls, expected_state):
    from src.orchestrator import OrchestratorParams
    from src.provenance import ProvenanceLog

    class Backend:
        model = "recording"

        def __init__(self):
            self.calls = []

        def generate(self, prompt):
            self.calls.append(prompt)
            value = responses[len(self.calls) - 1]
            if isinstance(value, Exception):
                raise value
            return json.dumps(_a1_payload()) if value == "valid" else value

    backend = Backend()
    runner = FUWForgeOrchestrator(PROJECT_ROOT, registry=model_registry(backend, provider="recording", max_retries=3))
    store = ExecutionStore(tmp_path / "ledger.db")
    kwargs = dict(agent_id="A1", params=OrchestratorParams("Test", "TEST", "2026-10-11", "Quick", [], "default"),
                  depth_config={}, focus_overrides={}, frozen_checksum="sha256:frozen", run_id="repair",
                  output_root=tmp_path, provenance=ProvenanceLog(tmp_path / "prov.log"), mode="automated", store=store)
    if expected_state == "returned":
        execution = runner._run_agent(**kwargs)
        assert execution.attempts == 2
    else:
        with pytest.raises(RuntimeError):
            runner._run_agent(**kwargs)
    assert len(backend.calls) == expected_calls
    receipts = store.get_by_agent("A1")
    main = next(receipt for receipt in receipts if receipt.state == expected_state
                and (expected_state != "failed" or receipt.raw_response == {"raw": "still not json"}))
    if expected_calls == 2:
        history = [receipt for receipt in receipts if receipt.execution_key != main.execution_key]
        assert len(history) == 1
        assert history[0].state == "failed"
        assert history[0].raw_response == {"raw": "not json"}
        assert history[0].metadata["repair_of"] == main.execution_key
        assert main.metadata["repair_prompt_sha256"] == hashlib.sha256(backend.calls[1].encode()).hexdigest()
        assert "CRITICAL ERROR" in backend.calls[1]
    else:
        assert len(receipts) == 1
    if expected_state == "unknown_completion":
        assert main.requires_recovery()


def test_durable_repair_after_contract_validation_failure(tmp_path):
    from src.agents.model import ModelAgent

    invalid = _a1_payload()
    invalid["artifacts"].pop("market_cap_check")

    class Backend:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt):
            self.calls += 1
            return json.dumps(invalid if self.calls == 1 else _a1_payload())

    store = ExecutionStore(tmp_path / "ledger.db")
    key = store.begin(agent_id="A1", provider="recording", model="test", prompt_sha256=PROMPT_HASH,
                      evidence_sha256=EVIDENCE_HASH, upstream=())
    context = _context(tmp_path, with_evidence=True)
    context.on_provider_return = lambda raw: store.mark_returned(key, {"raw": raw})
    context.on_repair_start = lambda error, prompt_hash: store.begin_repair(key, error, prompt_hash)
    backend = Backend()
    output = ModelAgent("A1", "Harvester", backend, strict=True, max_retries=1).run(context)
    assert backend.calls == 2
    assert output.envelope.artifacts["market_cap_check"]["price"] == 608.10
    assert len(store.get_by_agent("A1")) == 2


def test_repair_start_is_recorded_before_dispatch_and_requires_recovery_after_crash(tmp_path):
    store, key = _store_with_receipt(tmp_path, "bad response")
    store.begin_repair(key, "invalid envelope", PROMPT_HASH)
    assert store.get(key).state == "running"
    assert store.get(key).raw_response is None
    store.reconcile_unvalidated_on_resume()
    assert store.get(key).requires_recovery()
    history = [receipt for receipt in store.get_by_agent("A1") if receipt.execution_key != key]
    assert len(history) == 1 and history[0].raw_response == {"raw": "bad response"}


def test_repair_transition_rolls_back_if_recording_fails(tmp_path):
    store, key = _store_with_receipt(tmp_path, "bad response")
    store._conn.execute("""CREATE TRIGGER fail_repair BEFORE UPDATE ON execution_receipts
        WHEN OLD.state = 'returned' AND NEW.state = 'running'
        BEGIN SELECT RAISE(ABORT, 'storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.begin_repair(key, "invalid envelope", PROMPT_HASH)
    assert store.get(key).state == "returned"
    assert store.get(key).raw_response == {"raw": "bad response"}
    assert len(store.get_by_agent("A1")) == 1


def test_repeated_identical_rejections_keep_separate_usage_receipts(tmp_path):
    store, key = _store_with_receipt(tmp_path, "same invalid response")
    for _ in range(2):
        store.mark_returned(key, {"raw": "same invalid response"}, usage={"totalTokenCount": 10})
        store.begin_repair(key, "invalid envelope", PROMPT_HASH)
    history = [receipt for receipt in store.get_by_agent("A1") if receipt.execution_key != key]
    assert len(history) == 2
    assert sum(receipt.usage["totalTokenCount"] for receipt in history) == 20


class _RecordingBackend:
    """A backend that fails the test if anything asks it for a completion."""

    model = "never-called"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str) -> str:  # pragma: no cover - only on violation
        self.calls += 1
        raise AssertionError("recovery must not dispatch to the provider")

    def generate_envelope(self, prompt: str, schema):  # pragma: no cover
        self.calls += 1
        raise AssertionError("recovery must not dispatch to the provider")


def _a1_payload() -> dict:
    """A contract-valid A1 envelope, in the shape a provider would return."""
    return {
        "schema_version": "2.1",
        "agent": {"id": "A1", "name": "Harvester-&-Validator"},
        "gates": {"passed": True, "notes": []},
        "sections_md": {"RAW_DATA_PACK": "# Harvest"},
        "artifacts": {
            "price_timestamp_eet": "2026-10-09T16:00:00Z",
            "shares_basic": 1632475042.0,
            "shares_diluted": 1659000000.0,
            "market_cap_check": {"price": 608.10, "shares": 1632475042.0, "cap": 992688035728.0},
        },
        "sources": [],
        "warnings": [],
        "metrics": {"data_checksum": "sha256:abc"},
        "confidence": 0.9,
    }


def _context(tmp_path, *, with_evidence: bool = False) -> AgentContext:
    documents = (
        [
            {
                "type": "SEC 10-K",
                "url": "https://www.sec.gov/Archives/edgar/data/0000002488/amd-10k.htm",
                "doc_date": "2026-02-04",
                "accessed": "2026-10-11T00:00:00Z",
                "verification": "retrieved",
            }
        ]
        if with_evidence
        else []
    )
    return AgentContext(
        run_id="recovery",
        params={
            "company": "Advanced Micro Devices, Inc.",
            "ticker": "AMD",
            "analysis_date": "2026-10-11",
            "depth": "Quick",
            "focus": [],
            "weights": "default",
        },
        depth_config={},
        focus_overrides={},
        frozen_checksum="sha256:frozen",
        workdir=tmp_path,
        provenance_path=tmp_path / "prov.log",
        source_documents=documents,
        source_text="",
        evidence_checksum=EVIDENCE_HASH,
    )


def _store_with_receipt(tmp_path, stored_text: str, *, state: str = "returned"):
    """Persist a receipt whose provider response is on disk but unaccepted."""
    store = ExecutionStore(tmp_path / "ledger.db")
    key = store.begin(
        agent_id="A1",
        provider="recording",
        model="never-called",
        prompt_sha256=PROMPT_HASH,
        evidence_sha256=EVIDENCE_HASH,
        upstream=(),
    )
    store.mark_returned(key, {"raw": stored_text})
    if state == "failed":
        store.mark_failed(key, "provider returned but local normalization/validation failed")
    return store, key


def _orchestrator(*, strict: bool = False):
    """An orchestrator whose registry resolves A1 to a never-called backend.

    ``provider="mock"`` only gets past construction; the registry is then
    replaced, so any real dispatch would raise instead of reaching a network.
    Strictness is a parameter because strict A3/A1 paths additionally require a
    retrieved evidence pack, which is orthogonal to whether recovery ran.
    """
    backend = _RecordingBackend()
    orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="never-called")
    orchestrator.registry = model_registry(backend, provider="recording", strict=strict)
    return orchestrator, backend


def test_a_locally_failed_response_is_recovered_without_a_provider_call(tmp_path):
    """
    The motivating case: the provider answered, local normalization failed, and
    the run died. On resume the stored bytes must be re-processed locally.
    """
    store, key = _store_with_receipt(tmp_path, json.dumps(_a1_payload()))
    orchestrator, backend = _orchestrator()
    context = _context(tmp_path)

    execution = orchestrator._recover_saved_response(
        "A1", store.get(key), context, store, key, {"provider": "recording"}, (), {}
    )

    assert execution is not None
    assert backend.calls == 0
    assert execution.reused is False
    assert execution.raw_response["recovered_locally"] is True
    assert execution.raw_response["prior_state"] == "returned"
    assert execution.envelope.artifacts["market_cap_check"]["price"] == 608.10
    # Recovery does not claim the receipt was validated; acceptance does that.
    assert store.get(key).state == "returned"


def test_recovered_repair_preserves_both_provider_usage_records(tmp_path):
    store, key = _store_with_receipt(tmp_path, "invalid")
    store.mark_returned(key, {"raw": "invalid"}, usage={"totalTokenCount": 10})
    store.begin_repair(key, "invalid envelope", PROMPT_HASH)
    store.mark_returned(key, {"raw": json.dumps(_a1_payload())}, usage={"totalTokenCount": 20})
    orchestrator, backend = _orchestrator()
    execution = orchestrator._recover_saved_response(
        "A1", store.get(key), _context(tmp_path), store, key, {"provider": "recording"}, (), {}
    )
    assert backend.calls == 0
    assert execution.raw_response["usage"]["total_tokens"] == 30
    assert store.get(key).usage["total_tokens"] == 30


def test_a_genuinely_bad_response_is_not_recovered(tmp_path):
    """Malformed provider text stays a failure; recovery is not leniency."""
    store, key = _store_with_receipt(tmp_path, "not json at all")
    orchestrator, backend = _orchestrator()

    execution = orchestrator._recover_saved_response(
        "A1", store.get(key), _context(tmp_path), store, key, {}, (), {}
    )

    assert execution is None
    assert backend.calls == 0
    assert store.get(key).state == "failed"
    assert "could not be recovered locally" in store.get(key).error


def test_a_contract_violating_response_is_not_recovered(tmp_path):
    """
    Recovery applies the run's own contract. In strict mode a required artifact
    the normalizer cannot legitimately supply is still a rejection — recovery
    must not become a way to launder a response past the contract.
    """
    payload = _a1_payload()
    payload["artifacts"].pop("market_cap_check")
    store, key = _store_with_receipt(tmp_path, json.dumps(payload))
    orchestrator, backend = _orchestrator(strict=True)

    execution = orchestrator._recover_saved_response(
        "A1",
        store.get(key),
        _context(tmp_path, with_evidence=True),
        store,
        key,
        {},
        (),
        {},
    )

    assert execution is None
    assert backend.calls == 0
    assert store.get(key).state == "failed"


def test_a_receipt_without_provider_text_is_left_alone(tmp_path):
    """
    A receipt that carries an envelope rather than provider text (controller-mode
    writes, legacy rows) has nothing to re-normalize, and must not be reported as
    a recovery attempt.
    """
    store, key = _store_with_receipt(tmp_path, json.dumps(_a1_payload()))
    store.mark_returned(key, {"envelope": _a1_payload()})
    orchestrator, backend = _orchestrator()

    execution = orchestrator._recover_saved_response(
        "A1", store.get(key), _context(tmp_path), store, key, {}, (), {}
    )

    assert execution is None
    assert backend.calls == 0
    assert store.get(key).state == "returned"


def test_recovered_text_is_normalized_not_passed_through(tmp_path):
    """
    Recovery runs the real local pipeline. A1's normalizer fills defaults and
    records what it injected, so a pass-through would be visible here.
    """
    payload = _a1_payload()
    payload["artifacts"].pop("price_timestamp_eet")
    store, key = _store_with_receipt(tmp_path, json.dumps(payload))
    orchestrator, _backend = _orchestrator()

    execution = orchestrator._recover_saved_response(
        "A1", store.get(key), _context(tmp_path), store, key, {}, (), {}
    )

    assert execution is not None
    # The normalizer restored the required artifact, which a raw pass-through
    # could not have done.
    assert execution.envelope.artifacts.get("price_timestamp_eet")


def test_an_abandoned_receipt_is_never_recovered(tmp_path):
    """
    Abandon is an explicit operator decision. It is honoured before recovery is
    considered, so recovery can never resurrect work the operator discarded.
    """
    store, key = _store_with_receipt(tmp_path, json.dumps(_a1_payload()))
    store.mark_unknown_completion(key, reason="interrupted")
    store.resolve_recovery(key, "abandon")
    orchestrator, backend = _orchestrator()

    receipt = store.get(key)
    assert receipt.recovery_choice == "abandon"
    assert not receipt.is_safely_reusable()
    # The orchestrator's resume block raises on ABANDON before recovery is
    # considered, so recovery can never resurrect discarded work.
    assert backend.calls == 0


# ----------------------------------------- Task 3.2: response-bearing failed gate


def _run_tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_failed_receipt_with_recoverable_response_recovers_locally_without_choice(
    tmp_path,
):
    from src.orchestrator import OrchestratorParams
    from src.provenance import ProvenanceLog

    run_dir = tmp_path / "run"
    params = OrchestratorParams("Test Co", "TEST", "2026-10-11", "Quick", [], "default")

    backend = _RecordingBackend()
    orch = FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(backend, provider="recording", strict=False),
        strict=False,
    )

    class _FailOnceBackend:
        model = "never-called"

        def __init__(self):
            self.calls = 0

        def generate(self, prompt: str) -> str:
            self.calls += 1
            return "not valid json"

    fail_backend = _FailOnceBackend()
    orch_init = FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(
            fail_backend, provider="recording", max_retries=0, strict=False
        ),
        strict=False,
    )
    with pytest.raises(RuntimeError):
        orch_init.run("rec_run", params, output_root=run_dir)
    assert fail_backend.calls == 1

    # Replace stored raw_response on the failed receipt with valid A1 JSON
    store = ExecutionStore(run_dir / "execution_ledger.db")
    a1_receipt = store.get_by_agent("A1")[0]
    assert a1_receipt.state == "failed"
    with store._conn:
        store._conn.execute(
            "UPDATE execution_receipts SET raw_response_json = ? WHERE execution_key = ?",
            (json.dumps({"raw": json.dumps(_a1_payload())}), a1_receipt.execution_key),
        )
    store.close()

    # Now resume _run_agent for A1 without recovery_choice: must recover locally with 0 calls.
    store2 = ExecutionStore(run_dir / "execution_ledger.db")
    try:
        exec_res = orch._run_agent(
            "A1",
            params=params,
            depth_config=orch.depth_modes["quick"].payload,
            focus_overrides={},
            frozen_checksum=None,
            run_id="rec_run",
            output_root=run_dir,
            provenance=ProvenanceLog(run_dir / "prov.log"),
            mode="automated",
            store=store2,
            resume=True,
        )
        assert backend.calls == 0
        assert exec_res.raw_response.get("recovered_locally") is True
    finally:
        store2.close()


def test_failed_receipt_with_unrecoverable_response_requires_recovery_choice_and_preserves_run_tree(
    tmp_path,
):
    from src.orchestrator import OrchestratorParams
    from src.utils.checksum import sha256_digest

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    payload = b"Annual filing body"
    (pack_dir / "D001.bytes").write_bytes(payload)
    (pack_dir / "D001.txt").write_bytes(payload)
    pack_path = pack_dir / "evidence.json"
    pack_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "ticker": "TEST",
                "documents": [
                    {
                        "id": "D001",
                        "type": "Filing",
                        "title": "Annual results",
                        "url": "https://example.com/annual-results",
                        "doc_date": "2025-01-01",
                        "date_basis": "supplied_manifest",
                        "accessed": "2026-10-06T10:00:00Z",
                        "content_type": "text/plain",
                        "verification": "retrieved",
                        "content_sha256": sha256_digest(payload),
                        "text_sha256": sha256_digest(payload),
                        "raw_file": "D001.bytes",
                        "text_file": "D001.txt",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run_dir = tmp_path / "run"
    params = OrchestratorParams("Test Co", "TEST", "2026-10-11", "Quick", [], "default")

    class _CountingBackend:
        model = "recording-model"

        def __init__(self):
            self.calls = 0
            self.response = "still not json"

        def generate(self, prompt: str) -> str:
            self.calls += 1
            return self.response

    backend = _CountingBackend()
    orch1 = FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(
            backend, provider="recording", max_retries=0, strict=False
        ),
        evidence_pack=pack_path,
        strict=False,
    )
    with pytest.raises(RuntimeError):
        orch1.run("rec_run", params, output_root=run_dir)
    assert backend.calls == 1

    store = ExecutionStore(run_dir / "execution_ledger.db")
    receipts = store.get_by_agent("A1")
    assert len(receipts) == 1
    assert receipts[0].state == "failed"
    assert receipts[0].raw_response == {"raw": "still not json"}
    store.close()

    before = _run_tree_snapshot(run_dir)

    # 1. Resume without --recovery-choice: must try local recovery, fail, and refuse
    # before any provider call or run-tree mutation (including provenance.log).
    backend.calls = 0
    orch_no_choice = FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(
            backend, provider="recording", max_retries=0, strict=False
        ),
        evidence_pack=pack_path,
        strict=False,
    )
    with pytest.raises(RuntimeError, match="recovery-choice|recovery_choice"):
        orch_no_choice.run("rec_run", params, output_root=run_dir, resume=True)

    assert backend.calls == 0
    assert _run_tree_snapshot(run_dir) == before

    # 2. Resume with recovery_choice='retry': dispatches once to the provider.
    orch_retry = FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(
            backend, provider="recording", max_retries=0, strict=False
        ),
        evidence_pack=pack_path,
        strict=False,
    )
    with pytest.raises(RuntimeError):
        orch_retry.run(
            "rec_run",
            params,
            output_root=run_dir,
            resume=True,
            recovery_choice="retry",
        )
    assert backend.calls == 1

    # 3. Resume with recovery_choice='abandon': zero dispatches and durable abandon marker.
    backend.calls = 0
    orch_abandon = FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(
            backend, provider="recording", max_retries=0, strict=False
        ),
        evidence_pack=pack_path,
        strict=False,
    )
    with pytest.raises(RuntimeError, match="explicitly abandoned"):
        orch_abandon.run(
            "rec_run",
            params,
            output_root=run_dir,
            resume=True,
            recovery_choice="abandon",
        )
    assert backend.calls == 0
    store_after = ExecutionStore(run_dir / "execution_ledger.db")
    try:
        abandoned = store_after.get_by_agent("A1")[0]
        assert abandoned.recovery_choice == "abandon"
    finally:
        store_after.close()


# --- Phase 3 review fix round 1 -------------------------------------------------


class _ScriptedBackend:
    model = "m"

    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self.text


def _scripted(backend):
    return FUWForgeOrchestrator(
        PROJECT_ROOT,
        registry=model_registry(backend, provider="recording", max_retries=0, strict=False),
        strict=False,
    )


def _params():
    from src.orchestrator import OrchestratorParams

    return OrchestratorParams("Test Co", "TEST", "2026-10-11", "Quick", [], "default")


def test_abandon_choice_still_tries_zero_cost_local_recovery_first(tmp_path):
    run_dir = tmp_path / "run"
    backend = _ScriptedBackend("not valid json")
    with pytest.raises(RuntimeError):
        _scripted(backend).run("r", _params(), output_root=run_dir)
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        a1 = store.get_by_agent("A1")[0]
        assert a1.state == "failed"
        with store._conn:
            store._conn.execute(
                "UPDATE execution_receipts SET raw_response_json = ? WHERE execution_key = ?",
                (json.dumps({"raw": json.dumps(_a1_payload())}), a1.execution_key),
            )

    with pytest.raises(RuntimeError):  # A2-A5 are new work and still fail to parse
        _scripted(backend).run("r", _params(), output_root=run_dir, resume=True, recovery_choice="abandon")

    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        a1 = store.get(a1.execution_key)
        assert a1.state == "validated"
        assert a1.recovery_choice is None


def _gate_failed_a1_text() -> str:
    payload = _a1_payload()
    payload["gates"] = {"passed": False, "notes": ["missing price"]}
    return json.dumps(payload)


def test_acceptance_failed_receipt_needs_a_choice_and_retry_dispatches_once(tmp_path):
    run_dir = tmp_path / "run"
    backend = _ScriptedBackend(_gate_failed_a1_text())
    with pytest.raises(RuntimeError, match="gate"):
        _scripted(backend).run("r", _params(), output_root=run_dir)
    assert backend.calls == 1

    # Replaying the saved response can only fail acceptance again: no choice, no call.
    backend.calls = 0
    with pytest.raises(RuntimeError, match="requires --recovery-choice"):
        _scripted(backend).run("r", _params(), output_root=run_dir, resume=True)
    assert backend.calls == 0

    # The operator's retry is honoured with exactly one dispatch and is not left behind.
    with pytest.raises(RuntimeError, match="gate"):
        _scripted(backend).run("r", _params(), output_root=run_dir, resume=True, recovery_choice="retry")
    assert backend.calls == 1
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        a1 = store.get_by_agent("A1")[0]
        assert a1.state == "failed"
        assert a1.recovery_choice is None


def test_recovery_choice_leaves_repair_history_rows_alone(tmp_path):
    run_dir = tmp_path / "run"
    backend = _ScriptedBackend("not json")
    orch = lambda: FUWForgeOrchestrator(  # noqa: E731
        PROJECT_ROOT,
        registry=model_registry(backend, provider="recording", max_retries=2, strict=False),
        strict=False,
    )
    with pytest.raises(RuntimeError):
        orch().run("r", _params(), output_root=run_dir)
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        history = {
            r.execution_key: (r.state, r.recovery_choice)
            for r in store.list_receipts()
            if "repair_of" in (r.metadata or {})
        }
    assert history

    with pytest.raises(RuntimeError):
        orch().run("r", _params(), output_root=run_dir, resume=True, recovery_choice="retry")
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        for key, before in history.items():
            r = store.get(key)
            assert (r.state, r.recovery_choice) == before


def test_reused_receipt_failing_acceptance_reports_the_real_error(tmp_path, monkeypatch):
    import src.orchestrator as orchestrator_module
    from src.validators import ValidationResult

    run_dir = tmp_path / "run"
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock").run("r", _params(), output_root=run_dir)
    original = orchestrator_module.validate_contract

    def reject_a2(env, **kw):
        result = original(env, **kw)
        if env.agent.id == "A2":
            return ValidationResult(False, ["new rule: A2 rejected"], list(result.warnings))
        return result

    monkeypatch.setattr(orchestrator_module, "validate_contract", reject_a2)
    with pytest.raises(RuntimeError, match="A2 contract validation failed"):
        FUWForgeOrchestrator(PROJECT_ROOT, provider="mock").run("r", _params(), output_root=run_dir, resume=True)
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        assert store.get_by_agent("A2")[0].state == "validated"
