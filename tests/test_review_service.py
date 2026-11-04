"""Accepting and rejecting proposals; remembered rejections."""


import pytest

from scgraph.exposure.compute import Exposure
from scgraph.graph.facts import Fact, Parameter, SourceLocation
from scgraph.graph.store import GraphStore
from scgraph.review.service import DecidedError, ReviewError, accept, describe, reject

SPEC = "gen/w__weights.json"


def cell(row, column="B", file="w.xlsx", sheet="Weights"):
    return SourceLocation(file=file, sheet=sheet, row=row, column=column)


def graph_facts(revenue_confidence=0.8):
    """Two assemblies with roots R1, R2; C in A1 supplied by S1; revenue from a column below the threshold."""
    keys = {"spec": SPEC, "region": "assemblies", "column": None}
    rev = {"spec": SPEC, "region": "assemblies", "column": "Weight"}
    facts = []
    for i, (assembly, root, revenue) in enumerate([("A1", "R1", 300.0), ("A2", "R2", 100.0)]):
        facts += [Fact("entity", "Assembly", (assembly,), cell(2 + i, "A"), origin=keys),
                  Fact("field", "Assembly", (assembly,), cell(2 + i), field="revenue_eur", value=revenue,
                       confidence=revenue_confidence, origin=rev),
                  Fact("entity", "Component", (root,), cell(2 + i, "A", sheet="Components")),
                  Fact("field", "Component", (root,), cell(2 + i, "C", sheet="Components"), field="part_number",
                       value=assembly)]
    facts += [Fact("entity", "Component", ("C",), cell(5, "A", sheet="Components")),
              Fact("relation", "CONTAINS", ("R1", "C", "A1"), cell(2, "A", sheet="BoM")),
              Fact("entity", "Supplier", ("S1",), cell(2, "A", sheet="Suppliers")),
              Fact("field", "Supplier", ("S1",), cell(2, "D", sheet="Suppliers"), field="country", value="DE"),
              Fact("relation", "SUPPLIES", ("S1", "C", None), cell(5, "D", sheet="Components"))]
    return facts


@pytest.fixture
def store():
    s = GraphStore(":memory:")
    s.load(graph_facts(), parameters=[Parameter("margin", 0.2, cell(30))])
    yield s
    s.close()


def test_accept_mapping_proposal(store):
    [proposal] = store.proposals(status="pending")
    assert proposal["kind"] == "mapping" and proposal["facts"] == 2
    with pytest.raises(Exception, match="total assembly revenue is zero"):
        Exposure.from_store(store).business_exposure("Supplier", "S1")
    decision = accept(store, proposal["id"], "ana", comment="checked the weights sheet")
    exposure = Exposure.from_store(store)
    assert exposure.revenue == {"A1": 300.0, "A2": 100.0}
    assert exposure.business_exposure("Supplier", "S1") == 75.0
    [value] = store.field_values("Assembly", "A1")["revenue_eur"]
    assert (value.review, value.locked, value.active) == ("accepted", True, True)
    [entry] = store.journal(limit=1)
    assert entry["id"] == decision.journal_id and entry["actor"] == "ana" and entry["action"] == "accept"
    assert "checked the weights sheet" in entry["summary"] and entry["ts"]
    decided = store.proposal(proposal["id"])
    assert (decided["status"], decided["decided_by"]) == ("accepted", "ana") and decided["decided_at"]
    assert decision.warnings == [f"spec {SPEC} not found; the decision applies to the graph only"]


def test_already_decided_proposal_is_refused(store):
    [proposal] = store.proposals()
    reject(store, proposal["id"], "ana")
    for action in (accept, reject):
        with pytest.raises(DecidedError, match="already rejected"):
            action(store, proposal["id"], "bo")
    with pytest.raises(ReviewError, match="no proposal #99"):
        accept(store, 99, "bo")


def conflicting(store, value="AT", row=7, file="new.csv"):
    report = store.load([Fact("field", "Supplier", ("S1",), cell(row, "D", file, "Suppliers"), field="country",
                              value=value)])
    return report.proposals_created[0] if report.proposals_created else None


def test_reject_conflict_keeps_the_graph(store):
    pid = conflicting(store)
    assert store.stats()["flagged_fields"] == 1
    reject(store, pid, "ana", comment="still German")
    values = store.field_values("Supplier", "S1")["country"]
    assert [(v.value, v.active, v.status) for v in values] == [("DE", True, "active"), ("AT", False, "rejected")]
    assert store.stats()["flagged_fields"] == 0


