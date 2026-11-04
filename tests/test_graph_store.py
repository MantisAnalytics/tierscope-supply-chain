import pytest

from scgraph.graph.facts import Fact, OracleValue, Parameter, SourceLocation
from scgraph.graph.store import GraphStore


def src(row, column="A", sheet="Suppliers", file="a.xlsx"):
    return SourceLocation(file=file, sheet=sheet, row=row, column=column)


def entity(type_name, key, row=2, **kw):
    return Fact("entity", type_name, key if isinstance(key, tuple) else (key,), src(row), **kw)


def field(type_name, key, name, value, row=2, column="F", **kw):
    key = key if isinstance(key, tuple) else (key,)
    return Fact("field", type_name, key, src(row, column, **kw.pop("src_kw", {})), field=name, value=value, **kw)


def relation(type_name, key, row=2, column="A", sheet="BoM"):
    return Fact("relation", type_name, key, src(row, column, sheet))


@pytest.fixture
def store():
    s = GraphStore(":memory:")
    yield s
    s.close()


def test_field_reads_back_with_provenance(store):
    report = store.load([entity("Supplier", "S1"), field("Supplier", "S1", "country", "Germany", confidence=0.9)],
                        inputs=["a.xlsx"])
    assert report.loaded == 2 and not report.rejected
    [value] = store.field_values("Supplier", "S1")["country"]
    assert value.value == "Germany"
    assert value.confidence == 0.9
    assert value.method == "mapping"
    assert value.status == "active" and value.active and not value.locked
    assert value.source == SourceLocation(file="a.xlsx", sheet="Suppliers", row=2, column="F")
    assert value.run_id == report.run_id
    assert store.runs()[0]["inputs"] == ["a.xlsx"]


def test_rejected_fact_does_not_stop_the_load(store):
    report = store.load([
        entity("Supplier", "S1"),
        field("Supplier", "S1", "colour", "red"),
        Fact("entity", "Supplier", ("S2",), None),
        entity("Supplier", "S3"),
    ])
    assert report.loaded == 2
    assert [e for _, e in report.rejected] == ["unknown field 'colour' on Supplier",
                                               "Supplier ('S2',): fact has no source location"]
    assert [n[1] for n in store.nodes("Supplier")] == ["S1", "S3"]


def test_conflicting_values_kept_and_flagged(store):
    report = store.load([
        entity("Supplier", "S1"),
        field("Supplier", "S1", "country", "Germany", row=2, confidence=0.7),
        field("Supplier", "S1", "country", "Austria", row=3, confidence=0.9),
    ])
    # Neither was accepted before: the higher-confidence value is active, the other waits in a conflict proposal.
    assert report.flagged == [("Supplier", "S1", "country")]
    values = store.field_values("Supplier", "S1")["country"]
    assert [(v.value, v.active, v.status) for v in values] == [("Austria", True, "flagged"),
                                                               ("Germany", False, "proposed")]
    [proposal] = store.proposals()
    assert proposal["kind"] == "conflict" and values[1].proposal_id == proposal["id"]
    assert store.stats()["flagged_fields"] == 1 and store.stats()["proposals"] == {"conflict": 1}


def test_conflicting_values_without_review_are_both_accepted():
    store = GraphStore(":memory:", review=False)
    report = store.load([
        entity("Supplier", "S1"),
        field("Supplier", "S1", "country", "Germany", row=2, confidence=0.7),
        field("Supplier", "S1", "country", "Austria", row=3, confidence=0.9),
    ])
    assert report.flagged == [("Supplier", "S1", "country")] and report.proposals_created == []
    values = store.field_values("Supplier", "S1")["country"]
    assert [(v.value, v.active, v.status) for v in values] == [("Austria", True, "flagged"),
                                                               ("Germany", False, "flagged")]


