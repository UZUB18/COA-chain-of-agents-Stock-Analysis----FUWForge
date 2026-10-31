"""
Durable SQLite-backed execution store and selective resume ledger.

Idempotency and Billing Constraints:
------------------------------------
- Local idempotency guarantees that stored results are reused only when all
  execution inputs (provider, exact requested model, prompt hash, evidence hash,
  consumed upstream envelopes) match an existing validated record.
- Local idempotency DOES NOT guarantee exactly-once billing by external providers.
- A retry can repeat a paid external call if the previous call succeeded but its
  receipt was not stored (e.g., process crash or network partition during/after
  the call).
- State transitions strictly distinguish between raw provider completion and
  orchestrator validation:
    * `pending`: registered before call dispatch.
    * `running`: call is in flight.
    * `returned`: raw provider response has returned and is atomically persisted
      to disk, but has NOT yet passed semantic/gate validation.
    * `validated`: orchestrator has verified schema, checksums, contracts, and gates.
      Only `validated` nodes are safely reusable.
    * `failed`: provider call or validation failed with an explicit error.
    * `unknown_completion`: execution was interrupted (e.g., crash during `returned`
      or `running` state) or cannot be confirmed as safely valid.
- `returned != validated`. A `returned` node is NOT safely reusable on resume.
  Resuming when an agent is in `returned` or `running` state flags it as
  `unknown_completion`, requiring an explicit operator recovery choice:
    - `retry`: Re-executes the node. WARNING: this may duplicate an external paid
      call if the provider already processed the previous request.
    - `abandon`: Marks the node abandoned, preventing further execution attempts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import uuid
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple, Union

from src.execution_identity import (
    UpstreamRef,
    _bare_hex,
    _hash,
    execution_key as compute_execution_key,
    true_descendants,
)
from src.resources import get_schema_path
from src.utils.checksum import envelope_content_checksum
from src.validators import ValidationResult

try:
    from jsonschema import Draft202012Validator  # type: ignore
except ImportError:  # pragma: no cover
    Draft202012Validator = None  # type: ignore

SCHEMA_PATH = get_schema_path("execution_receipt_v1.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_raw_checksum(data: Any) -> str:
    """Compute sha256 checksum for raw response data."""
    raw_str = _canonical_json(data) if not isinstance(data, str) else data
    return f"sha256:{hashlib.sha256(raw_str.encode('utf-8')).hexdigest()}"


def receipt_envelope_checksum_matches(
    envelope: Any, accepted_checksum: Optional[str]
) -> bool:
    """Require the embedded, receipt, and recomputed envelope checksums to agree."""
    return (
        isinstance(envelope, dict)
        and isinstance(accepted_checksum, str)
        and envelope.get("content_checksum") == accepted_checksum
        and envelope_content_checksum(envelope) == accepted_checksum
    )


class ExecutionState(str, Enum):
    """Lifecycle states for an agent execution receipt."""

    PENDING = "pending"
    RUNNING = "running"
    RETURNED = "returned"
    VALIDATED = "validated"
    FAILED = "failed"
    UNKNOWN_COMPLETION = "unknown_completion"


class RecoveryChoice(str, Enum):
    """Explicit operator recovery choice for uncertain completions."""

    RETRY = "retry"
    ABANDON = "abandon"


@dataclass(frozen=True)
class ExecutionReceipt:
    """Immutable receipt record for one agent execution."""

    schema_version: str
    execution_key: str
    agent_id: str
    state: str
    provider: str
    model: str
    prompt_sha256: str
    evidence_sha256: str
    upstream: Tuple[UpstreamRef, ...]
    created_at: str
    updated_at: str
    raw_checksum: Optional[str] = None
    accepted_checksum: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    accepted_envelope: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    recovery_choice: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize receipt to JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "execution_key": self.execution_key,
            "agent_id": self.agent_id,
            "state": self.state,
            "provider": self.provider,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "evidence_sha256": self.evidence_sha256,
            "upstream": [
                {"agent": ref.agent, "content_checksum": ref.content_checksum}
                for ref in self.upstream
            ],
            "raw_checksum": self.raw_checksum,
            "accepted_checksum": self.accepted_checksum,
            "raw_response": self.raw_response,
            "accepted_envelope": self.accepted_envelope,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "recovery_choice": self.recovery_choice,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionReceipt:
        """Construct receipt from serialized dictionary."""
        upstream_raw = data.get("upstream") or []
        upstream = tuple(
            UpstreamRef(
                agent=entry["agent"],
                content_checksum=entry["content_checksum"],
            )
            for entry in upstream_raw
        )
        return cls(
            schema_version=data.get("schema_version", "execution_receipt_v1"),
            execution_key=data["execution_key"],
            agent_id=data["agent_id"],
            state=data["state"],
            provider=data["provider"],
            model=data["model"],
            prompt_sha256=data["prompt_sha256"],
            evidence_sha256=data["evidence_sha256"],
            upstream=upstream,
            raw_checksum=data.get("raw_checksum"),
            accepted_checksum=data.get("accepted_checksum"),
            raw_response=data.get("raw_response"),
            accepted_envelope=data.get("accepted_envelope"),
            usage=data.get("usage"),
            latency_ms=data.get("latency_ms"),
            error=data.get("error"),
            recovery_choice=data.get("recovery_choice"),
            metadata=data.get("metadata"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
        )

    def is_validated(self) -> bool:
        """Check if receipt has reached the validated state."""
        return self.state == ExecutionState.VALIDATED.value

    def is_safely_reusable(self) -> bool:
        """
        Check if receipt is validated and safe to reuse without re-execution.

        Only validated receipts with accepted envelope and valid checksum
        and without an explicit abandon recovery choice are safely reusable.
        """
        meta = self.metadata if isinstance(self.metadata, dict) else {}
        return (
            self.state == ExecutionState.VALIDATED.value
            and receipt_envelope_checksum_matches(
                self.accepted_envelope, self.accepted_checksum
            )
            and self.recovery_choice != RecoveryChoice.ABANDON.value
            and meta.get("receipt_reuse") is not False
            and meta.get("agent_identity_source") != "unavailable"
        )

    def requires_recovery(self) -> bool:
        """Check if receipt requires explicit operator recovery."""
        return (
            self.state in (ExecutionState.RETURNED.value, ExecutionState.UNKNOWN_COMPLETION.value)
            and self.recovery_choice is None
        )

    def has_provider_response(self) -> bool:
        """Return True if a provider response was recorded on this receipt."""
        return self.raw_response is not None or self.raw_checksum is not None


def validate_receipt(data: Dict[str, Any]) -> ValidationResult:
    """Validate a receipt dictionary against schemas/execution_receipt_v1.json."""
    if Draft202012Validator is not None:
        try:
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
            if errors:
                return ValidationResult(
                    valid=False,
                    errors=[f"{'/'.join(map(str, err.path))}: {err.message}" for err in errors],
                )
            return ValidationResult(valid=True, errors=[])
        except Exception as e:
            return ValidationResult(valid=False, errors=[str(e)])

    # Structural fallback
    required = [
        "schema_version",
        "execution_key",
        "agent_id",
        "state",
        "provider",
        "model",
        "prompt_sha256",
        "evidence_sha256",
        "upstream",
        "created_at",
        "updated_at",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        return ValidationResult(valid=False, errors=[f"Missing keys: {', '.join(missing)}"])
    return ValidationResult(valid=True, errors=[])


def unresolved_recovery_agents(db_path: Union[str, Path]) -> List[str]:
    """Return unresolved agents after validating a private copy, including WAL."""
    path = Path(db_path)
    candidates = [path, Path(f"{path}-wal"), Path(f"{path}-shm")]

    def state() -> Dict[Path, Optional[Tuple[int, int]]]:
        return {
            candidate: (
                (candidate.stat().st_size, candidate.stat().st_mtime_ns)
                if candidate.is_file()
                else None
            )
            for candidate in candidates
        }

    if not path.exists():
        if any(candidate.exists() for candidate in candidates[1:]):
            raise RuntimeError("Execution ledger sidecar exists without its database.")
        return []
    if not path.is_file():
        raise RuntimeError("Execution ledger path is not a regular file.")
    wal_path, shm_path = candidates[1:]
    if any(candidate.exists() and not candidate.is_file() for candidate in candidates[1:]):
        raise RuntimeError("Execution ledger sidecar is not a regular file.")
    if shm_path.exists() and not wal_path.exists():
        raise RuntimeError("Execution ledger shared-memory file exists without its WAL.")

    before = state()
    with tempfile.TemporaryDirectory(prefix="fuwforge-ledger-inspect-") as temp_dir:
        copied = Path(temp_dir) / path.name
        for candidate in candidates:
            if candidate.is_file():
                shutil.copyfile(candidate, copied.with_name(candidate.name))
        # shortcut: resume inspection assumes no concurrent writer; replace this
        # copied-file check with SQLite backup/locking when concurrent resumes exist.
        if state() != before:
            raise RuntimeError("Execution ledger changed during read-only inspection.")

        connection = sqlite3.connect(copied)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if integrity != [("ok",)]:
                raise RuntimeError(
                    f"Execution ledger failed integrity check: {integrity!r}"
                )
            has_table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'execution_receipts'"
            ).fetchone()
            if not has_table:
                raise RuntimeError(
                    "Existing execution ledger is missing the execution_receipts table."
                )
            all_rows = connection.execute(
                """
                SELECT agent_id, state, accepted_envelope_json, accepted_checksum, metadata_json
                FROM execution_receipts
                """
            ).fetchall()
            for agent_id, row_state, envelope_json, accepted_checksum, metadata_json in all_rows:
                if isinstance(metadata_json, str) and metadata_json.strip():
                    try:
                        meta_obj = json.loads(metadata_json)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"{agent_id}: reusable receipt checksum mismatch"
                        ) from exc
                    if isinstance(meta_obj, dict) and (
                        meta_obj.get("receipt_reuse") is False
                        or meta_obj.get("agent_identity_source") == "unavailable"
                    ):
                        raise RuntimeError(
                            f"{agent_id}: custom agent implementation cannot be hashed; refusing to resume"
                        )
                if row_state != "validated":
                    continue
                envelope = None
                if isinstance(envelope_json, str):
                    try:
                        envelope = json.loads(envelope_json)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"{agent_id}: reusable receipt checksum mismatch"
                        ) from exc
                if not receipt_envelope_checksum_matches(envelope, accepted_checksum):
                    raise RuntimeError(
                        f"{agent_id}: reusable receipt checksum mismatch"
                    )
            rows = connection.execute(
                """
                SELECT agent_id
                FROM execution_receipts
                WHERE state IN ('running', 'returned', 'unknown_completion')
                  AND recovery_choice IS NULL
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [row[0] for row in rows]
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"Execution ledger cannot be inspected safely: {exc}"
            ) from exc
        finally:
            connection.close()


