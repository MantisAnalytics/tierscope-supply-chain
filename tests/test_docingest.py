from fakes import FakeTransport, make_config, tool_response
from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.store import GraphStore
from scgraph.ingest.docingest import ingest_document
from scgraph.llm import LLMClient

TEXT = """Supplier report

Supplier S154292 is Nemkan Springs & Stampings Inc. Its city is Lexington, KY.

Component DMC-2221 is supplied by Porben Metal Forming for delivery to Plant Bremen.
Component DMC-2222 is supplied by Zzz Unknown Works.
"""


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


def graph():
    src = SourceLocation("ref.xlsx", "S", 1, "A")
    store = GraphStore(":memory:", review=False)
    facts = [Fact("entity", "Component", (c,), src) for c in ("DMC-2221", "DMC-2222")]
    for sid, name, city in [("S1", "Porben Metal Forming GmbH", "Potsdam"), ("S2", "Porben Metal Forming GmbH", "Bremen")]:
        facts += [Fact("entity", "Supplier", (sid,), src), Fact("field", "Supplier", (sid,), src, field="name", value=name),
                  Fact("field", "Supplier", (sid,), src, field="city", value=city)]
    facts += [Fact("entity", "Site", ("X1",), src), Fact("field", "Site", ("X1",), src, field="name", value="Plant Bremen")]
    store.load(facts)
    return store


FACTS = {"facts": [
    {"kind": "entity", "type": "Supplier", "mention": "S154292", "quote": "Supplier S154292 is Nemkan", "confidence": 1},
    {"kind": "field", "type": "Supplier", "mention": "Nemkan Springs & Stampings", "field": "city",
     "value": "Lexington, KY", "quote": "Its city is Lexington, KY.", "confidence": 0.9},
    {"kind": "field", "type": "Supplier", "mention": "S154292", "field": "name", "value": "Nemkan Springs & Stampings Inc.",
     "quote": "Supplier S154292 is Nemkan Springs & Stampings Inc.", "confidence": 1},
    {"kind": "relation", "type": "SUPPLIES", "from": "Porben Metal Forming", "to": "DMC-2221",
     "identity": {"site": "Plant Bremen"},
     "quote": "Component DMC-2221 is supplied by Porben Metal Forming for delivery to Plant Bremen.", "confidence": 0.9},
    {"kind": "relation", "type": "SUPPLIES", "from": "Zzz Unknown Works", "to": "DMC-2222",
     "quote": "Component DMC-2222 is supplied by Zzz Unknown Works.", "confidence": 0.8},
    {"kind": "relation", "type": "SUPPLIES", "from": "Nobody", "to": "DMC-2222", "quote": "made up", "confidence": 1},
]}


def test_text_document_end_to_end(tmp_path):
    path = tmp_path / "report.txt"
    path.write_text(TEXT)
    store = graph()
    c, transport = client(tmp_path, [
        tool_response("submit_document", {"domains": ["core"], "types": ["Supplier", "SUPPLIES"]}),
        tool_response("submit_facts", FACTS),
        tool_response("submit_resolutions", {"resolutions": [{"mention_id": 0, "choice": "S2", "confidence": 0.7}]}),
    ])
    report = ingest_document(path, c, store=store, specs_dir=tmp_path / "gen")
    assert report.chunks == 1 and report.extracted == 6 and len(report.rejected) == 1
    assert report.resolutions == {"id": 3, "name": 2, "model": 1, "unresolved": 1}
    assert report.unresolved == [("Supplier", "Zzz Unknown Works", "mention:Zzz Unknown Works")]
    assert transport.calls == 3

    # A confident extraction through an uncertain match gets the lower confidence; provenance names the candidates.
    [edge] = [e for e in store.edges("SUPPLIES") if e.src_id == "S2"]
    assert edge.key == ("S2", "DMC-2221", "X1")
    row = store._db.execute("SELECT confidence, method, resolution_json, source_json FROM edge_sources"
                            " WHERE edge_id = ?", (edge.id,)).fetchone()
    assert row[0] == 0.7 and row[1] == "llm"
    assert '"candidates":["S1","S2"]' in row[2] and '"method":"model"' in row[2]
    assert '"lines":[5,5]' in row[3] and "quote" in row[3]

    # The supplier the text introduces keeps its id; its city mention by name resolves to it.
    [city] = store.field_values("Supplier", "S154292")["city"]
    assert city.value == "Lexington, KY" and city.resolution["method"] == "name" and city.confidence == 0.9
    assert city.source.quote == "Its city is Lexington, KY."

    # The unknown name is kept as a flagged node.
    assert store.has_node("Supplier", "mention:Zzz Unknown Works")
    assert store.stats()["unresolved_mentions"] == 1
    assert ("mention:Zzz Unknown Works", "DMC-2222", None) in [e.key for e in store.edges("SUPPLIES")]

    # Every text fact names its passage; the unresolved mention's facts name the passage it came from.
    assert all(f.origin["document"] == "report.txt" and f.origin["chunk"] == 0 for f in report.facts)
    assert all(f.origin["quote"] == f.source.quote for f in report.facts)
    zzz = [f for f in report.facts if f.key[0] == "mention:Zzz Unknown Works"]
    assert {f.origin["quote"] for f in zzz} == {"Component DMC-2222 is supplied by Zzz Unknown Works."}


