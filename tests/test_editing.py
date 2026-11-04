"""Human edits, evidence, deletions and merges; journal and undo."""

import json

import pytest

from scgraph.editing.evidence import EvidenceError, EvidenceStore
from scgraph.editing.journal import undo
from scgraph.editing.service import apply_edits
from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.store import GraphStore
from scgraph.review.service import accept


def test_evidence_limit(tmp_path, monkeypatch):
    import scgraph.editing.evidence as ev

    monkeypatch.setattr(ev, "MAX_BYTES", 10)
    with pytest.raises(EvidenceError, match="limit"):
        EvidenceStore(tmp_path).add("big.pdf", b"x" * 11)


def test_journal_after_ingest_and_review(tmp_path):
    store = GraphStore(":memory:")
    origin = {"spec": "x.json", "region": "r", "column": "Name"}
    store.load([Fact("entity", "Supplier", ("S1",), SourceLocation("a.xlsx", "S", 2, "A")),
                Fact("field", "Supplier", ("S1",), SourceLocation("a.xlsx", "S", 2, "B"), field="name", value="Acme",
                     confidence=0.5, origin=origin)], inputs=["a.xlsx"])
    accept(store, store.proposals()[0]["id"], "ana")
    apply_edits(store, [{"op": "set_field", "type": "Supplier", "key": ["S1"], "field": "tier", "value": 2}], "ana")
    entries = list(reversed(store.journal()))
    assert [(e["action"], e["actor"]) for e in entries] == [("ingest", "system"), ("accept", "ana"), ("edit", "ana")]
    assert "1 facts applied, 1 proposed" in entries[0]["summary"]


def test_undone_accept_returns_proposal_to_pending(tmp_path):
    store = GraphStore(":memory:")
    origin = {"spec": "x.json", "region": "r", "column": "Name"}
    store.load([Fact("entity", "Supplier", ("S1",), SourceLocation("a.xlsx", "S", 2, "A")),
                Fact("field", "Supplier", ("S1",), SourceLocation("a.xlsx", "S", 2, "B"), field="name", value="Acme",
                     confidence=0.5, origin=origin)])
    [p] = store.proposals()
    decision = accept(store, p["id"], "ana")
    assert store.active_fields()[("node", "Supplier", "S1")] == {"name": "Acme"}
    undo(store, decision.journal_id, "ana")
    assert store.proposal(p["id"])["status"] == "pending" and store.active_fields() == {}
    assert json.loads(json.dumps(store.journal()[0]))["action"] == "undo"
