"""
Tests for durable execution receipts, SQLite execution store, and selective resume (Task 7).

Verifies:
- Crash-injection: a row persisted after provider return is 'returned' (not validated)
  and is NOT safely reusable on resume.
- A 'validated' node with a matching execution key IS reusable and avoids re-executing.
- Changed evidence / changed prompt / changed model changes execution key -> invalidation.
- Mismatched prompt or model -> not reusable.
- 'unknown_completion' is never auto-reused; requires explicit operator choice (retry vs abandon).
  Documents that retry can duplicate a paid external call.
- Selective invalidation touches a node + true descendants only (using true_descendants).
- raw_checksum vs accepted_checksum stored distinctly.
- Schema validation against schemas/execution_receipt_v1.json.
- Real mock orchestrator execution and resume ledger verification.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.execution_identity import (
    UpstreamRef,
    capture_upstream,
    execution_key,
    true_descendants,
)
from src.agents.registry import model_registry
from src.agents.base import BaseAgent
from src.execution_store import (
    ExecutionReceipt,
    ExecutionState,
    ExecutionStore,
    RecoveryChoice,
    compute_raw_checksum,
    unresolved_recovery_agents,
    validate_receipt,
)
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams
from src.utils.checksum import envelope_content_checksum


CHECKSUM_A1 = "sha256:" + "a" * 64
CHECKSUM_A2 = "sha256:" + "b" * 64
PROMPT_HASH_1 = "sha256:" + "1" * 64
PROMPT_HASH_2 = "sha256:" + "2" * 64
EVIDENCE_HASH_1 = "sha256:" + "e" * 64
EVIDENCE_HASH_2 = "sha256:" + "f" * 64


def _prompt_hash_for(agent_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"prompt_{agent_id}".encode("utf-8")).hexdigest()


def _sample_raw_response() -> Dict[str, Any]:
    return {
        "provider": "mock",
        "model": "mock-model-v1",
        "text": "Simulated raw model response text.",
        "tokens": {"prompt": 120, "completion": 80, "total": 200},
    }


def _sample_accepted_envelope(agent_id: str = "A1", checksum: str = CHECKSUM_A1) -> Dict[str, Any]:
    envelope = {
        "schema_version": "2.1",
        "agent": {"id": agent_id, "name": f"Agent {agent_id}"},
        "gates": {"passed": True, "notes": ["All criteria met"]},
        "sections_md": {"ANALYSIS": "Validated financial analysis"},
        "artifacts": {"data": 123},
        "sources": [],
        "metrics": {"data_checksum": checksum},
    }
    envelope["content_checksum"] = envelope_content_checksum(envelope)
    return envelope


# -----------------------------------------------------------------------------
# 1. Crash injection: returned != validated
# -----------------------------------------------------------------------------


def test_crash_injection_returned_is_not_safely_reusable():
    """
    A row persisted immediately after provider return is in 'returned' state.
    It is NOT validated and must NOT be reported as safely reusable.
    """
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )

    # Provider call returns and is atomically written to the ledger
    raw = _sample_raw_response()
    receipt = store.mark_returned(
        key,
        raw_response=raw,
        usage={"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        latency_ms=250,
    )

    assert receipt.state == ExecutionState.RETURNED.value
    assert receipt.raw_response == raw
    assert receipt.raw_checksum is not None
    assert receipt.accepted_envelope is None
    assert receipt.accepted_checksum is None

    # Crucial assertion: returned != validated, so is_reusable is False!
    assert not receipt.is_validated()
    assert not receipt.is_safely_reusable()
    assert not store.is_reusable(key)
    assert store.get_reusable_envelope(key) is None
    assert store.reusable_nodes({"A2": key}) == {}


def test_reconcile_unvalidated_on_resume_flags_unknown_completion():
    """
    When resuming after a process crash during provider return or validation,
    unvalidated executions must be transitioned to 'unknown_completion'.
    """
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key1 = store.begin(
        agent_id="A2",
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    store.mark_returned(key1, raw_response=_sample_raw_response())

    # Simulated crash happened here before mark_validated.
    # On resume, reconcile unvalidated nodes:
    reconciled = store.reconcile_unvalidated_on_resume()
    assert len(reconciled) == 1
    assert reconciled[0].execution_key == key1
    assert reconciled[0].state == ExecutionState.UNKNOWN_COMPLETION.value
    assert not reconciled[0].is_safely_reusable()


# -----------------------------------------------------------------------------
# 2. Validated node reuse
# -----------------------------------------------------------------------------


def test_validated_node_is_reusable_without_provider_call():
    """
    A node that has completed both provider return and orchestrator validation
    is in 'validated' state and IS safely reusable.
    """
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )

    raw = _sample_raw_response()
    store.mark_returned(key, raw_response=raw, usage={"total_tokens": 200})

    envelope = _sample_accepted_envelope("A2", CHECKSUM_A2)
    receipt = store.mark_validated(
        key,
        accepted_envelope=envelope,
        accepted_checksum=envelope["content_checksum"],
    )

    assert receipt.state == ExecutionState.VALIDATED.value
    assert receipt.is_validated()
    assert receipt.is_safely_reusable()
    assert store.is_reusable(key)
    assert store.get_reusable_envelope(key) == envelope

    reusable = store.reusable_nodes({"A2": key, "A3": "non-existent-key"})
    assert "A2" in reusable
    assert "A3" not in reusable
    assert reusable["A2"].accepted_envelope == envelope


# -----------------------------------------------------------------------------
# 3. Execution identity changes invalidate nodes
# -----------------------------------------------------------------------------


def test_changed_evidence_changes_execution_key():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)

    key_v1 = store.begin(
        agent_id="A2",
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    store.mark_returned(key_v1, raw_response=_sample_raw_response())
    store.mark_validated(key_v1, accepted_envelope=_sample_accepted_envelope("A2", CHECKSUM_A2))

    # If evidence changes, the computed execution key differs
    key_v2 = execution_key(
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_2,  # changed evidence!
        upstream=upstream,
    )
    assert key_v1 != key_v2
    assert not store.is_reusable(key_v2)


def test_changed_prompt_changes_execution_key():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)

    key_v1 = store.begin(
        agent_id="A2",
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    store.mark_returned(key_v1, raw_response=_sample_raw_response())
    store.mark_validated(key_v1, accepted_envelope=_sample_accepted_envelope("A2", CHECKSUM_A2))

    key_v2 = execution_key(
        provider="mock",
        model="test-model",
        prompt_sha256=PROMPT_HASH_2,  # changed prompt!
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    assert key_v1 != key_v2
    assert not store.is_reusable(key_v2)


def test_changed_model_or_provider_changes_execution_key():
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    k1 = execution_key(
        provider="gemini",
        model="gemini-2.5-pro",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    k2 = execution_key(
        provider="gemini",
        model="gemini-2.5-flash",  # different model
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    assert k1 != k2


def test_changed_parent_checksum_changes_execution_key():
    upstream_v1 = (UpstreamRef("A1", CHECKSUM_A1),)
    upstream_v2 = (UpstreamRef("A1", CHECKSUM_A2),)  # A1 output checksum changed

    k1 = execution_key(
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream_v1,
    )
    k2 = execution_key(
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream_v2,
    )
    assert k1 != k2


# -----------------------------------------------------------------------------
# 4. Unknown completion & Explicit Operator Recovery Choice
# -----------------------------------------------------------------------------


def test_unknown_completion_requires_explicit_recovery_choice():
    """
    An execution in unknown_completion state is never automatically reused.
    An operator must explicitly choose 'retry' or 'abandon'.
    Retrying is documented as potentially duplicating a paid call.
    """
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A3",
        provider="paid-provider",
        model="premium-model",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )

    store.mark_unknown_completion(key, reason="Timeout or uncertain network disconnect")
    receipt = store.get(key)
    assert receipt is not None
    assert receipt.state == ExecutionState.UNKNOWN_COMPLETION.value
    assert not receipt.is_safely_reusable()
    assert receipt.requires_recovery()

    # Invalid choice rejected
    with pytest.raises(ValueError, match="Invalid recovery choice"):
        store.resolve_recovery(key, "auto_ignore")

    # Operator explicitly chooses retry (aware of double-billing risk)
    receipt_retry = store.resolve_recovery(key, RecoveryChoice.RETRY)
    assert receipt_retry.recovery_choice == "retry"
    # Even after marking retry, it is still not validated until re-executed and verified
    assert not receipt_retry.is_safely_reusable()

    # Operator chooses abandon on another node
    key2 = store.begin(
        agent_id="A4",
        provider="paid-provider",
        model="premium-model",
        prompt_sha256=PROMPT_HASH_2,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    store.mark_unknown_completion(key2, reason="Halt on cost budget")
    receipt_abandon = store.resolve_recovery(key2, RecoveryChoice.ABANDON)
    assert receipt_abandon.recovery_choice == "abandon"
    assert not receipt_abandon.is_safely_reusable()


def test_recorded_recovery_decision_clears_the_obligation():
    """
    A decided node must stop demanding an operator decision on every resume.

    Leaving the state at ``unknown_completion`` after recording a choice makes
    a resume loop re-prompt the operator forever. Recording the decision must
    move the node on: `retry` -> running, `abandon` -> failed. Neither is
    reusable, because neither has actually returned and validated.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_recovery_"))
    try:
        store = ExecutionStore(tmp / "r.db")
        upstream = (UpstreamRef("A1", CHECKSUM_A1),)

        key = store.begin(
            agent_id="A3",
            provider="paid-provider",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        )
        store.mark_unknown_completion(key, reason="process killed")
        assert store.get(key).requires_recovery()

        retried = store.resolve_recovery(key, RecoveryChoice.RETRY)
        assert retried.state == ExecutionState.RUNNING.value
        assert not retried.requires_recovery()
        assert not retried.is_safely_reusable()

        key2 = store.begin(
            agent_id="A5",
            provider="paid-provider",
            model="m",
            prompt_sha256=PROMPT_HASH_2,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        )
        store.mark_unknown_completion(key2, reason="process killed")
        abandoned = store.resolve_recovery(key2, RecoveryChoice.ABANDON)
        assert abandoned.state == ExecutionState.FAILED.value
        assert not abandoned.requires_recovery()
        assert not abandoned.is_safely_reusable()

        # Only a node that actually returned AND validated is ever reusable.
        store.mark_returned(key, {"ok": True})
        assert not store.is_reusable(key)
        accepted_envelope = _sample_accepted_envelope("A3")
        store.mark_validated(
            key,
            accepted_envelope,
            accepted_checksum=accepted_envelope["content_checksum"],
        )
        assert store.is_reusable(key)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------------------------------------------------------
