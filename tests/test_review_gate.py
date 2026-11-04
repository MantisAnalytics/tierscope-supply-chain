"""The review gate on small fact sets: one test per branch of design D2, and the conflict rules."""

import pytest

from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.rows import to_json
from scgraph.graph.store import GraphStore

SPEC = "gen/w__suppliers.json"


def cell(row, column="F", file="w.xlsx", sheet="Suppliers"):
    return SourceLocation(file=file, sheet=sheet, row=row, column=column)


def origin(column):
    return {"spec": SPEC, "region": "suppliers", "column": column}


def entity(sid, row=2, confidence=1.0):
    return Fact("entity", "Supplier", (sid,), cell(row, "A"), confidence=confidence, origin=origin(None))


def value(sid, name, v, row=2, confidence=1.0, column="Country", file="w.xlsx", **kw):
    return Fact("field", "Supplier", (sid,), cell(row, "F", file), field=name, value=v, confidence=confidence,
                origin=kw.pop("origin", origin(column)), **kw)


def doc(quote, line=3):
    return SourceLocation(file="note.txt", lines=(line, line), quote=quote)


@pytest.fixture
def store():
    s = GraphStore(":memory:")
    yield s
    s.close()


def visible(store, sid, name):
    return store.active_fields().get(("node", "Supplier", sid), {}).get(name)


def test_new_confident_facts_apply(store):
    report = store.load([entity("S1"), value("S1", "country", "DE", confidence=0.95)])
    assert (report.applied, report.proposed, report.proposals_created) == (2, 0, [])
    assert visible(store, "S1", "country") == "DE"


def test_low_confidence_column_is_one_mapping_proposal(store):
    facts = [entity(f"S{i}", row=i) for i in range(2, 6)]
    facts += [value(f"S{i}", "photo_url", f"http://p/{i}", row=i, confidence=0.7, column="Photo") for i in range(2, 6)]
    report = store.load(facts)
    assert report.applied == 4 and report.proposed == 4
    [proposal] = store.proposals()
    assert proposal["kind"] == "mapping" and proposal["group_key"] == ["spec", SPEC, "suppliers", "Photo"]
    assert proposal["facts"] == 4 and "Supplier.photo_url" in proposal["summary"]
    assert all(visible(store, f"S{i}", "photo_url") is None for i in range(2, 6))


def test_next_load_appends_to_the_pending_proposal(store):
    store.load([entity("S1"), value("S1", "photo_url", "a", confidence=0.7, column="Photo")])
    report = store.load([entity("S2", row=3), value("S2", "photo_url", "b", row=3, confidence=0.7, column="Photo")])
    [proposal] = store.proposals()
    assert report.proposals_created == [] and report.proposals_updated == [proposal["id"]]
    assert proposal["facts"] == 2
    again = store.load([value("S2", "photo_url", "b", row=3, confidence=0.7, column="Photo")])
    assert (again.proposed, again.unchanged, again.proposals_updated) == (0, 1, [])


def test_agreeing_fact_adds_provenance_whatever_its_confidence(store):
    store.load([entity("S1"), value("S1", "country", "DE")])
    report = store.load([value("S1", "country", "DE", file="other.csv", confidence=0.3)])
    assert report.applied == 1 and report.proposed == 0
    values = store.field_values("Supplier", "S1")["country"]
    assert [(v.review, v.active) for v in values] == [("accepted", True), ("accepted", False)]


def test_existing_entity_gets_another_source(store):
    store.load([entity("S1")])
    report = store.load([Fact("entity", "Supplier", ("S1",), doc("S1 exists"), confidence=0.2, method="llm")])
    assert report.applied == 1 and store.proposals() == []
    assert len(store.node_sources("Supplier", "S1")) == 2


def test_new_source_disagreeing_with_accepted_value_waits(store):
    store.load([entity("S1"), value("S1", "country", "DE", confidence=0.9)])
    report = store.load([value("S1", "country", "AT", file="b.csv", confidence=1.0,
                               origin={"spec": "gen/b.json", "region": "r", "column": "Land"})])
    assert report.proposed == 1 and report.flagged == [("Supplier", "S1", "country")]
    assert visible(store, "S1", "country") == "DE"
    [proposal] = store.proposals()
    assert proposal["kind"] == "conflict" and proposal["group_key"] == ["spec", "gen/b.json", "r", "Land"]
    values = store.field_values("Supplier", "S1")["country"]
    assert [(v.value, v.status) for v in values] == [("DE", "flagged"), ("AT", "proposed")]


def test_changed_column_is_one_conflict_proposal(store):
    rows = range(2, 7)
    store.load([entity(f"S{i}", row=i) for i in rows] + [value(f"S{i}", "tier", 1, row=i) for i in rows])
    changed = [value(f"S{i}", "tier", 2, row=i, file="w2.xlsx") for i in rows if i % 2 == 0]
    unchanged = [value(f"S{i}", "tier", 1, row=i, file="w2.xlsx") for i in rows if i % 2]
    report = store.load(changed + unchanged)
    [proposal] = store.proposals()
    assert proposal["kind"] == "conflict" and proposal["facts"] == len(changed) == report.proposed
    assert report.applied == len(unchanged)
    assert all(visible(store, f"S{i}", "tier") == 1 for i in rows)
    assert store.stats()["flagged_fields"] == len(changed)


