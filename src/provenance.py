"""
Provenance logging with hash chaining.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.checksum import checksum_chain, checksum_string
from src.validators import ValidationResult


@dataclass
class ProvenanceEntry:
    timestamp: str
    actor: str
    message: str
    checksum: str

    def format(self) -> str:
        return f"{self.timestamp} | {self.actor:<10} | {self.message} | {self.checksum}"


@dataclass
class ProvenanceLog:
    path: Path
    entries: List[ProvenanceEntry] = field(default_factory=list)
    tail_checksum: Optional[str] = None
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def append(self, actor: str, message: str) -> ProvenanceEntry:
        with self._lock:
            timestamp = (
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            payload = f"{timestamp}|{actor}|{message}|{self.tail_checksum or ''}"
            checksum = checksum_string(payload)
            entry = ProvenanceEntry(
                timestamp=timestamp, actor=actor, message=message, checksum=checksum
            )
            self.entries.append(entry)
            self.tail_checksum = self._recompute_tail()
            return entry

    def _recompute_tail(self) -> str:
        chain = checksum_chain(entry.checksum for entry in self.entries)
        return chain

    def flush(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as handle:
                for entry in self.entries:
                    handle.write(entry.format() + "\n")

    def load_existing(self) -> None:
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 4:
                continue
            timestamp = parts[0]
            actor = parts[1]
            checksum = parts[-1]
            message = " | ".join(parts[2:-1]).strip()
            self.entries.append(
                ProvenanceEntry(
                    timestamp=timestamp, actor=actor, message=message, checksum=checksum
                )
            )
        if self.entries:
            self.tail_checksum = self._recompute_tail()


def verify_chain(path: Path) -> ValidationResult:
    """
    Re-verify a persisted provenance log against its hash chain.

    Recomputes each entry checksum from the raw line (timestamp, actor, message,
    previous chain tail) and compares it with the stored value. Any edit to a
    line invalidates that entry and every chain tail after it.
    """
    if not path.exists():
        return ValidationResult(
            valid=False, errors=[f"provenance log not found: {path}"]
        )

    errors: List[str] = []
    chain_tail = ""
    seen: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return ValidationResult(
            valid=False, errors=[f"provenance log unreadable: {exc}"]
        )
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if "|" not in line:
            errors.append(f"line {line_number}: malformed entry")
            continue
        head, stored_checksum = line.rsplit("|", 1)
        stored_checksum = stored_checksum.strip()
        parts = head.split("|", 2)
        if len(parts) < 3:
            errors.append(f"line {line_number}: malformed entry")
            continue
        timestamp = parts[0].strip()
        actor = parts[1].strip()
        message = parts[2].strip()
        payload = f"{timestamp}|{actor}|{message}|{chain_tail}"
        expected = checksum_string(payload)
        if expected != stored_checksum:
            errors.append(
                f"line {line_number}: checksum mismatch (log was modified or corrupted)"
            )
        seen.append(stored_checksum)
        chain_tail = checksum_chain(seen)
    if not seen:
        errors.append("provenance log has no entries")
    return ValidationResult(valid=not errors, errors=errors)