class ExecutionStore:
    """
    SQLite-backed durable ledger for agent execution receipts and resume.

    Provides atomic persistence of provider returns and validation milestones,
    selective recomputation with true dependency invalidation, and explicit
    handling of uncertain completions.
    """

    ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
        "begin": frozenset({
            ExecutionState.PENDING.value,
            ExecutionState.RUNNING.value,
            ExecutionState.FAILED.value,
        }),
        "begin_repair": frozenset({
            ExecutionState.RETURNED.value,
        }),
        "mark_returned": frozenset({
            ExecutionState.PENDING.value,
            ExecutionState.RUNNING.value,
            ExecutionState.RETURNED.value,
            ExecutionState.UNKNOWN_COMPLETION.value,
            ExecutionState.FAILED.value,
        }),
        "mark_validated": frozenset({
            ExecutionState.RETURNED.value,
        }),
        "mark_failed": frozenset({
            ExecutionState.PENDING.value,
            ExecutionState.RUNNING.value,
            ExecutionState.RETURNED.value,
            ExecutionState.UNKNOWN_COMPLETION.value,
            ExecutionState.FAILED.value,
        }),
        "mark_unknown_completion": frozenset({
            ExecutionState.PENDING.value,
            ExecutionState.RUNNING.value,
            ExecutionState.RETURNED.value,
            ExecutionState.UNKNOWN_COMPLETION.value,
        }),
        "resolve_recovery": frozenset({
            ExecutionState.RUNNING.value,
            ExecutionState.RETURNED.value,
            ExecutionState.UNKNOWN_COMPLETION.value,
            ExecutionState.FAILED.value,
        }),
        "reconcile_unvalidated_on_resume": frozenset({
            ExecutionState.RUNNING.value,
            ExecutionState.RETURNED.value,
        }),
        "invalidate_descendants": frozenset({
            ExecutionState.VALIDATED.value,
        }),
    }

    def __init__(
        self,
        db_path: Union[str, Path],
        timeout: float = 30.0,
    ) -> None:
        raw_path = str(db_path).strip() if db_path is not None else ""
        if not raw_path:
            raise ValueError("ExecutionStore requires a non-empty db_path")
        self.db_path = raw_path
        self.timeout = timeout
        if self.db_path == ":memory:":
            self._connect_target = (
                f"file:fuwforge_mem_{uuid.uuid4().hex}?mode=memory&cache=shared"
            )
            self._use_uri = True
        else:
            self._connect_target = self.db_path
            self._use_uri = self.db_path.startswith("file:")
        self._local = threading.local()
        self._connections: List[sqlite3.Connection] = []
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        # Each parallel agent needs an independent transaction/connection.
        if not hasattr(self._local, "connection"):
            conn = sqlite3.connect(
                self._connect_target,
                timeout=self.timeout,
                uri=self._use_uri,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            self._local.connection = conn
            self._connections.append(conn)
        return self._local.connection

    def _get_row_for_transition(self, execution_key: str) -> sqlite3.Row:
        row = self._conn.execute(
            """
            SELECT execution_key, agent_id, state, provider, model,
                   prompt_sha256, evidence_sha256, upstream_json,
                   raw_checksum, raw_response_json, recovery_choice, metadata_json
            FROM execution_receipts
            WHERE execution_key = ?
            """,
            (execution_key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"No execution record found for key {execution_key}")
        return row

    @classmethod
    def _row_has_provider_response(cls, row: sqlite3.Row) -> bool:
        return row["raw_response_json"] is not None or row["raw_checksum"] is not None

    @classmethod
    def _check_transition(
        cls,
        operation: str,
        row: sqlite3.Row,
        *,
        override_abandoned: bool = False,
    ) -> None:
        current_state = row["state"]
        key = row["execution_key"]
        allowed = cls.ALLOWED_TRANSITIONS.get(operation, frozenset())
        if current_state not in allowed:
            raise ValueError(
                f"Illegal execution state transition: {operation} from state {current_state!r} for key {key}"
            )
        choice = row["recovery_choice"]
        if choice == RecoveryChoice.ABANDON.value:
            if not (override_abandoned and operation in ("begin", "resolve_recovery")):
                raise ValueError(
                    f"Execution {key} was explicitly abandoned; pass override_abandoned=True to reopen"
                )
        if operation == "begin":
            if override_abandoned and choice == RecoveryChoice.ABANDON.value:
                return
            if current_state == ExecutionState.RUNNING.value:
                if choice != RecoveryChoice.RETRY.value:
                    raise ValueError(
                        f"Illegal execution state transition: cannot begin in-flight 'running' key {key} without retry recovery choice"
                    )
            elif current_state == ExecutionState.FAILED.value:
                if choice != RecoveryChoice.RETRY.value and cls._row_has_provider_response(row):
                    raise ValueError(
                        f"Illegal execution state transition: response-bearing failed key {key} requires retry recovery choice before begin"
                    )
        elif operation == "begin_repair":
            if not row["raw_checksum"]:
                raise ValueError("repair requires a recorded provider response")
        elif operation == "mark_returned":
            if current_state in (
                ExecutionState.FAILED.value,
                ExecutionState.UNKNOWN_COMPLETION.value,
            ) and not cls._row_has_provider_response(row):
                raise ValueError(
                    f"Illegal execution state transition: cannot mark_returned from {current_state!r} without a recorded provider response"
                )
        elif operation == "resolve_recovery":
            if current_state == ExecutionState.FAILED.value and not (
                cls._row_has_provider_response(row)
                or (override_abandoned and choice == RecoveryChoice.ABANDON.value)
            ):
                raise ValueError(
                    f"Illegal execution state transition: cannot resolve_recovery on pre-response failed key {key}"
                )

    def _init_db(self) -> None:
        if self.db_path != ":memory:":
            p = Path(self.db_path)
            if p.is_file() and p.stat().st_size > 0:
                has_table = self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'execution_receipts'"
                ).fetchone()
                if has_table:
                    return
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_receipts (
                    execution_key TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    upstream_json TEXT NOT NULL,
                    raw_checksum TEXT,
                    accepted_checksum TEXT,
                    raw_response_json TEXT,
                    accepted_envelope_json TEXT,
                    usage_json TEXT,
                    latency_ms INTEGER,
                    error TEXT,
                    recovery_choice TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_agent_state ON execution_receipts(agent_id, state)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_state ON execution_receipts(state)"
            )

    def close(self) -> None:
        """Close SQLite database connection."""
        for conn in self._connections:
            conn.close()
        self._connections.clear()
        if hasattr(self._local, "connection"):
            delattr(self._local, "connection")

    def __enter__(self) -> ExecutionStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def begin(
        self,
        *,
        agent_id: str,
        provider: str,
        model: str,
        prompt_sha256: str,
        evidence_sha256: str,
        upstream: Tuple[UpstreamRef, ...],
        execution_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        state: Union[ExecutionState, str] = ExecutionState.RUNNING,
        override_abandoned: bool = False,
    ) -> str:
        """
        Record the initiation of an agent execution.

        Calculates or verifies the content-addressed execution_key and atomically
        persists the initial row in `running` or `pending` state.
        """
        state_val = state.value if isinstance(state, ExecutionState) else str(state)
        if state_val not in (ExecutionState.PENDING.value, ExecutionState.RUNNING.value):
            raise ValueError(
                f"Invalid initial execution state {state_val!r}. Must be 'pending' or 'running'."
            )
        expected_key = compute_execution_key(
            provider=provider,
            model=model,
            prompt_sha256=prompt_sha256,
            evidence_sha256=evidence_sha256,
            upstream=upstream,
        )
        if execution_key is not None and execution_key != expected_key:
            raise ValueError(
                f"Supplied execution_key {execution_key!r} does not match computed key {expected_key!r}"
            )
        key = expected_key
        now = _now_iso()
        upstream_json = _canonical_json(
            [{"agent": ref.agent, "content_checksum": ref.content_checksum} for ref in upstream]
        )
        metadata_json = _canonical_json(metadata) if metadata is not None else None

        with self._conn:
            existing = self._conn.execute(
                """
                SELECT execution_key, agent_id, state, provider, model,
                       prompt_sha256, evidence_sha256, upstream_json,
                       raw_checksum, raw_response_json, recovery_choice, metadata_json
                FROM execution_receipts
                WHERE execution_key = ?
                """,
                (key,),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO execution_receipts (
                        execution_key, agent_id, state, provider, model,
                        prompt_sha256, evidence_sha256, upstream_json,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        agent_id,
                        state_val,
                        provider,
                        model,
                        prompt_sha256,
                        evidence_sha256,
                        upstream_json,
                        metadata_json,
                        now,
                        now,
                    ),
                )
            else:
                if (
                    existing["agent_id"] != agent_id
                    or existing["provider"] != provider
                    or existing["model"] != model
                    or existing["prompt_sha256"] != prompt_sha256
                    or existing["evidence_sha256"] != evidence_sha256
                    or existing["upstream_json"] != upstream_json
                ):
                    raise ValueError(
                        f"Identity mismatch for existing execution_key {key}"
                    )
                self._check_transition(
                    "begin",
                    existing,
                    override_abandoned=override_abandoned,
                )
                cursor = self._conn.execute(
                    """
                    UPDATE execution_receipts SET
                        state = ?,
                        updated_at = ?,
                        metadata_json = COALESCE(?, metadata_json),
                        -- Starting a new attempt invalidates the previous attempt's
                        -- outcome. Leaving these in place would let one receipt
                        -- describe two different executions at once: a reader could
                        -- see a 'running' attempt carrying the prior attempt's raw
                        -- response, error, latency, usage, or an already-accepted
                        -- envelope. recovery_choice MUST also be cleared: it is
                        -- scoped to the attempt that was uncertain, and keeping it
                        -- would silently discharge the new attempt's recovery
                        -- obligation (requires_recovery() only fires when it is NULL).
                        -- Identity and lineage (provider/model/prompt/evidence/
                        -- upstream/created_at) describe what is being executed and
                        -- are deliberately preserved.
                        raw_checksum = NULL,
                        raw_response_json = NULL,
                        accepted_checksum = NULL,
                        accepted_envelope_json = NULL,
                        usage_json = NULL,
                        latency_ms = NULL,
                        error = NULL,
                        recovery_choice = NULL
                    WHERE execution_key = ? AND state = ?
                    """,
                    (state_val, now, metadata_json, key, existing["state"]),
                )
                if cursor.rowcount == 0:
                    raise ValueError(
                        f"Concurrent or illegal state transition on begin for key {key}"
                    )
        return key

    def begin_repair(self, execution_key: str, error: str, prompt_sha256: str) -> None:
        """Keep the rejected response and atomically mark the repair in flight."""
        with self._conn:
            row = self._conn.execute(
                """
                SELECT execution_key, agent_id, state, provider, model,
                       prompt_sha256, evidence_sha256, upstream_json,
                       raw_checksum, raw_response_json, recovery_choice, metadata_json
                FROM execution_receipts
                WHERE execution_key = ?
                """,
                (execution_key,),
            ).fetchone()
            if not row or row["state"] != ExecutionState.RETURNED.value or not row["raw_checksum"]:
                raise ValueError("repair requires a recorded provider response")
            self._check_transition("begin_repair", row)
            history_key = hashlib.sha256(
                f"{execution_key}:repair:{row['raw_checksum']}:{uuid.uuid4()}".encode()
            ).hexdigest()
            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            self._conn.execute(
                """INSERT INTO execution_receipts (
                    execution_key, agent_id, state, provider, model, prompt_sha256,
                    evidence_sha256, upstream_json, raw_checksum, raw_response_json,
                    usage_json, latency_ms, error, metadata_json, created_at, updated_at
                ) SELECT ?, agent_id, 'failed', provider, model, prompt_sha256,
                    evidence_sha256, upstream_json, raw_checksum, raw_response_json,
                    usage_json, latency_ms, ?, ?, created_at, ?
                  FROM execution_receipts WHERE execution_key = ?""",
                (history_key, error, _canonical_json({**metadata, "repair_of": execution_key}), _now_iso(), execution_key),
            )
            cursor = self._conn.execute(
                """UPDATE execution_receipts SET state = 'running', raw_checksum = NULL,
                    raw_response_json = NULL, accepted_checksum = NULL, accepted_envelope_json = NULL,
                    usage_json = NULL, latency_ms = NULL, error = NULL, recovery_choice = NULL, metadata_json = ?,
                    updated_at = ? WHERE execution_key = ? AND state = ?""",
                (
                    _canonical_json({**metadata, "repair_prompt_sha256": prompt_sha256}),
                    _now_iso(),
                    execution_key,
                    row["state"],
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Illegal state transition on begin_repair for key {execution_key}")

    def mark_returned(
        self,
        execution_key: str,
        raw_response: Dict[str, Any],
        raw_checksum: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        latency_ms: Optional[int] = None,
    ) -> ExecutionReceipt:
        """
        Atomically persist raw provider response.

        Transitions state to `returned`. Note that `returned != validated`.
        A crash after this call leaves a durable record of provider return,
        preventing silent re-execution while requiring validation before reuse.
        """
        now = _now_iso()
        ck = raw_checksum or compute_raw_checksum(raw_response)
        raw_json = _canonical_json(raw_response)
        usage_json = _canonical_json(usage) if usage is not None else None

        with self._conn:
            row = self._get_row_for_transition(execution_key)
            self._check_transition("mark_returned", row)
            cursor = self._conn.execute(
                """
                UPDATE execution_receipts SET
                    state = ?,
                    raw_checksum = ?,
                    raw_response_json = ?,
                    usage_json = ?,
                    latency_ms = COALESCE(?, latency_ms),
                    updated_at = ?
                WHERE execution_key = ? AND state = ?
                """,
                (
                    ExecutionState.RETURNED.value,
                    ck,
                    raw_json,
                    usage_json,
                    latency_ms,
                    now,
                    execution_key,
                    row["state"],
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Illegal state transition on mark_returned for key {execution_key}")

        receipt = self.get(execution_key)
        assert receipt is not None
        return receipt

    def mark_validated(
        self,
        execution_key: str,
        accepted_envelope: Dict[str, Any],
        accepted_checksum: Optional[str] = None,
    ) -> ExecutionReceipt:
        """
        Atomically mark execution as validated and accepted.

        Transitions state to `validated`. Only receipts in this state are
        safely reusable on resume.
        """
        now = _now_iso()
        ck = accepted_checksum or accepted_envelope.get("content_checksum") or compute_raw_checksum(accepted_envelope)
        envelope_json = _canonical_json(accepted_envelope)

        with self._conn:
            row = self._get_row_for_transition(execution_key)
            self._check_transition("mark_validated", row)
            cursor = self._conn.execute(
                """
                UPDATE execution_receipts SET
                    state = ?,
                    accepted_checksum = ?,
                    accepted_envelope_json = ?,
                    error = NULL,
                    recovery_choice = NULL,
                    updated_at = ?
                WHERE execution_key = ? AND state = ?
                """,
                (
                    ExecutionState.VALIDATED.value,
                    ck,
                    envelope_json,
                    now,
                    execution_key,
                    row["state"],
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Illegal state transition on mark_validated for key {execution_key}")

        receipt = self.get(execution_key)
        assert receipt is not None
        return receipt

    def mark_failed(
        self,
        execution_key: str,
        error: str,
        error_details: Optional[Dict[str, Any]] = None,
    ) -> ExecutionReceipt:
        """Atomically mark execution as failed."""
        now = _now_iso()
        err_msg = error
        if error_details:
            err_msg = f"{error} | Details: {_canonical_json(error_details)}"

        with self._conn:
            row = self._get_row_for_transition(execution_key)
            self._check_transition("mark_failed", row)
            cursor = self._conn.execute(
                """
                UPDATE execution_receipts SET
                    state = ?,
                    error = ?,
                    updated_at = ?
                WHERE execution_key = ? AND state = ?
                """,
                (
                    ExecutionState.FAILED.value,
                    err_msg,
                    now,
                    execution_key,
                    row["state"],
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Illegal state transition on mark_failed for key {execution_key}")

        receipt = self.get(execution_key)
        assert receipt is not None
        return receipt

    def mark_unknown_completion(
        self,
        execution_key: str,
        reason: Optional[str] = None,
    ) -> ExecutionReceipt:
        """
        Atomically mark execution as unknown_completion.

        Indicates that provider completion is uncertain or unvalidated,
        requiring explicit operator recovery choice (`retry` vs `abandon`).
        """
        now = _now_iso()
        with self._conn:
            row = self._get_row_for_transition(execution_key)
            self._check_transition("mark_unknown_completion", row)
            cursor = self._conn.execute(
                """
                UPDATE execution_receipts SET
                    state = ?,
                    error = COALESCE(?, error),
                    updated_at = ?
                WHERE execution_key = ? AND state = ?
                """,
                (
                    ExecutionState.UNKNOWN_COMPLETION.value,
                    reason,
                    now,
                    execution_key,
                    row["state"],
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Illegal state transition on mark_unknown_completion for key {execution_key}")

        receipt = self.get(execution_key)
        assert receipt is not None
        return receipt

    def resolve_recovery(
        self,
        execution_key: str,
        choice: Union[RecoveryChoice, str],
        *,
        override_abandoned: bool = False,
    ) -> ExecutionReceipt:
        """
        Record explicit operator recovery choice for uncertain completion.

        Allowed choices:
        - `retry`: Marks intent to re-execute.
          NOTE: Re-executing may duplicate an external paid call if the provider
          processed the request previously.
        - `abandon`: Marks intent to abandon this execution node.

        A recorded decision clears the recovery obligation, so a later resume
        pass does not re-prompt an operator about a node they already answered.
        `retry` moves the node back to ``running`` (a fresh attempt may now be
        made); `abandon` moves it to ``failed``. Neither is reusable until a
        subsequent call actually returns and validates.
        """
        choice_val = choice.value if isinstance(choice, RecoveryChoice) else str(choice)
        if choice_val not in (RecoveryChoice.RETRY.value, RecoveryChoice.ABANDON.value):
            raise ValueError(f"Invalid recovery choice '{choice}'. Must be 'retry' or 'abandon'.")

        now = _now_iso()
        next_state = (
            ExecutionState.RUNNING.value
            if choice_val == RecoveryChoice.RETRY.value
            else ExecutionState.FAILED.value
        )
        with self._conn:
            row = self._get_row_for_transition(execution_key)
            self._check_transition(
                "resolve_recovery",
                row,
                override_abandoned=override_abandoned,
            )
            cursor = self._conn.execute(
                """
                UPDATE execution_receipts SET
                    recovery_choice = ?,
                    state = ?,
                    updated_at = ?
                WHERE execution_key = ? AND state = ?
                """,
                (
                    choice_val,
                    next_state,
                    now,
                    execution_key,
                    row["state"],
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Illegal state transition on resolve_recovery for key {execution_key}")

        receipt = self.get(execution_key)
        assert receipt is not None
        return receipt

    def get(self, execution_key: str) -> Optional[ExecutionReceipt]:
        """Look up execution receipt by execution_key."""
        cursor = self._conn.execute(
            """
            SELECT execution_key, agent_id, state, provider, model,
                   prompt_sha256, evidence_sha256, upstream_json,
                   raw_checksum, accepted_checksum, raw_response_json,
                   accepted_envelope_json, usage_json, latency_ms,
                   error, recovery_choice, metadata_json, created_at, updated_at
            FROM execution_receipts
            WHERE execution_key = ?
            """,
            (execution_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_receipt(row)

    def lookup(self, execution_key: str) -> Optional[ExecutionReceipt]:
        """Alias for get(execution_key)."""
        return self.get(execution_key)

    def get_by_agent(self, agent_id: str) -> List[ExecutionReceipt]:
        """Retrieve all receipts for a specific agent_id, newest first."""
        cursor = self._conn.execute(
            """
            SELECT execution_key, agent_id, state, provider, model,
                   prompt_sha256, evidence_sha256, upstream_json,
                   raw_checksum, accepted_checksum, raw_response_json,
                   accepted_envelope_json, usage_json, latency_ms,
                   error, recovery_choice, metadata_json, created_at, updated_at
            FROM execution_receipts
            WHERE agent_id = ?
            ORDER BY updated_at DESC
            """,
            (agent_id,),
        )
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def list_receipts(
        self,
        state: Optional[Union[ExecutionState, str]] = None,
    ) -> List[ExecutionReceipt]:
        """List receipts, optionally filtered by state."""
        if state is not None:
            state_val = state.value if isinstance(state, ExecutionState) else str(state)
            cursor = self._conn.execute(
                """
                SELECT execution_key, agent_id, state, provider, model,
                       prompt_sha256, evidence_sha256, upstream_json,
                       raw_checksum, accepted_checksum, raw_response_json,
                       accepted_envelope_json, usage_json, latency_ms,
                       error, recovery_choice, metadata_json, created_at, updated_at
                FROM execution_receipts
                WHERE state = ?
                ORDER BY created_at ASC
                """,
                (state_val,),
            )
        else:
            cursor = self._conn.execute(
                """
                SELECT execution_key, agent_id, state, provider, model,
                       prompt_sha256, evidence_sha256, upstream_json,
                       raw_checksum, accepted_checksum, raw_response_json,
                       accepted_envelope_json, usage_json, latency_ms,
                       error, recovery_choice, metadata_json, created_at, updated_at
                FROM execution_receipts
                ORDER BY created_at ASC
                """
            )
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def is_reusable(self, execution_key: str) -> bool:
        """
        Return True if execution is validated and safe to reuse without calling provider.

        Unvalidated states ('pending', 'running', 'returned', 'failed', 'unknown_completion')
        return False.
        """
        receipt = self.get(execution_key)
        if not receipt:
            return False
        return receipt.is_safely_reusable()

    def get_reusable_envelope(self, execution_key: str) -> Optional[Dict[str, Any]]:
        """Return accepted envelope if execution is safely reusable, else None."""
        receipt = self.get(execution_key)
        if receipt and receipt.is_safely_reusable():
            return receipt.accepted_envelope
        return None

    def reusable_nodes(
        self,
        candidate_keys: Mapping[str, str],
    ) -> Dict[str, ExecutionReceipt]:
        """
        Given a mapping of {agent_id: execution_key}, returns {agent_id: receipt}
        for all nodes that are currently validated and safely reusable.
        """
        reusable = {}
        for agent_id, key in candidate_keys.items():
            receipt = self.get(key)
            if receipt and receipt.is_safely_reusable():
                reusable[agent_id] = receipt
        return reusable

    def reconcile_unvalidated_on_resume(
        self,
        agent_ids: Optional[Iterable[str]] = None,
    ) -> List[ExecutionReceipt]:
        """
        Reconcile unvalidated executions during a resume operation.

        Any execution found in `running` or `returned` state cannot be verified
        as safely validated. They are transitioned to `unknown_completion`
        so an operator can choose whether to `retry` (acknowledging potential double-billing)
        or `abandon`.
        """
        now = _now_iso()
        targets = set(agent_ids) if agent_ids is not None else None
        reconciled = []

        cursor = self._conn.execute(
            """
            SELECT execution_key, agent_id, state
            FROM execution_receipts
            WHERE state IN ('running', 'returned')
            """
        )
        rows = cursor.fetchall()
        if targets is not None:
            rows = [r for r in rows if r["agent_id"] in targets]
        if not rows:
            return []

        with self._conn:
            for row in rows:
                key = row["execution_key"]
                current = self._get_row_for_transition(key)
                self._check_transition("reconcile_unvalidated_on_resume", current)
                cursor = self._conn.execute(
                    """
                    UPDATE execution_receipts SET
                        state = ?,
                        error = COALESCE(error, 'Unvalidated completion detected on resume'),
                        updated_at = ?
                    WHERE execution_key = ? AND state = ?
                    """,
                    (
                        ExecutionState.UNKNOWN_COMPLETION.value,
                        now,
                        key,
                        current["state"],
                    ),
                )
                if cursor.rowcount == 0:
                    raise ValueError(
                        f"Illegal state transition on reconcile_unvalidated_on_resume for key {key}"
                    )
                receipt = self.get(key)
                if receipt:
                    reconciled.append(receipt)
        return reconciled

    def invalidate_descendants(
        self,
        graph: Mapping[str, Any],
        changed_agents: Iterable[str],
    ) -> Set[str]:
        """
        Selectively invalidate affected nodes and their genuine downstream descendants.

        Uses `true_descendants` from execution identity to compute the transitive
        closure of affected agents. Marks their receipts in the database as failed
        or abandoned.
        """
        affected = true_descendants(graph, changed_agents)
        if not affected:
            return set()

        now = _now_iso()
        with self._conn:
            placeholders = ",".join("?" for _ in affected)
            self._conn.execute(
                f"""
                UPDATE execution_receipts SET
                    state = ?,
                    error = 'Invalidated due to upstream dependency change',
                    updated_at = ?
                WHERE agent_id IN ({placeholders}) AND state = ?
                """,
                [
                    ExecutionState.FAILED.value,
                    now,
                    *list(affected),
                    ExecutionState.VALIDATED.value,
                ],
            )
        return affected

    def _row_to_receipt(self, row: sqlite3.Row) -> ExecutionReceipt:
        upstream_raw = json.loads(row["upstream_json"]) if row["upstream_json"] else []
        upstream = tuple(
            UpstreamRef(
                agent=entry["agent"],
                content_checksum=entry["content_checksum"],
            )
            for entry in upstream_raw
        )
        return ExecutionReceipt(
            schema_version="execution_receipt_v1",
            execution_key=row["execution_key"],
            agent_id=row["agent_id"],
            state=row["state"],
            provider=row["provider"],
            model=row["model"],
            prompt_sha256=row["prompt_sha256"],
            evidence_sha256=row["evidence_sha256"],
            upstream=upstream,
            raw_checksum=row["raw_checksum"],
            accepted_checksum=row["accepted_checksum"],
            raw_response=json.loads(row["raw_response_json"]) if row["raw_response_json"] else None,
            accepted_envelope=json.loads(row["accepted_envelope_json"]) if row["accepted_envelope_json"] else None,
            usage=json.loads(row["usage_json"]) if row["usage_json"] else None,
            latency_ms=row["latency_ms"],
            error=row["error"],
            recovery_choice=row["recovery_choice"],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = [
    "ExecutionReceipt",
    "ExecutionState",
    "ExecutionStore",
    "RecoveryChoice",
    "compute_raw_checksum",
    "validate_receipt",
]