def test_disagreeing_document_value_groups_by_field(store):
    store.load([entity("S1"), value("S1", "country", "DE")])
    fact = Fact("field", "Supplier", ("S1",), doc("S1 is Austrian"), field="country", value="AT", method="llm",
                confidence=0.95, origin={"document": "note.txt", "chunk": 0, "quote": "S1 is Austrian"})
    store.load([fact])
    [proposal] = store.proposals()
    assert proposal["group_key"] == ["field", "node", "Supplier", "S1", "country"]


def test_statement_proposal_holds_facts_sharing_a_quote(store):
    store.load([entity("S1"), Fact("entity", "Component", ("C1",), cell(2, "A", sheet="Components"))])
    quote = "S1 supplies C1 from Lyon."
    common = {"method": "llm", "confidence": 0.8, "origin": {"document": "note.txt", "chunk": 0, "quote": quote}}
    facts = [Fact("relation", "SUPPLIES", ("S1", "C1", None), doc(quote), **common),
             Fact("field", "Supplier", ("S1",), doc(quote), field="city", value="Lyon", **common),
             Fact("field", "Supplier", ("S1",), doc("other", 9), field="tier", value=2, method="llm",
                  confidence=0.8, origin={"document": "note.txt", "chunk": 1, "quote": "other"})]
    report = store.load(facts)
    assert report.proposed == 3 and len(report.proposals_created) == 2
    statement = next(p for p in store.proposals() if p["group_key"][2] == quote)
    assert statement["kind"] == "statement" and statement["facts"] == 2
    assert store.edges("SUPPLIES") == []


def test_unresolved_mention_is_a_resolution_proposal(store):
    store.load([Fact("entity", "Component", ("C1",), cell(2, "A", sheet="Components"))])
    quote = "C1 is supplied by Zzz Works."
    record = {"method": "unresolved", "mention": "Zzz Works", "chosen": "mention:Zzz Works", "confidence": 0.0}
    rel = {"method": "unresolved", "confidence": 0.0,
           "mentions": [record, {"method": "id", "mention": "C1", "chosen": "C1", "confidence": 1.0}]}
    common = {"method": "llm", "origin": {"document": "note.txt", "chunk": 0, "quote": quote}}
    facts = [Fact("relation", "SUPPLIES", ("mention:Zzz Works", "C1", None), doc(quote), confidence=0.9,
                  resolution=rel, **common),
             Fact("entity", "Supplier", ("mention:Zzz Works",), doc(quote), confidence=0.0, resolution=record,
                  **common)]
    store.load(facts)
    [proposal] = store.proposals()
    assert proposal["kind"] == "resolution" and proposal["facts"] == 2
    assert proposal["group_key"] == ["mention", "note.txt", "Zzz Works", "Supplier"]
    assert not store.has_node("Supplier", "mention:Zzz Works") and store.stats()["unresolved_mentions"] == 0


def test_uncertain_model_match_is_a_resolution_proposal(store):
    store.load([entity("S1"), Fact("entity", "Component", ("C1",), cell(2, "A", sheet="Components"))])
    record = {"method": "model", "mention": "Acme", "chosen": "S1", "confidence": 0.7, "candidates": ["S1"]}
    fact = Fact("field", "Supplier", ("S1",), doc("Acme is in Lyon"), field="city", value="Lyon", method="llm",
                confidence=0.7, resolution=record)
    store.load([fact])
    [proposal] = store.proposals()
    assert proposal["kind"] == "resolution"
    named = Fact("field", "Supplier", ("S1",), doc("Acme Inc is in Lyon", 4), field="city", value="Lyon",
                 method="llm", confidence=0.95, resolution={**record, "method": "name", "confidence": 0.95})
    assert store.load([named]).applied == 1


def test_alias_rekeys_facts(store):
    store.load([entity("S1"), entity("S2", row=3)])
    with store.operation("ana", "edit"):
        store.insert("aliases", {"type": "Supplier", "from_id": "S1", "to_id": "S2"})
    store.load([value("S1", "country", "DE")])
    assert visible(store, "S2", "country") == "DE" and not store.has_node("Supplier", "S1")


def test_deleted_item_same_source_dropped_other_source_restore(store):
    store.load([entity("S1"), value("S1", "country", "DE")])
    sources = [to_json(cell(2, "A").to_dict()), to_json(cell(2, "F").to_dict())]
    with store.operation("ana", "edit"):
        store.insert("deletions", {"item_kind": "node", "type": "Supplier", "key": "S1", "author": "ana",
                                   "ts": "t", "sources_json": to_json(sources)})
    report = store.load([entity("S1"), value("S1", "country", "DE")])
    assert store.proposals() == [] and report.unchanged == 2
    report = store.load([value("S1", "country", "DE", row=9, file="new.xlsx")])
    [proposal] = store.proposals()
    assert proposal["kind"] == "restore" and proposal["group_key"] == ["deleted", "node", "Supplier", "S1", None]
    assert not store.has_node("Supplier", "S1")


def test_gate_is_deterministic():
    facts = [entity("S1"), value("S1", "country", "DE", confidence=0.5), value("S1", "tier", 2, confidence=0.95),
             value("S1", "country", "AT", row=3, confidence=0.6)]
    results = []
    for _ in range(2):
        s = GraphStore(":memory:")
        s.load(facts, run_id="r")
        results.append(([(p["kind"], p["group_key"], p["facts"]) for p in s.proposals()], s.active_fields()))
    assert results[0] == results[1]