def test_accept_conflict_supersedes_the_old_value(store):
    pid = conflicting(store)
    view = describe(store, pid)
    assert view["changes"][0]["current"] == "DE" and view["changes"][0]["proposed"] == "AT"
    accept(store, pid, "ana")
    values = store.field_values("Supplier", "S1")["country"]
    assert [(v.value, v.active, v.status, v.locked) for v in values] == [("AT", True, "active", True),
                                                                         ("DE", False, "superseded", False)]
    # The superseded table value does not come back as a proposal from the same cell.
    report = store.load([Fact("field", "Supplier", ("S1",), cell(2, "D", sheet="Suppliers"), field="country",
                              value="DE")])
    assert report.dropped == 1 and report.proposals_created == []


def test_reingest_after_rejection_creates_no_proposal(store):
    pid = conflicting(store)
    reject(store, pid, "ana")
    assert conflicting(store) is None
    assert store.proposals(status="pending", kind="conflict") == []


def test_rejected_value_from_new_evidence_is_proposed_with_a_note(store):
    pid = conflicting(store)
    reject(store, pid, "ana")
    new = conflicting(store, file="letter.csv", row=3)
    assert new is not None and new != pid
    [note] = store.proposal(new)["notes"]
    assert "'AT' was rejected before" in note and f"proposal #{pid}" in note


def test_rejected_entity_is_remembered_without_its_row(store):
    """The fingerprint alone keeps a rejected fact out, e.g. after the rows were rebuilt."""
    report = store.load([Fact("entity", "Supplier", ("S9",), cell(9, "A", sheet="Suppliers"), confidence=0.5)])
    reject(store, report.proposals_created[0], "ana")
    store._db.execute("DELETE FROM node_sources WHERE id = 'S9'")
    again = store.load([Fact("entity", "Supplier", ("S9",), cell(9, "A", sheet="Suppliers"), confidence=0.5)])
    assert again.dropped == 1 and again.proposals_created == []


DOC = "note.txt"


def mention_facts(quote="Plant DM Auto Oranienburg ships C.", line=3, doc=DOC):
    record = {"method": "unresolved", "mention": "DM Auto Oranienburg", "chosen": "mention:DM Auto Oranienburg",
              "confidence": 0.0}
    rel = {"method": "unresolved", "confidence": 0.0,
           "mentions": [{"method": "id", "mention": "C", "chosen": "C", "confidence": 1.0}, record]}
    src = SourceLocation(file=doc, lines=(line, line), quote=quote)
    common = {"method": "llm", "origin": {"document": doc, "chunk": 0, "quote": quote}}
    return [Fact("relation", "DELIVERED_TO", ("C", "mention:DM Auto Oranienburg"), src, confidence=0.9,
                 resolution=rel, **common),
            Fact("entity", "Site", ("mention:DM Auto Oranienburg",), src, confidence=0.0, resolution=record,
                 **common),
            Fact("field", "Site", ("mention:DM Auto Oranienburg",), src, field="name", value="DM Auto Oranienburg",
                 confidence=0.0, resolution=record, **common)]


def test_accept_resolution_by_choosing_a_node(store):
    store.load([Fact("entity", "Site", ("P07",), cell(8, "A", sheet="Sites")),
                Fact("field", "Site", ("P07",), cell(8, "B", sheet="Sites"), field="name", value="Oranienburg")])
    report = store.load(mention_facts())
    [pid] = report.proposals_created
    view = describe(store, pid)
    assert view["kind"] == "resolution" and view["mention"] == "DM Auto Oranienburg" and view["count"] == 3
    with pytest.raises(ReviewError, match="choosing a node"):
        accept(store, pid, "ana")
    decision = accept(store, pid, "ana", target_id="P07")
    assert decision.report.applied == 2 and decision.report.proposed == 1  # the mention's name disagrees
    assert [(e.src_id, e.dst_id) for e in store.edges("DELIVERED_TO")] == [("C", "P07")]
    assert not any(i.startswith("mention:") for _, i, _ in store.nodes())
    # A later mention with the same text resolves to the chosen site without review.
    again = store.load(mention_facts("DM Auto Oranienburg also ships C.", line=9))
    assert again.proposals_created == [] and again.applied >= 1
    sources = [s for s in store.edge_sources(store.edges("DELIVERED_TO")[0].id)]
    assert len(sources) == 2 and sources[1]["source"].lines == (9, 9)
    # ... and so does the same text in another document.
    other = store.load(mention_facts("DM Auto Oranienburg ships C weekly.", line=2, doc="other.txt"))
    assert other.proposals_created == [] and len(store.edge_sources(store.edges("DELIVERED_TO")[0].id)) == 3


def test_accept_resolution_with_a_new_node(store):
    [pid] = store.load(mention_facts()).proposals_created
    with pytest.raises(ReviewError, match="no Site 'P98'"):
        accept(store, pid, "ana", target_id="P98")
    accept(store, pid, "ana", new_node="P99")
    assert store.has_node("Site", "P99")
    assert store.active_fields()[("node", "Site", "P99")] == {"name": "DM Auto Oranienburg"}
    assert [(e.src_id, e.dst_id) for e in store.edges("DELIVERED_TO")] == [("C", "P99")]
