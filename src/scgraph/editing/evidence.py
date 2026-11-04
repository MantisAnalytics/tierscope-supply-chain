"""Evidence files attached to edits, stored content-addressed: `<dir>/<sha256>/<original name>`."""

from __future__ import annotations

import hashlib
from pathlib import Path

MAX_BYTES = 25 * 1024 * 1024


class EvidenceError(ValueError):
    pass


class EvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def add(self, name: str, data: bytes) -> str:
        """Store a file and return its id (the content hash); the same bytes give the same id."""
        if len(data) > MAX_BYTES:
            raise EvidenceError(f"{name}: {len(data)} bytes, more than the {MAX_BYTES // 2**20} MB limit")
        name = Path(name).name or "evidence"
        evidence_id = hashlib.sha256(data).hexdigest()
        folder = self.root / evidence_id
        if self.path(evidence_id) is None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
        return evidence_id

    def add_file(self, path: str | Path) -> str:
        path = Path(path)
        if not path.is_file():
            raise EvidenceError(f"no file at {path}")
        if path.stat().st_size > MAX_BYTES:
            raise EvidenceError(f"{path.name}: more than the {MAX_BYTES // 2**20} MB limit")
        return self.add(path.name, path.read_bytes())

    def path(self, evidence_id: str) -> Path | None:
        folder = self.root / evidence_id
        if len(evidence_id) != 64 or not all(c in "0123456789abcdef" for c in evidence_id) or not folder.is_dir():
            return None
        return next((p for p in sorted(folder.iterdir()) if p.is_file()), None)

    def name(self, evidence_id: str) -> str | None:
        path = self.path(evidence_id)
        return path.name if path else None