# 5. Selective invalidation touches true descendants only
# -----------------------------------------------------------------------------


def test_selective_invalidation_touches_only_true_descendants():
    """
    In the FUWForge DAG:
      A1 -> {A2, A3, A4, A5}
      {A1, A2, A3, A4, A5} -> A6B
      {A1..A5, A6B} -> A6R
      {A1..A5, A6B, A6R} -> A6
      {A1..A6} -> A7

    If A3 changes, true_descendants(graph, ['A3']) must be:
      {'A3', 'A6B', 'A6R', 'A6', 'A7'}
    A2, A4, A5 must NOT be invalidated!
    """
    graph = {
        "A1": {"upstream": []},
        "A2": {"upstream": [{"agent": "A1"}]},
        "A3": {"upstream": [{"agent": "A1"}]},
        "A4": {"upstream": [{"agent": "A1"}]},
        "A5": {"upstream": [{"agent": "A1"}]},
        "A6B": {"upstream": [{"agent": "A1"}, {"agent": "A2"}, {"agent": "A3"}, {"agent": "A4"}, {"agent": "A5"}]},
        "A6R": {"upstream": [{"agent": "A1"}, {"agent": "A2"}, {"agent": "A3"}, {"agent": "A4"}, {"agent": "A5"}, {"agent": "A6B"}]},
        "A6": {"upstream": [{"agent": "A1"}, {"agent": "A2"}, {"agent": "A3"}, {"agent": "A4"}, {"agent": "A5"}, {"agent": "A6B"}, {"agent": "A6R"}]},
        "A7": {"upstream": [{"agent": "A1"}, {"agent": "A2"}, {"agent": "A3"}, {"agent": "A4"}, {"agent": "A5"}, {"agent": "A6B"}, {"agent": "A6R"}, {"agent": "A6"}]},
    }

    store = ExecutionStore(":memory:")
    keys = {}
    for aid in ["A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"]:
        upstream_agents = [entry["agent"] for entry in graph[aid]["upstream"]]
        refs = tuple(UpstreamRef(a, CHECKSUM_A1) for a in upstream_agents)
        p_hash = _prompt_hash_for(aid)
        k = store.begin(
            agent_id=aid,
            provider="mock",
            model="m",
            prompt_sha256=p_hash,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=refs,
        )
        store.mark_returned(k, raw_response=_sample_raw_response())
        store.mark_validated(k, accepted_envelope=_sample_accepted_envelope(aid))
        keys[aid] = k

    # Verify all 9 are validated initially
    assert len(store.reusable_nodes(keys)) == 9

    # A change to A3 reaches only A3 and its true descendants; validated
    # receipts are never demoted (a changed request gets a new key instead).
    assert true_descendants(graph, ["A3"]) == {"A3", "A6B", "A6R", "A6", "A7"}
    assert len(store.reusable_nodes(keys)) == 9