def test_same_value_from_two_sources_is_one_active_value(store):
    report = store.load([
        entity("Supplier", "S1"),
        field("Supplier", "S1", "country", "Germany", row=2),
        field("Supplier", "S1", "country", "Germany", row=9, src_kw={"file": "b.csv"}),
    ])
    assert report.flagged == []
    values = store.field_values("Supplier", "S1")["country"]
    assert len(values) == 2
    assert [v.active for v in values] == [True, False]
    assert {v.source.file for v in values} == {"a.xlsx", "b.csv"}
    assert store.active_fields()[("node", "Supplier", "S1")] == {"country": "Germany"}


def test_reload_is_idempotent(store):
    facts = [entity("Supplier", "S1"), field("Supplier", "S1", "country", "Germany")]
    store.load(facts)
    before = store.stats()
    store.load(facts)
    after = store.stats()
    assert {k: v for k, v in after.items() if k != "runs"} == {k: v for k, v in before.items() if k != "runs"}
    assert after["runs"] == 2


def bom_row(parent, child, assembly, row, quantity, level=1):
    key = (parent, child, assembly)
    return [
        relation("CONTAINS", key, row),
        Fact("field", "CONTAINS", key, src(row, "E", "BoM"), field="quantity", value=quantity),
        Fact("field", "CONTAINS", key, src(row, "G", "BoM"), field="level", value=level),
    ]


def test_repeated_bom_triple_is_one_edge_with_occurrences(store):
    store.load([
        entity("Assembly", "A1"),
        *bom_row("P", "C", "A1", 2, 1.0),
        *bom_row("P", "C", "A1", 7, 2.0, level=3),
    ])
    [edge] = store.edges("CONTAINS")
    occ = store.occurrences("CONTAINS")[edge.id]
    assert [(o["quantity"], o["level"], o["source"].row) for o in occ] == [(1.0, 1, 2), (2.0, 3, 7)]
    assert store.stats()["occurrences"] == {"CONTAINS": 2}
    assert store.stats()["flagged_fields"] == 0  # different quantities are separate occurrences, not a conflict


def test_same_pair_in_two_assemblies_is_two_edges(store):
    store.load([*bom_row("P", "C", "A1", 2, 1.0), *bom_row("P", "C", "A2", 3, 1.0)])
    assert sorted(e.identity()["assembly"] for e in store.edges("CONTAINS")) == ["A1", "A2"]


def test_undescribed_supplier_becomes_referenced_only(store):
    report = store.load([
        entity("Component", "C1"),
        entity("Site", "X1"),
        relation("SUPPLIES", ("S9", "C1", "X1"), sheet="Components"),
        relation("SUPPLIES", ("S1", "C1", None), sheet="Components"),
        entity("Supplier", "S1"),
    ])
    assert len(store.edges("SUPPLIES")) == 2
    assert report.referenced_only == [("Supplier", "S9")]
    assert ("Supplier", "S9", True) in store.nodes("Supplier")
    assert ("Supplier", "S1", False) in store.nodes("Supplier")


def test_oracle_value_kept_apart_from_fields(store):
    store.load(
        [entity("Component", "C1"), field("Component", "C1", "description", "BALL")],
        oracle=[OracleValue("Component", "C1", "business_exposure_pct", 34.1, src(2, "L", "Components"))],
        parameters=[Parameter("margin", 0.19, src(30, "B", "Weights"))],
    )
    [ov] = store.oracle_values("business_exposure_pct")
    assert (ov.entity_id, ov.value, ov.source.column) == ("C1", 34.1, "L")
    assert set(store.field_values("Component", "C1")) == {"description"}
    assert store.parameters()["margin"].value == 0.19


def test_remove_edge(store):
    store.load([entity("Supplier", "S1"), entity("Component", "C1"), relation("SUPPLIES", ("S1", "C1", None))])
    assert store.remove_edge("SUPPLIES", ("S1", "C1", None))
    assert store.edges("SUPPLIES") == []


