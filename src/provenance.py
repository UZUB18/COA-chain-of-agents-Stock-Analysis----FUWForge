"""
Provenance logging with hash chaining.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.checksum import checksum_chain, checksum_string


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

    def append(self, actor: str, message: str) -> ProvenanceEntry:
        timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        payload = f"{timestamp}|{actor}|{message}|{self.tail_checksum or ''}"
        checksum = checksum_string(payload)
        entry = ProvenanceEntry(timestamp=timestamp, actor=actor, message=message, checksum=checksum)
        self.entries.append(entry)
        self.tail_checksum = self._recompute_tail()
        return entry

    def _recompute_tail(self) -> str:
        chain = checksum_chain(entry.checksum for entry in self.entries)
        return chain

    def flush(self) -> None:
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
            if len(parts) != 4:
                continue
            timestamp, actor, message, checksum = parts
            self.entries.append(
                ProvenanceEntry(timestamp=timestamp, actor=actor, message=message, checksum=checksum)
            )
        if self.entries:
            self.tail_checksum = self._recompute_tail()