# -----------------------------------------------------------------------------
# 6. Distinct raw_checksum and accepted_checksum
# -----------------------------------------------------------------------------


def test_raw_checksum_and_accepted_checksum_stored_distinctly():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )

    raw_data = {"raw_text": "Model output with formatting tags"}
    custom_raw_ck = "sha256:" + "8" * 64
    store.mark_returned(key, raw_response=raw_data, raw_checksum=custom_raw_ck)

    accepted_env = _sample_accepted_envelope("A2", CHECKSUM_A2)
    custom_acc_ck = accepted_env["content_checksum"]
    receipt = store.mark_validated(key, accepted_envelope=accepted_env, accepted_checksum=custom_acc_ck)

    assert receipt.raw_checksum == custom_raw_ck
    assert receipt.accepted_checksum == custom_acc_ck
    assert receipt.raw_checksum != receipt.accepted_checksum


# -----------------------------------------------------------------------------
# 7. Schema validation against schemas/execution_receipt_v1.json
# -----------------------------------------------------------------------------


def test_receipt_validates_against_json_schema():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
        metadata={"run_id": "test_run_123"},
    )
    store.mark_returned(
        key,
        raw_response=_sample_raw_response(),
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        latency_ms=120,
    )
    accepted_envelope = _sample_accepted_envelope("A2", CHECKSUM_A2)
    receipt = store.mark_validated(
        key,
        accepted_envelope=accepted_envelope,
        accepted_checksum=accepted_envelope["content_checksum"],
    )

    data = receipt.to_dict()
    res = validate_receipt(data)
    assert res.valid, f"Validation failed: {res.errors}"


def test_receipt_schema_rejects_invalid_state():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    receipt = store.get(key)
    assert receipt is not None
    d = receipt.to_dict()
    d["state"] = "invalid_state_unknown"

    res = validate_receipt(d)
    assert not res.valid


# -----------------------------------------------------------------------------
# 8. Multi-agent execution & resume simulation with mock orchestrator
# -----------------------------------------------------------------------------