def test_view_counts_match_store(store):
    from scgraph.graph.view import build_view

    store.load([
        entity("Assembly", "DMP1"),
        entity("Component", "R"), field("Component", "R", "part_number", "DMP1"),
        entity("Component", "C"),
        entity("Supplier", "S1"),
        *bom_row("R", "C", "DMP1", 2, 1.0),
        *bom_row("R", "C", "DMP1", 3, 1.0),
        relation("SUPPLIES", ("S1", "C", None)),
    ])
    view = build_view(store)
    assert view.number_of_nodes() == len(store.nodes())
    assert view.number_of_edges() == len(store.edges())
    assert view.nodes[("Component", "R")]["fields"] == {"part_number": "DMP1"}
    assert view.nodes[("Assembly", "DMP1")]["root"] == "R"
    [(_, _, data)] = [e for e in view.edges(data=True) if e[2]["type"] == "CONTAINS"]
    assert len(data["occurrences"]) == 2 and data["identity"] == {"assembly": "DMP1"}


def test_fact_origin_and_evidence_round_trip():
    source = SourceLocation(file="letter.pdf", page=2, quote="we supply DMC-1", url="https://x.example/a",
                            note="confirmed by buyer", author="ana", evidence_id="ab" * 32)
    fact = Fact("relation", "SUPPLIES", ("S1", "C1", None), source, method="human",
                origin={"edit": 7})
    again = Fact.from_dict(fact.to_dict())
    assert again == fact and again.origin == {"edit": 7}
    assert SourceLocation.from_dict(source.to_dict()) == source
    label = source.label()
    assert "https://x.example/a" in label and "(confirmed by buyer)" in label and "by ana" in label
    assert "evidence abababababab" in label
    assert "origin" not in Fact("entity", "Supplier", ("S1",), src(2)).to_dict()


# The schema before review state existed (graph-core plus document-ingest's resolution columns).
OLD_SCHEMA = """
CREATE TABLE runs (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, inputs_json TEXT NOT NULL, counts_json TEXT NOT NULL);
CREATE TABLE nodes (type TEXT NOT NULL, id TEXT NOT NULL, referenced_only INTEGER NOT NULL, PRIMARY KEY (type, id));
CREATE TABLE node_sources (type TEXT NOT NULL, id TEXT NOT NULL, source_json TEXT NOT NULL, method TEXT NOT NULL,
    confidence REAL NOT NULL, run_id TEXT NOT NULL, resolution_json TEXT, PRIMARY KEY (type, id, source_json));
CREATE TABLE edges (id INTEGER PRIMARY KEY, type TEXT NOT NULL, key_json TEXT NOT NULL, src_type TEXT NOT NULL,
    src_id TEXT NOT NULL, dst_type TEXT NOT NULL, dst_id TEXT NOT NULL, UNIQUE (type, key_json));
CREATE TABLE edge_sources (edge_id INTEGER NOT NULL, source_json TEXT NOT NULL, method TEXT NOT NULL,
    confidence REAL NOT NULL, run_id TEXT NOT NULL, resolution_json TEXT, PRIMARY KEY (edge_id, source_json));
CREATE TABLE field_values (id INTEGER PRIMARY KEY, owner_kind TEXT NOT NULL, owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL, field TEXT NOT NULL, value_json TEXT NOT NULL, confidence REAL NOT NULL,
    method TEXT NOT NULL, source_json TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    locked INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 0, resolution_json TEXT,
    UNIQUE (owner_kind, owner_type, owner_key, field, value_json, source_json));
CREATE TABLE oracle_values (entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, metric TEXT NOT NULL,
    value REAL NOT NULL, source_json TEXT NOT NULL, anomaly TEXT, PRIMARY KEY (entity_type, entity_id, metric));
CREATE TABLE parameters (name TEXT PRIMARY KEY, value_json TEXT NOT NULL, source_json TEXT NOT NULL);
"""


