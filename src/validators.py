"""
Validation helpers for agent envelopes and orchestrator gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.envelope import Envelope

try:
    from jsonschema import Draft7Validator  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Draft7Validator = None  # type: ignore


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str]
    warnings: List[str] = field(default_factory=list)
    envelope_checksum_errors: List[str] = field(default_factory=list)
    #: Informational warnings that strict mode must continue to report without
    #: treating them as defects.
    strict_exempt_warnings: List[str] = field(default_factory=list)


class EnvelopeValidator:
    """
    JSON schema validator with graceful degradation when jsonschema is unavailable.
    """

    def __init__(self, schema_path: Path):
        self.schema_path = schema_path
        self.schema = self._load_schema(schema_path)
        self.validator = None
        if Draft7Validator is not None:
            self.validator = Draft7Validator(self.schema)

    def _load_schema(self, schema_path: Path) -> Dict[str, Any]:
        import json

        return json.loads(schema_path.read_text(encoding="utf-8"))

    def validate(self, data: Dict[str, Any]) -> ValidationResult:
        if self.validator is not None:
            errors = sorted(self.validator.iter_errors(data), key=lambda e: e.path)
            if errors:
                rendered = [
                    f"{'/'.join(map(str, err.path))}: {err.message}" for err in errors
                ]
                return ValidationResult(valid=False, errors=rendered)
            return ValidationResult(valid=True, errors=[])

        # Fallback: minimal structural checks
        required_keys = [
            "schema_version",
            "agent",
            "gates",
            "sections_md",
            "artifacts",
            "sources",
            "metrics",
        ]
        missing = [key for key in required_keys if key not in data]
        if missing:
            return ValidationResult(
                valid=False, errors=[f"Missing keys: {', '.join(missing)}"]
            )
        return ValidationResult(valid=True, errors=[])


def validate_required_artifacts(
    envelope: Envelope, required: Iterable[str]
) -> ValidationResult:
    missing = [key for key in required if key not in envelope.artifacts]
    if missing:
        return ValidationResult(
            valid=False,
            errors=[f"Missing required artifacts: {', '.join(missing)}"],
        )
    return ValidationResult(valid=True, errors=[])


def validate_checksum(envelope: Envelope, expected_checksum: str) -> ValidationResult:
    actual = envelope.metrics.get("data_checksum")
    if actual != expected_checksum:
        return ValidationResult(
            valid=False,
            errors=[f"Checksum mismatch: expected {expected_checksum}, found {actual}"],
        )
    return ValidationResult(valid=True, errors=[])


def validate_gate_passed(envelope: Envelope) -> ValidationResult:
    """Hard gate check; a failed gate in any agent halts the run."""
    passed = envelope.gates.get("passed")
    if not passed:
        notes = envelope.gates.get("notes", [])
        note_text = "; ".join(notes) if notes else "No notes provided."
        agent_id = envelope.agent.id or "AGENT"
        return ValidationResult(
            valid=False, errors=[f"{agent_id} gate failed: {note_text}"]
        )
    return ValidationResult(valid=True, errors=[])