def test_orchestrator_mock_run_persists_and_identifies_reusable_nodes():
    """
    Simulate a run using the orchestrator with provider='mock', populate
    the ExecutionStore from the completed run, and verify that all nodes
    are recognized as validated and reusable.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_exec_store_"))
    try:
        orchestrator = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
        params = OrchestratorParams(
            company="Resume Co",
            ticker="RESUME",
            analysis_date="2026-02-01",
            depth="Standard",
            focus=[],
            weights="default",
        )
        orchestrator.run("resume_run", params, output_root=tmp)

        db_path = tmp / "execution_ledger.db"
        with ExecutionStore(db_path) as store:
            candidate_keys = {}
            for agent_id in ["A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"]:
                env_file = tmp / f"{agent_id.lower()}_envelope.json"
                assert env_file.exists(), f"Expected envelope file {env_file}"
                env_dict = json.loads(env_file.read_text(encoding="utf-8"))

                upstream_refs = tuple(
                    UpstreamRef(entry["agent"], entry["content_checksum"])
                    for entry in env_dict.get("upstream", [])
                )
                p_hash = _prompt_hash_for(agent_id)
                exec_key = execution_key(
                    provider="mock",
                    model="mock-v1",
                    prompt_sha256=p_hash,
                    evidence_sha256="0" * 64,
                    upstream=upstream_refs,
                )

                store.begin(
                    agent_id=agent_id,
                    provider="mock",
                    model="mock-v1",
                    prompt_sha256=p_hash,
                    evidence_sha256="0" * 64,
                    upstream=upstream_refs,
                    execution_key=exec_key,
                )
                raw_response = {"agent": agent_id, "mock": True}
                store.mark_returned(exec_key, raw_response=raw_response)
                store.mark_validated(
                    exec_key,
                    accepted_envelope=env_dict,
                    accepted_checksum=env_dict.get("content_checksum"),
                )
                candidate_keys[agent_id] = exec_key

            reusable = store.reusable_nodes(candidate_keys)
            assert len(reusable) == 9
            assert set(reusable.keys()) == {"A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"}

            # Re-read from a new store instance pointing to same SQLite DB
            with ExecutionStore(db_path) as store2:
                for agent_id, key in candidate_keys.items():
                    assert store2.is_reusable(key)
                    cached_env = store2.get_reusable_envelope(key)
                    assert cached_env is not None
                    assert cached_env["agent"]["id"] == agent_id

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_evidence_pack(root: Path, text: str, ticker: str = "RESUME") -> Path:
    """Minimal, fully-valid evidence pack for offline resume tests."""
    from src.utils.checksum import sha256_digest

    root.mkdir(parents=True, exist_ok=True)
    payload = text.encode()
    (root / "D001.bytes").write_bytes(payload)
    (root / "D001.txt").write_bytes(payload)
    document = {
        "id": "D001", "type": "Filing", "title": "Annual results",
        "url": "https://example.com/annual-results", "doc_date": "2025-01-01",
        "date_basis": "supplied_manifest", "accessed": "2026-10-06T10:00:00Z",
        "content_type": "text/plain", "verification": "retrieved",
        "content_sha256": sha256_digest(payload), "text_sha256": sha256_digest(payload),
        "raw_file": "D001.bytes", "text_file": "D001.txt",
    }
    path = root / "evidence.json"
    path.write_text(
        json.dumps({"schema_version": "1", "ticker": ticker, "documents": [document]}),
        encoding="utf-8",
    )
    return path


def _resume_params() -> OrchestratorParams:
    return OrchestratorParams(
        company="Resume Co",
        ticker="RESUME",
        analysis_date="2025-01-31",
        depth="Standard",
        focus=[],
        weights={},
    )


def _run_tree_bytes(root: Path) -> Dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


class _NoDispatchBackend:
    model = "no-dispatch"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str) -> str:  # pragma: no cover - only on violation
        self.calls += 1
        raise AssertionError("a refused resume must not dispatch to the provider")


def test_resume_reuses_existing_evidence_snapshot_instead_of_overwriting():
    """
    A resumed run already owns its evidence directory. Re-snapshotting used to
    call mkdir(exist_ok=False) and abort before any receipt could be reused.
    Resume must reload and re-verify the existing snapshot instead.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_resume_evidence_"))
    try:
        pack = _make_evidence_pack(tmp / "pack", "Revenue: 123. Public filing data.")
        run_dir = tmp / "run"

        first = FUWForgeOrchestrator(
            PROJECT_ROOT, provider="mock", model="mock-v1", evidence_pack=pack
        )
        first.run("resume_run", _resume_params(), output_root=run_dir)

        snapshot = run_dir / "evidence" / "evidence.json"
        assert snapshot.is_file()
        sealed_bytes = snapshot.read_bytes()

        # Resume against the same evidence: must not crash and must not rewrite
        # the snapshot the earlier envelopes were generated from.
        second = FUWForgeOrchestrator(
            PROJECT_ROOT, provider="mock", model="mock-v1", evidence_pack=pack
        )
        second.run(
            "resume_run", _resume_params(), output_root=run_dir, resume=True
        )
        assert snapshot.read_bytes() == sealed_bytes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resume_against_changed_evidence_is_refused():
    """
    Resuming against different evidence would invalidate every receipt in the
    store, so it must fail closed rather than reuse results produced from other
    bytes.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_resume_chgev_"))
    try:
        original = _make_evidence_pack(tmp / "pack_a", "Revenue: 123. Original filing.")
        run_dir = tmp / "run"

        first = FUWForgeOrchestrator(
            PROJECT_ROOT, provider="mock", model="mock-v1", evidence_pack=original
        )
        first.run("resume_run", _resume_params(), output_root=run_dir)
        before = _run_tree_bytes(run_dir)

        changed = _make_evidence_pack(tmp / "pack_b", "Revenue: 999. Different filing.")
        backend = _NoDispatchBackend()
        second = FUWForgeOrchestrator(
            PROJECT_ROOT,
            registry=model_registry(backend, provider="recording"),
            evidence_pack=changed,
        )
        with pytest.raises(ValueError) as raised:
            second.run("resume_run", _resume_params(), output_root=run_dir, resume=True)
        assert str(raised.value) == (
            f"Run directory '{run_dir}' already holds a different evidence snapshot "
            "than the supplied --evidence-pack. Resuming against changed evidence "
            "would invalidate prior receipts; use a fresh directory instead."
        )
        assert backend.calls == 0
        assert _run_tree_bytes(run_dir) == before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resume_with_uncertain_receipt_in_wal_is_refused_without_mutation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_resume_wal_"))
    store = None
    try:
        run_dir = tmp / "run"
        FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
            "resume_run", _resume_params(), output_root=run_dir
        )

        db_path = run_dir / "execution_ledger.db"
        store = ExecutionStore(db_path)
        store._conn.execute("PRAGMA wal_autocheckpoint=0")
        key = store.begin(
            agent_id="A1",
            provider="mock",
            model="mock-v1",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=(),
        )
        store.mark_returned(key, raw_response={"raw": "provider returned"})
        assert Path(f"{db_path}-wal").is_file()
        before = _run_tree_bytes(run_dir)

        backend = _NoDispatchBackend()
        runner = FUWForgeOrchestrator(
            PROJECT_ROOT, registry=model_registry(backend, provider="recording")
        )
        with pytest.raises(RuntimeError) as raised:
            runner.run("resume_run", _resume_params(), output_root=run_dir, resume=True)

        assert str(raised.value) == (
            "Uncertain completion(s) detected for agents ['A1'] on resume. "
            "An explicit recovery choice is required: specify "
            "recovery_choice='retry' or 'abandon'."
        )
        assert backend.calls == 0
        assert store.get(key).state == ExecutionState.RETURNED.value
        assert _run_tree_bytes(run_dir) == before
    finally:
        if store is not None:
            store.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_ledger_inspection_fails_closed_when_corrupt(tmp_path):
    db_path = tmp_path / "execution_ledger.db"
    db_path.write_bytes(b"not sqlite")
    with pytest.raises(RuntimeError, match="cannot be inspected safely"):
        unresolved_recovery_agents(db_path)


def test_existing_ledger_without_receipt_schema_fails_closed(tmp_path):
    db_path = tmp_path / "execution_ledger.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated (id TEXT)")
    with pytest.raises(RuntimeError, match="execution_receipts"):
        unresolved_recovery_agents(db_path)


def test_invalid_recovery_choice_preserves_run_tree(tmp_path):
    run_dir = tmp_path / "run"
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", _resume_params(), output_root=run_dir
    )
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        key = store.begin(
            agent_id="A1",
            provider="mock",
            model="mock-v1",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=(),
        )
        store.mark_returned(key, raw_response={"raw": "provider returned"})
    before = _run_tree_bytes(run_dir)

    backend = _NoDispatchBackend()
    runner = FUWForgeOrchestrator(
        PROJECT_ROOT, registry=model_registry(backend, provider="recording")
    )
    with pytest.raises(ValueError) as raised:
        runner.run(
            "resume_run",
            _resume_params(),
            output_root=run_dir,
            resume=True,
            recovery_choice="bogus",
        )

    assert str(raised.value) == (
        "Invalid recovery choice 'bogus'. Must be 'retry' or 'abandon'."
    )
    assert backend.calls == 0
    assert _run_tree_bytes(run_dir) == before


@pytest.mark.parametrize(
    "choice, expected_state",
    [("retry", ExecutionState.RUNNING.value), ("abandon", ExecutionState.FAILED.value)],
)
def test_explicit_recovery_choice_allows_resume(tmp_path, choice, expected_state):
    run_dir = tmp_path / "run"
    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    runner.run("resume_run", _resume_params(), output_root=run_dir)

    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        key = store.begin(
            agent_id="A1",
            provider="mock",
            model="mock-v1",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=(),
        )
        store.mark_returned(key, raw_response={"raw": "provider returned"})

    summary = runner.run(
        "resume_run",
        _resume_params(),
        output_root=run_dir,
        resume=True,
        recovery_choice=choice,
    )

    assert summary["status"] == "completed"
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        receipt = store.get(key)
        assert receipt.recovery_choice == choice
        assert receipt.state == expected_state


def test_strict_resume_without_evidence_is_refused_without_mutation():
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_resume_strict_"))
    try:
        run_dir = tmp / "run"
        FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
            "resume_run", _resume_params(), output_root=run_dir
        )
        before = _run_tree_bytes(run_dir)

        backend = _NoDispatchBackend()
        runner = FUWForgeOrchestrator(
            PROJECT_ROOT,
            registry=model_registry(backend, provider="recording", strict=True),
            strict=True,
        )
        with pytest.raises(ValueError) as raised:
            runner.run("resume_run", _resume_params(), output_root=run_dir, resume=True)

        assert str(raised.value) == (
            "Strict model analysis requires --evidence-pack with retrieved source documents."
        )
        assert backend.calls == 0
        assert _run_tree_bytes(run_dir) == before
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resume_halt_after_dispatch_is_still_recorded(tmp_path):
    run_dir = tmp_path / "run"
    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    runner.run("resume_run", _resume_params(), output_root=run_dir)

    failing_params = _resume_params()
    failing_params.__dict__["fail_gate"] = True
    with pytest.raises(RuntimeError, match="A1 gate failure"):
        runner.run("resume_run", failing_params, output_root=run_dir, resume=True)

    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert summary["status"] == "halted"
    assert manifest["status"] == "halted"
    assert "A1 gate failure" in summary["halt_reason"]


def test_resume_halt_after_reused_agents_is_still_recorded(tmp_path):
    run_dir = tmp_path / "run"
    params = _resume_params()
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir
    )

    class LateFailureAgent(BaseAgent):
        id = "A6"
        name = "CIO-Synthesis"

        def run(self, context):
            raise RuntimeError("late dispatch failure")

    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    runner.registry.register_instance(LateFailureAgent())
    with pytest.raises(RuntimeError, match="late dispatch failure"):
        runner.run("resume_run", params, output_root=run_dir, resume=True)

    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert summary["status"] == "halted"
    assert manifest["status"] == "halted"
    assert "late dispatch failure" in summary["halt_reason"]


def test_failure_before_dispatch_does_not_create_halted_outputs(tmp_path):
    run_dir = tmp_path / "run"
    backend = _NoDispatchBackend()
    runner = FUWForgeOrchestrator(
        PROJECT_ROOT, registry=model_registry(backend, provider="recording")
    )
    params = _resume_params()
    params.depth = "Not a configured depth"

    with pytest.raises(KeyError, match="Unknown depth mode"):
        runner.run("resume_run", params, output_root=run_dir)

    assert backend.calls == 0
    assert not (run_dir / "run_summary.json").exists()
    assert not (run_dir / "run_manifest.json").exists()


def test_corrupt_reusable_receipt_preserves_run_tree(tmp_path):
    run_dir = tmp_path / "run"
    params = _resume_params()
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir
    )
    with sqlite3.connect(run_dir / "execution_ledger.db") as connection:
        connection.execute(
            "UPDATE execution_receipts "
            "SET accepted_envelope_json = '{}' "
            "WHERE agent_id = 'A1'"
        )
    before = _run_tree_bytes(run_dir)

    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    original_resolve = runner.registry.resolve

    def resolve_without_dispatch(agent_id):
        agent = original_resolve(agent_id)

        def must_not_dispatch(context):
            raise AssertionError("provider was dispatched")

        agent.run = must_not_dispatch
        return agent

    runner.registry.resolve = resolve_without_dispatch
    with pytest.raises(RuntimeError, match="reusable receipt checksum mismatch"):
        runner.run("resume_run", params, output_root=run_dir, resume=True)

    assert _run_tree_bytes(run_dir) == before


@pytest.mark.parametrize(
    "corruption",
    [
        "accepted_checksum",
        "accepted_envelope_json",
        "embedded_checksum_missing",
        "embedded_checksum_mismatch",
    ],
)
def test_inconsistent_validated_receipt_preserves_run_tree(tmp_path, corruption):
    run_dir = tmp_path / "run"
    params = _resume_params()
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir
    )
    db_path = run_dir / "execution_ledger.db"
    with sqlite3.connect(db_path) as connection:
        if corruption in ("accepted_checksum", "accepted_envelope_json"):
            connection.execute(
                f"UPDATE execution_receipts SET {corruption} = NULL "
                "WHERE agent_id = 'A1'"
            )
        else:
            envelope_json, accepted_checksum = connection.execute(
                "SELECT accepted_envelope_json, accepted_checksum "
                "FROM execution_receipts WHERE agent_id = 'A1'"
            ).fetchone()
            envelope = json.loads(envelope_json)
            assert envelope_content_checksum(envelope) == accepted_checksum
            if corruption == "embedded_checksum_missing":
                envelope.pop("content_checksum")
            else:
                envelope["content_checksum"] = "sha256:" + "0" * 64
            connection.execute(
                "UPDATE execution_receipts SET accepted_envelope_json = ? "
                "WHERE agent_id = 'A1'",
                (json.dumps(envelope),),
            )
    before = _run_tree_bytes(run_dir)

    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    original_resolve = runner.registry.resolve
    dispatches = []

    def resolve_without_dispatch(agent_id):
        agent = original_resolve(agent_id)

        def must_not_dispatch(context):
            dispatches.append(agent_id)
            raise AssertionError("provider was dispatched")

        agent.run = must_not_dispatch
        return agent

    runner.registry.resolve = resolve_without_dispatch
    with pytest.raises(RuntimeError, match="reusable receipt checksum mismatch"):
        runner.run("resume_run", params, output_root=run_dir, resume=True)

    assert dispatches == []
    assert _run_tree_bytes(run_dir) == before


def test_valid_wal_backed_receipts_are_reused(tmp_path):
    run_dir = tmp_path / "run"
    params = _resume_params()
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir
    )
    db_path = run_dir / "execution_ledger.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    with connection:
        connection.execute(
            "UPDATE execution_receipts SET updated_at = updated_at || ' ' "
            "WHERE agent_id = 'A1'"
        )
    assert Path(f"{db_path}-wal").stat().st_size > 32

    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    original_resolve = runner.registry.resolve
    dispatches = []

    def resolve_without_dispatch(agent_id):
        agent = original_resolve(agent_id)

        def must_not_dispatch(context):
            dispatches.append(agent_id)
            raise AssertionError("validated receipt was not reused")

        agent.run = must_not_dispatch
        return agent

    runner.registry.resolve = resolve_without_dispatch
    try:
        summary = runner.run(
            "resume_run", params, output_root=run_dir, resume=True
        )
    finally:
        connection.close()

    assert summary["status"] == "completed"
    assert dispatches == []


def test_reused_envelopes_preserve_sealed_semantic_bytes():
    """
    Reuse must reproduce the same sealed content, not merely similar content.
    Raw bytes may reorder because a reused envelope is re-serialized from its
    receipt, but the parsed document and its content checksum must be identical.
    """
    tmp = Path(tempfile.mkdtemp(prefix="fuwforge_resume_reuse_"))
    try:
        pack = _make_evidence_pack(tmp / "pack", "Revenue: 123. Public filing data.")
        run_dir = tmp / "run"

        first = FUWForgeOrchestrator(
            PROJECT_ROOT, provider="mock", model="mock-v1", evidence_pack=pack
        )
        first.run("resume_run", _resume_params(), output_root=run_dir)

        before = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(run_dir.glob("*_envelope.json"))
        }
        assert before

        second = FUWForgeOrchestrator(
            PROJECT_ROOT, provider="mock", model="mock-v1", evidence_pack=pack
        )
        second.run("resume_run", _resume_params(), output_root=run_dir, resume=True)

        after = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(run_dir.glob("*_envelope.json"))
        }

        assert set(before) == set(after)
        for name, original in before.items():
            assert after[name] == original, f"{name} changed across resume"
            # The sealed checksum is order-independent, so it must survive too.
            assert after[name].get("content_checksum") == original.get("content_checksum")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_begining_a_new_attempt_clears_previous_attempt_outcome():
    """
    A receipt must describe exactly one attempt.

    Re-running the same execution key must not leave the previous attempt's raw
    response, accepted envelope, latency, usage, error, or recovery decision
    attached to the new attempt; otherwise a reader would see a 'running'
    receipt carrying a stale result, and a new attempt's uncertainty would
    silently inherit an old operator decision and never be raised again.
    """
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A3",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )

    store.mark_returned(key, raw_response={"raw": "first attempt"}, latency_ms=999)
    store.mark_failed(key, error="previous attempt error")
    store.resolve_recovery(key, "retry")

    first = store.get(key)
    assert first.raw_response == {"raw": "first attempt"}
    assert first.error == "previous attempt error"
    assert first.recovery_choice == "retry"

    # Same execution key, new attempt.
    store.begin(
        agent_id="A3",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    second = store.get(key)

    # Attempt-scoped outcome is cleared.
    assert second.raw_response is None
    assert second.accepted_envelope is None
    assert second.accepted_checksum is None
    assert second.usage is None
    assert second.latency_ms is None
    assert second.error is None
    # recovery_choice is scoped to the attempt that was uncertain.
    assert second.recovery_choice is None

    # Identity and lineage survive so the receipt still describes what runs.
    assert second.provider == "mock"
    assert second.model == "m"
    assert second.prompt_sha256 == PROMPT_HASH_1
    assert second.evidence_sha256 == EVIDENCE_HASH_1
    assert second.upstream == first.upstream


def test_unhashable_custom_agent_fails_closed_on_resume_without_dispatch_or_mutation(
    tmp_path,
):
    run_dir = tmp_path / "run"
    params = _resume_params()
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir
    )
    before = _run_tree_bytes(run_dir)

    dispatched = []

    def _dyn_run(self, context):
        dispatched.append(self.id)
        raise AssertionError("unhashable custom agent must not dispatch on resume")

    dyn_cls = type(
        "DynamicA6",
        (BaseAgent,),
        {"id": "A6", "name": "CIO-Synthesis", "run": _dyn_run},
    )

    runner = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    runner.registry.register_instance(dyn_cls())
    with pytest.raises(RuntimeError, match="cannot be hashed|not source-hashable"):
        runner.run("resume_run", params, output_root=run_dir, resume=True)

    assert dispatched == []
    assert _run_tree_bytes(run_dir) == before

    # Factory-registered agent also fails closed on resume and leaves run tree unchanged.
    runner_factory = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    orig_a6 = runner_factory.registry.resolve("A6")
    runner_factory.registry.register_factory("A6", lambda: orig_a6)
    with pytest.raises(RuntimeError, match="cannot be hashed|not source-hashable"):
        runner_factory.run("resume_run", params, output_root=run_dir, resume=True)

    assert _run_tree_bytes(run_dir) == before

    # Fresh run with an unhashable custom agent that halts at A6 writes a receipt with
    # receipt_reuse=False; a subsequent resume (even with standard mock agents) fails closed.
    run_dir_2 = tmp_path / "run_from_unhashable"
    dyn_fail_cls = type(
        "DynamicFailA6",
        (BaseAgent,),
        {
            "id": "A6",
            "name": "CIO-Synthesis",
            "run": lambda self, ctx: (_ for _ in ()).throw(RuntimeError("dyn boom")),
        },
    )
    runner_initial = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    runner_initial.registry.register_instance(dyn_fail_cls())
    with pytest.raises(RuntimeError, match="dyn boom"):
        runner_initial.run("resume_run", params, output_root=run_dir_2)

    before_2 = _run_tree_bytes(run_dir_2)
    runner_resume = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    with pytest.raises(RuntimeError, match="cannot be hashed|not source-hashable"):
        runner_resume.run(
            "resume_run",
            params,
            output_root=run_dir_2,
            resume=True,
            recovery_choice="retry",
        )
    assert _run_tree_bytes(run_dir_2) == before_2


def test_source_backed_custom_agent_reuses_and_invalidates_on_source_change(tmp_path):
    import importlib.util

    mod_dir = tmp_path / "custom_mod"
    mod_dir.mkdir()
    mod_file = mod_dir / "custom_a7.py"

    def _write_custom_a7(marker: str) -> Any:
        mod_file.write_text(
            "from src.agents.mock import MockAgent\n"
            "class CustomA7(MockAgent):\n"
            "    id = 'A7'\n"
            "    name = 'Final-Assembly'\n"
            "    def __init__(self, prompt_path=None):\n"
            "        super().__init__(agent_id='A7', name='Final-Assembly', prompt_path=prompt_path)\n"
            f"        self._marker = {marker!r}\n",
            encoding="utf-8",
        )
        mod_name = f"custom_a7_{marker}"
        spec = importlib.util.spec_from_file_location(mod_name, mod_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module.CustomA7

    run_dir = tmp_path / "run"
    params = _resume_params()
    cls_v1 = _write_custom_a7("v1")
    try:
        orch1 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
        orch1.registry.register(cls_v1)
        orch1.run("resume_run", params, output_root=run_dir)

        # Unchanged custom agent source: resume reuses A7 without calling run().
        orch2 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
        inst_v1 = cls_v1()
        calls_unchanged = []
        orig_run = inst_v1.run
        inst_v1.run = lambda ctx: (calls_unchanged.append(1), orig_run(ctx))[1]
        orch2.registry.register_instance(inst_v1)
        orch2.run("resume_run", params, output_root=run_dir, resume=True)
        assert calls_unchanged == []

        # Changed custom agent source: resume invalidates A7 key and dispatches new CustomA7.
        cls_v2 = _write_custom_a7("v2")
        orch3 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
        inst_v2 = cls_v2()
        calls_changed = []
        orig_run_v2 = inst_v2.run
        inst_v2.run = lambda ctx: (calls_changed.append(1), orig_run_v2(ctx))[1]
        orch3.registry.register_instance(inst_v2)
        orch3.run("resume_run", params, output_root=run_dir, resume=True)
        assert calls_changed == [1]
    finally:
        for name in ("custom_a7_v1", "custom_a7_v2"):
            sys.modules.pop(name, None)


# -----------------------------------------------------------------------------
# 9. Task 3.3: ExecutionStore state machine, key verification, durable abandon
# -----------------------------------------------------------------------------


def test_execution_store_requires_explicit_db_path_and_shares_memory_across_threads():
    import threading

    with pytest.raises(TypeError):
        ExecutionStore()  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="db_path"):
        ExecutionStore("")

    store = ExecutionStore(":memory:")
    try:
        key = store.begin(
            agent_id="A1",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=(),
        )
        seen: list[ExecutionReceipt | None] = []

        def _worker() -> None:
            seen.append(store.get(key))

        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=5.0)
        assert len(seen) == 1
        assert seen[0] is not None
        assert seen[0].execution_key == key
    finally:
        store.close()


def test_begin_verifies_supplied_execution_key_and_existing_row_identity():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    expected_key = execution_key(
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )

    with pytest.raises(ValueError, match="execution_key"):
        store.begin(
            agent_id="A2",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
            execution_key="fake-non-matching-key",
        )
    assert store.get("fake-non-matching-key") is None

    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
        execution_key=expected_key,
    )
    assert key == expected_key
    store.mark_failed(key, error="pre-response local failure")

    # Attempting to reopen the same key for a different agent_id must fail.
    with pytest.raises(ValueError, match="Identity mismatch"):
        store.begin(
            agent_id="A3",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
            execution_key=expected_key,
        )
    assert store.get(key).agent_id == "A2"


def test_execution_store_enforces_state_machine_and_protects_validated_receipts():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)

    # 1. Invalid initial state in begin()
    with pytest.raises(ValueError, match="state"):
        store.begin(
            agent_id="A2",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
            state="validated",
        )

    # 2. Running state restrictions
    key = store.begin(
        agent_id="A2",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    with pytest.raises(ValueError, match="transition|mark_validated"):
        store.mark_validated(key, accepted_envelope=_sample_accepted_envelope("A2"))
    with pytest.raises(ValueError, match="repair"):
        store.begin_repair(key, "err", PROMPT_HASH_1)
    # In-flight running receipt without retry choice cannot be overwritten by begin()
    with pytest.raises(ValueError, match="transition|begin"):
        store.begin(
            agent_id="A2",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        )

    # 3. Returned state restrictions
    store.mark_returned(key, raw_response=_sample_raw_response())
    with pytest.raises(ValueError, match="transition|begin"):
        store.begin(
            agent_id="A2",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        )

    # 4. Validated state is terminal for normal operations (cannot be demoted)
    accepted_env = _sample_accepted_envelope("A2", CHECKSUM_A2)
    store.mark_validated(key, accepted_envelope=accepted_env)

    for op in (
        lambda: store.mark_failed(key, "cannot fail validated"),
        lambda: store.mark_returned(key, {"raw": "cannot overwrite validated"}),
        lambda: store.mark_validated(key, accepted_envelope=accepted_env),
        lambda: store.mark_unknown_completion(key, "cannot mark uncertain"),
        lambda: store.resolve_recovery(key, "retry"),
        lambda: store.resolve_recovery(key, "abandon"),
        lambda: store.begin_repair(key, "err", PROMPT_HASH_1),
        lambda: store.begin(
            agent_id="A2",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        ),
        lambda: store.begin(
            agent_id="A2",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
            override_abandoned=True,
        ),
    ):
        with pytest.raises(ValueError):
            op()

    assert store.reconcile_unvalidated_on_resume() == []
    receipt = store.get(key)
    assert receipt.state == ExecutionState.VALIDATED.value
    assert receipt.accepted_envelope == accepted_env
    assert receipt.recovery_choice is None

    # 5. Failed state restrictions (no provider response vs response-bearing)
    key_no_resp = store.begin(
        agent_id="A3",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_2,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    store.mark_failed(key_no_resp, "failed before provider return")
    with pytest.raises(ValueError):
        store.mark_validated(key_no_resp, accepted_envelope=_sample_accepted_envelope("A3"))
    with pytest.raises(ValueError):
        store.mark_unknown_completion(key_no_resp, "uncertain")
    with pytest.raises(ValueError):
        store.mark_returned(key_no_resp, {"raw": "late"})
    with pytest.raises(ValueError):
        store.resolve_recovery(key_no_resp, "retry")
    # Pre-response failure CAN be restarted directly with begin()
    store.begin(
        agent_id="A3",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_2,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    assert store.get(key_no_resp).state == ExecutionState.RUNNING.value

    # Now response-bearing failed receipt: cannot begin() without resolve_recovery("retry")
    store.mark_returned(key_no_resp, {"raw": "provider text"})
    store.mark_failed(key_no_resp, "local parse failed")
    with pytest.raises(ValueError, match="transition|begin|recovery"):
        store.begin(
            agent_id="A3",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_2,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        )
    store.resolve_recovery(key_no_resp, "retry")
    store.begin(
        agent_id="A3",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_2,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    assert store.get(key_no_resp).state == ExecutionState.RUNNING.value


def test_abandoned_receipt_is_durable_across_begin_and_resolve_recovery_unless_overridden():
    store = ExecutionStore(":memory:")
    upstream = (UpstreamRef("A1", CHECKSUM_A1),)
    key = store.begin(
        agent_id="A4",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
    )
    store.mark_returned(key, raw_response={"raw": "unrecoverable"})
    store.mark_unknown_completion(key, reason="crash")
    store.resolve_recovery(key, "abandon")

    abandoned = store.get(key)
    assert abandoned.state == ExecutionState.FAILED.value
    assert abandoned.recovery_choice == RecoveryChoice.ABANDON.value

    # Ordinary begin(), resolve_recovery(), mark_failed(), mark_returned() must refuse
    with pytest.raises(ValueError, match="abandoned"):
        store.begin(
            agent_id="A4",
            provider="mock",
            model="m",
            prompt_sha256=PROMPT_HASH_1,
            evidence_sha256=EVIDENCE_HASH_1,
            upstream=upstream,
        )
    with pytest.raises(ValueError, match="abandoned"):
        store.resolve_recovery(key, "retry")
    with pytest.raises(ValueError, match="abandoned"):
        store.mark_failed(key, "new error")
    with pytest.raises(ValueError, match="abandoned"):
        store.mark_returned(key, {"raw": "new"})

    still_abandoned = store.get(key)
    assert still_abandoned.state == ExecutionState.FAILED.value
    assert still_abandoned.recovery_choice == RecoveryChoice.ABANDON.value
    assert still_abandoned.raw_response == {"raw": "unrecoverable"}

    # Explicit override on begin() reopens and clears prior attempt outcome
    store.begin(
        agent_id="A4",
        provider="mock",
        model="m",
        prompt_sha256=PROMPT_HASH_1,
        evidence_sha256=EVIDENCE_HASH_1,
        upstream=upstream,
        override_abandoned=True,
    )
    reopened = store.get(key)
    assert reopened.state == ExecutionState.RUNNING.value
    assert reopened.recovery_choice is None
    assert reopened.raw_response is None
    assert reopened.error is None

    # Explicit override on resolve_recovery() also works
    store.mark_unknown_completion(key, reason="crash 2")
    store.resolve_recovery(key, "abandon")
    retried = store.resolve_recovery(key, "retry", override_abandoned=True)
    assert retried.state == ExecutionState.RUNNING.value
    assert retried.recovery_choice == RecoveryChoice.RETRY.value


# -----------------------------------------------------------------------------
# 10. Task 3.4: Read-only raw files on resume (B8) & sibling persistence (B6)
# -----------------------------------------------------------------------------


def test_resume_does_not_overwrite_raw_files_and_logs_reuse_in_summary_and_provenance(
    tmp_path,
):
    run_dir = tmp_path / "run"
    params = _resume_params()
    orch1 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    orch1.run("resume_run", params, output_root=run_dir)

    raw_Paths = sorted(run_dir.glob("*_raw.json"))
    env_paths = sorted(run_dir.glob("*_envelope.json"))
    assert len(raw_Paths) == 9
    assert len(env_paths) == 9

    # Stamp a sentinel into each raw file so any overwrite on resume is caught.
    raw_before: dict[str, bytes] = {}
    for p in raw_Paths:
        payload = json.loads(p.read_text(encoding="utf-8"))
        payload["original_raw_sentinel"] = p.name
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        raw_before[p.name] = p.read_bytes()
    env_before = {p.name: p.read_bytes() for p in env_paths}

    orch2 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    summary = orch2.run("resume_run", params, output_root=run_dir, resume=True)

    for p in raw_Paths:
        assert p.read_bytes() == raw_before[p.name], f"{p.name} was overwritten on resume"
    for p in env_paths:
        assert p.read_bytes() == env_before[p.name], f"{p.name} was overwritten on resume"

    assert summary.get("reused_agents") == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6B",
        "A6R",
        "A6",
        "A7",
    ]
    for aid in ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
        assert summary["metrics"][aid].get("reused") is True

    prov_text = (
        run_dir / f"{params.ticker}_{params.analysis_date}_provenance.log"
    ).read_text(encoding="utf-8")
    assert "reused=true" in prov_text


def test_resume_restores_missing_or_altered_envelope_but_not_raw(tmp_path):
    run_dir = tmp_path / "run"
    params = _resume_params()
    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir
    )
    a2_env = run_dir / "a2_envelope.json"
    a3_env = run_dir / "a3_envelope.json"
    expected_a2 = json.loads(a2_env.read_text(encoding="utf-8"))
    expected_a3 = json.loads(a3_env.read_text(encoding="utf-8"))
    a2_env.unlink()
    altered = dict(expected_a3, confidence=0.01)
    a3_env.write_text(json.dumps(altered, indent=2), encoding="utf-8")
    raw_before = {p.name: p.read_bytes() for p in run_dir.glob("*_raw.json")}

    FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1").run(
        "resume_run", params, output_root=run_dir, resume=True
    )

    assert json.loads(a2_env.read_text(encoding="utf-8")) == expected_a2
    assert json.loads(a3_env.read_text(encoding="utf-8")) == expected_a3
    assert {p.name: p.read_bytes() for p in run_dir.glob("*_raw.json")} == raw_before


@pytest.mark.parametrize("failing_id", ["A2", "A3", "A4", "A5"])
def test_parallel_sibling_failure_persists_completed_siblings_for_next_resume(
    tmp_path, failing_id
):
    run_dir = tmp_path / f"run_{failing_id}"
    params = _resume_params()

    orch1 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    orig_agent = orch1.registry.resolve(failing_id)
    orig_run = orig_agent.run

    def _fail_gate_once(context):
        out = orig_run(context)
        out.envelope.gates["passed"] = False
        out.envelope.gates["notes"] = [f"{failing_id} simulated gate failure"]
        return out

    orig_agent.run = _fail_gate_once
    orch1.registry.register_instance(orig_agent)

    with pytest.raises(RuntimeError, match=f"{failing_id} hard gate failure"):
        orch1.run("resume_run", params, output_root=run_dir)

    sibling_ids = [aid for aid in ("A2", "A3", "A4", "A5") if aid != failing_id]
    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        for aid in ["A1", *sibling_ids]:
            receipts = store.get_by_agent(aid)
            assert len(receipts) == 1
            assert receipts[0].state == ExecutionState.VALIDATED.value, (
                f"Expected {aid} to be validated when sibling {failing_id} failed, got {receipts[0].state}"
            )
            assert (run_dir / f"{aid.lower()}_envelope.json").is_file()
            assert (run_dir / f"{aid.lower()}_raw.json").is_file()
        for downstream in ("A6B", "A6R", "A6", "A7"):
            assert store.get_by_agent(downstream) == []

    # Because finished siblings were validated and failing_id failed at acceptance
    # (so its receipt is in 'failed' state, not 'returned'/'running'), unresolved
    # recovery check is empty.
    assert unresolved_recovery_agents(run_dir / "execution_ledger.db") == []

    # Now resume with a normal mock orchestrator (and retry for the failed A* receipt):
    # A1 and the 3 healthy siblings must be reused without re-dispatching!
    orch2 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    dispatched: list[str] = []
    for aid in ("A1", "A2", "A3", "A4", "A5", "A6B", "A6R", "A6", "A7"):
        ag = orch2.registry.resolve(aid)
        orig_run = ag.run

        def _wrap(ctx, _aid=aid, _orig=orig_run):
            dispatched.append(_aid)
            return _orig(ctx)

        ag.run = _wrap
        orch2.registry.register_instance(ag)

    summary = orch2.run(
        "resume_run",
        params,
        output_root=run_dir,
        resume=True,
        recovery_choice="retry",
    )
    assert summary["status"] == "completed"
    for aid in ["A1", *sibling_ids]:
        assert aid not in dispatched, f"Sibling {aid} should have been reused on resume"
    assert failing_id in dispatched
    for downstream in ("A6B", "A6R", "A6", "A7"):
        assert downstream in dispatched


def test_parallel_sibling_run_exception_still_accepts_completed_siblings(tmp_path):
    run_dir = tmp_path / "run_exc_a3"
    params = _resume_params()

    orch1 = FUWForgeOrchestrator(PROJECT_ROOT, provider="mock", model="mock-v1")
    a3 = orch1.registry.resolve("A3")
    a3.run = lambda ctx: (_ for _ in ()).throw(RuntimeError("A3 thread boom"))
    orch1.registry.register_instance(a3)

    with pytest.raises(RuntimeError, match="A3 thread boom"):
        orch1.run("resume_run", params, output_root=run_dir)

    with ExecutionStore(run_dir / "execution_ledger.db") as store:
        for aid in ("A1", "A2", "A4", "A5"):
            receipts = store.get_by_agent(aid)
            assert len(receipts) == 1
            assert receipts[0].state == ExecutionState.VALIDATED.value
            assert (run_dir / f"{aid.lower()}_envelope.json").is_file()
        assert store.get_by_agent("A6B") == []