def test_database_from_previous_version_opens_with_rows_accepted(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    src_json = '{"column":"A","file":"a.xlsx","row":2,"sheet":"Suppliers"}'
    db.execute("INSERT INTO nodes VALUES ('Supplier', 'S1', 0), ('Component', 'C1', 0)")
    db.execute("INSERT INTO node_sources VALUES ('Supplier', 'S1', ?, 'mapping', 1.0, 'r1', NULL)", (src_json,))
    db.execute("INSERT INTO node_sources VALUES ('Component', 'C1', ?, 'mapping', 1.0, 'r1', NULL)", (src_json,))
    db.execute("INSERT INTO edges VALUES (1, 'SUPPLIES', '[\"S1\",\"C1\",null]', 'Supplier', 'S1', 'Component', 'C1')")
    db.execute("INSERT INTO edge_sources VALUES (1, ?, 'mapping', 1.0, 'r1', NULL)", (src_json,))
    db.execute("INSERT INTO field_values (owner_kind, owner_type, owner_key, field, value_json, confidence, method,"
               " source_json, run_id, status, active) VALUES ('node', 'Supplier', 'S1', 'country', '\"Germany\"', 0.7,"
               " 'mapping', ?, 'r1', 'active', 1)", (src_json,))
    db.commit()
    db.close()

    store = GraphStore(path)
    assert [n[1] for n in store.nodes()] == ["C1", "S1"]
    assert [e.key for e in store.edges()] == [("S1", "C1", None)]
    [value] = store.field_values("Supplier", "S1")["country"]
    assert (value.value, value.review, value.active, value.proposal_id) == ("Germany", "accepted", True, None)
    assert store.active_fields()[("node", "Supplier", "S1")] == {"country": "Germany"}
    assert store.proposals() == [] and store.journal() == [] and store.deletions() == []
    # Re-opening adds nothing twice, and the store keeps working.
    store.close()
    store = GraphStore(path)
    report = store.load([field("Supplier", "S1", "city", "Berlin")])
    assert report.applied == 1 and store.stats()["proposals"] == {}


def test_pending_edge_does_not_affect_exposure():
    from scgraph.exposure.compute import Exposure

    store = GraphStore(":memory:")
    store.load([
        entity("Assembly", "A1"), field("Assembly", "A1", "revenue_eur", 100.0),
        entity("Component", "R"), field("Component", "R", "part_number", "A1"),
        entity("Component", "C"), relation("CONTAINS", ("R", "C", "A1")),
        entity("Supplier", "S1"), entity("Supplier", "S2"),
        relation("SUPPLIES", ("S1", "C", None)),
    ])
    pending = Fact("relation", "SUPPLIES", ("S2", "C", None), src(9, "B", "Components"), confidence=0.6)
    report = store.load([pending])
    assert report.proposed == 1 and report.applied == 0
    assert [e.src_id for e in store.edges("SUPPLIES")] == ["S1"]
    exposure = Exposure.from_store(store)
    assert exposure.business_exposure("Supplier", "S1") == 100.0
    assert exposure.business_exposure("Supplier", "S2") == 0.0
    stats = store.stats()
    assert stats["edges"]["SUPPLIES"] == 1 and stats["proposals"] == {"mapping": 1}


def test_only_accepted_content_is_visible():
    store = GraphStore(":memory:")
    store.load([entity("Supplier", "S1"), entity("Supplier", "S9", confidence=0.5),
                field("Supplier", "S1", "name", "Acme", confidence=0.5)])
    assert [n[1] for n in store.nodes("Supplier")] == ["S1"] and not store.has_node("Supplier", "S9")
    assert store.active_fields() == {}
    [value] = store.field_values("Supplier", "S1")["name"]
    assert (value.status, value.review, value.active) == ("proposed", "proposed", False)
    assert store.stats()["nodes"] == {"Supplier": 1} and store.stats()["field_values"] == 0


def test_threshold_is_configurable():
    facts = [entity("Supplier", "S1"), field("Supplier", "S1", "name", "Acme", confidence=0.8)]
    strict, lenient = GraphStore(":memory:"), GraphStore(":memory:", threshold=0.8)
    assert strict.load(facts).proposed == 1
    assert lenient.load(facts).proposed == 0 and lenient.active_fields()[("node", "Supplier", "S1")] == {"name": "Acme"}
