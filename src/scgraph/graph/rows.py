"""Row types the graph store returns (field values, edges, load reports, journaled operations) and the JSON, time
and fingerprint helpers that store, review and editing share."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.schema import SCHEMA


def to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def source_json(source: SourceLocation) -> str:
    return to_json(source.to_dict())


def source_row(source_json: str) -> str:
    """A source location without its column: the row (or text place) a fact came from."""
    data = json.loads(source_json)
    data.pop("column", None)
    return to_json(data)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fingerprint(item: tuple, value_json: str | None, source_json: str) -> str:
    """Identity of one candidate: (item, value, source location). Rejected or superseded ones are remembered."""
    return hashlib.sha256(to_json([list(item), value_json, source_json]).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class FieldValue:
    value: Any
    confidence: float
    method: str
    status: str
    source: SourceLocation
    active: bool
    locked: bool
    run_id: str
    resolution: dict[str, Any] | None = None
    review: str = "accepted"
    proposal_id: int | None = None
    origin: dict[str, Any] | None = None
    id: int | None = None


@dataclass(frozen=True)
class Edge:
    id: int
    type: str
    key: tuple
    src_type: str
    src_id: str
    dst_type: str
    dst_id: str

    def identity(self) -> dict[str, Any]:
        rel = SCHEMA.relation(self.type)
        return {f.name: v for f, v in zip(rel.identity, self.key[2:], strict=True)}


@dataclass
class LoadReport:
    run_id: str
    loaded: int = 0
    rejected: list[tuple[Fact, str]] = field(default_factory=list)
    # (owner_type, owner_id, field) whose candidates disagree.
    flagged: list[tuple[str, str, str]] = field(default_factory=list)
    referenced_only: list[tuple[str, str]] = field(default_factory=list)
    oracle_values: int = 0
    parameters: int = 0
    applied: int = 0  # new accepted candidates
    proposed: int = 0  # new candidates waiting in a proposal
    unchanged: int = 0  # candidates the store already had
    dropped: int = 0  # rejected before, superseded by a person, or deleted by a person from the same source
    proposals_created: list[int] = field(default_factory=list)
    proposals_updated: list[int] = field(default_factory=list)
    journal_id: int | None = None

    def counts(self) -> dict[str, int]:
        return {
            "loaded": self.loaded,
            "rejected": len(self.rejected),
            "flagged": len(self.flagged),
            "referenced_only": len(self.referenced_only),
            "oracle_values": self.oracle_values,
            "parameters": self.parameters,
            "applied": self.applied,
            "proposed": self.proposed,
            "unchanged": self.unchanged,
            "dropped": self.dropped,
            "proposals_created": len(self.proposals_created),
            "proposals_updated": len(self.proposals_updated),
        }


@dataclass
class Operation:
    """Row changes of one journaled change: first state before the change per (table, rowid), files replaced."""

    actor: str
    action: str
    summary: str = ""
    track_inserts: bool = True
    before: dict[tuple[str, int], dict[str, Any] | None] = field(default_factory=dict)
    files: dict[str, str | None] = field(default_factory=dict)  # path -> text before (None: did not exist)
    extra: dict[str, Any] = field(default_factory=dict)
    journal_id: int | None = None