def test_chunk_failure_is_reported_without_stopping(tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("A.\n\n" + "B. " * 10)
    c, _ = client(tmp_path, [tool_response("submit_document", {"domains": ["core"], "types": ["Supplier"]}),
                             tool_response("submit_facts", {"facts": "x"}), tool_response("submit_facts", {"facts": "y"})])
    report = ingest_document(path, c, store=GraphStore(":memory:", review=False), specs_dir=tmp_path / "gen")
    assert report.chunks == 1 and len(report.chunk_errors) == 1 and "lines 1-1" in report.chunk_errors[0]
    assert report.load is not None


def test_cli_dispatches_text_to_document_ingest(tmp_path, monkeypatch, capsys):
    from scgraph.cli import main

    monkeypatch.setenv("SCGRAPH_GRAPH_DB", str(tmp_path / "g.sqlite"))
    monkeypatch.setenv("SCGRAPH_LLM_DB", str(tmp_path / "llm.sqlite"))
    monkeypatch.setenv("SCGRAPH_LLM_CACHE", "record")  # the fake transport answers; a shell's replay must not apply
    monkeypatch.setenv("SCGRAPH_LLM_PROVIDER", "meta")  # the fake transport stands in for the provider
    monkeypatch.setenv("MODEL_API_KEY", "k")
    path = tmp_path / "report.txt"
    path.write_text(TEXT)
    transport = FakeTransport([
        tool_response("submit_document", {"domains": ["core"], "types": ["Supplier", "SUPPLIES"]}),
        tool_response("submit_facts", FACTS),
    ])
    assert main(["ingest", str(path), "--specs-dir", str(tmp_path / "gen")], transport=transport,
                load_env=lambda: None) == 0
    out = capsys.readouterr().out
    assert "document report.txt" in out and "chunks 1 (0 failed), facts extracted 6, rejected 1" in out
    assert "resolution: id 1, name 1, unresolved 5" in out and "LLM: 2 calls" in out
    assert "unresolved Component 'DMC-2221' -> mention:DMC-2221" in out  # empty graph: nothing describes it


def test_entity_fact_for_a_name_resolves_to_the_introduced_id(tmp_path):
    path = tmp_path / "r.txt"
    path.write_text("Supplier S9 is Acme Inc.\n\nComponent C1 is supplied by Acme Inc.\n")
    facts = {"facts": [
        {"kind": "entity", "type": "Supplier", "mention": "S9", "quote": "Supplier S9 is Acme Inc.", "confidence": 1},
        {"kind": "field", "type": "Supplier", "mention": "S9", "field": "name", "value": "Acme Inc.",
         "quote": "Supplier S9 is Acme Inc.", "confidence": 1},
        {"kind": "entity", "type": "Supplier", "mention": "Acme Inc.", "quote": "supplied by Acme Inc.", "confidence": 1},
        {"kind": "entity", "type": "Component", "mention": "C1", "quote": "Component C1", "confidence": 1},
        {"kind": "relation", "type": "SUPPLIES", "from": "Acme Inc.", "to": "C1", "quote": "supplied by Acme Inc.",
         "confidence": 1},
    ]}
    c, _ = client(tmp_path, [tool_response("submit_document", {"domains": ["core"], "types": []}),
                             tool_response("submit_facts", facts)])
    store = GraphStore(":memory:", review=False)
    ingest_document(path, c, store=store, specs_dir=tmp_path / "gen")
    assert [i for _, i, _ in store.nodes("Supplier")] == ["S9"]
    assert [e.key for e in store.edges("SUPPLIES")] == [("S9", "C1", None)]


def test_extraction_allows_every_type_of_the_classified_domains():
    from scgraph.ingest.textextract import DocClass, allowed_types

    allowed = allowed_types(DocClass(["core"], ["Component"]))
    assert {"CONTAINS", "SUPPLIES", "Assembly", "Site"} <= allowed and "PARENT_SUPPLIER" not in allowed
