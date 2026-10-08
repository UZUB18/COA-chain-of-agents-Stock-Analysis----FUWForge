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
from src.execution_store import (
    ExecutionReceipt,
    ExecutionState,
    ExecutionStore,
    RecoveryChoice,
    compute_raw_checksum,
    validate_receipt,
)
from src.orchestrator import FUWForgeOrchestrator, OrchestratorParams


CHECKSUM_A1 = "sha256:" + "a" * 64
CHECKSUM_A2 = "sha256:" + "b" * 64
CHECKSUM_A3 = "sha256:" + "c" * 64
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
    return {
        "schema_version": "2.1",
        "agent": {"id": agent_id, "name": f"Agent {agent_id}"},
        "gates": {"passed": True, "notes": ["All criteria met"]},
        "sections_md": {"ANALYSIS": "Validated financial analysis"},
        "artifacts": {"data": 123},
        "sources": [],
        "metrics": {"data_checksum": checksum},
        "content_checksum": checksum,
    }


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
    receipt = store.mark_validated(key, accepted_envelope=envelope, accepted_checksum=CHECKSUM_A2)

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
        prompt_sha256=PROMPT_HASH_1,
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
            prompt_sha256=PROMPT_HASH_1,
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
        store.mark_validated(key, _sample_accepted_envelope("A3"), accepted_checksum=CHECKSUM_A3)
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

    # Now selectively invalidate A3
    invalidated = store.invalidate_descendants(graph, ["A3"])
    assert invalidated == {"A3", "A6B", "A6R", "A6", "A7"}

    # Siblings A1, A2, A4, A5 remain validated and reusable!
    reusable_after = store.reusable_nodes(keys)
    assert set(reusable_after.keys()) == {"A1", "A2", "A4", "A5"}
    assert "A3" not in reusable_after
    assert "A6B" not in reusable_after


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
    custom_acc_ck = CHECKSUM_A2
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
    receipt = store.mark_validated(
        key,
        accepted_envelope=_sample_accepted_envelope("A2", CHECKSUM_A2),
        accepted_checksum=CHECKSUM_A2,
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

        changed = _make_evidence_pack(tmp / "pack_b", "Revenue: 999. Different filing.")
        second = FUWForgeOrchestrator(
            PROJECT_ROOT, provider="mock", model="mock-v1", evidence_pack=changed
        )
        with pytest.raises(ValueError, match="different evidence snapshot"):
            second.run("resume_run", _resume_params(), output_root=run_dir, resume=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    store.mark_validated(key, accepted_envelope={"agent": {"id": "A3"}}, accepted_checksum="cks1")
    store.mark_failed(key, error="previous attempt error")
    store.resolve_recovery(key, "retry")

    first = store.get(key)
    assert first.raw_response == {"raw": "first attempt"}
    assert first.accepted_checksum == "cks1"
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
