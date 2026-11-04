"""Parser fixes found with messy exports: csv encodings, references to entities outside the file, summary rows."""

import json

import openpyxl
import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.store import GraphStore
from scgraph.ingest.pipeline import ingest
from scgraph.ingest.summary import exclude_summary_rows, is_summary_label
from scgraph.ingest.validate import Known, dry_run, graph_ids
from scgraph.llm import LLMClient
from scgraph.mapping.tables import TableFile, decode_text


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


def region(rid, describes, *, header_row=1, start_row=2, stop_at_blank=None, sheet="data"):
    return {"id": rid, "sheet": sheet, "kind": "data", "header_row": header_row, "start_row": start_row,
            "stop_at_blank": stop_at_blank, "describes": describes, "domains": ["core"], "reason": "r"}


def col(header, role="key", target=None, confidence=0.95):
    out = {"header": header, "role": role, "confidence": confidence, "rationale": "r"}
    return out | ({"target": target} if target else {})


# -- 1.1 csv encodings ---------------------------------------------------------

def test_decode_text_encodings():
    assert decode_text("a;b\n".encode("utf-8")) == ("a;b\n", "utf-8")
    assert decode_text("﻿Straße\n".encode("utf-8")) == ("Straße\n", "utf-8-sig")
    assert decode_text("Straße\n".encode("utf-16")) == ("Straße\n", "utf-16")
    assert decode_text("Straße €\n".encode("cp1252")) == ("Straße €\n", "cp1252")
    assert decode_text(b"\x81x") == ("\x81x", "latin-1")  # undefined in Windows-1252; never an error


def test_cp1252_semicolon_csv_is_ingested(tmp_path):
    path = tmp_path / "Lieferanten.csv"
    path.write_bytes("Lieferanten-Nr.;Lieferant;Straße\nS1;Müller GmbH;Hauptstraße 1\nS2;Bäcker AG;Ölweg 2\n"
                     .encode("cp1252"))
    table = TableFile(path)
    assert table.encoding == "cp1252"
    assert table.region(None, header_row=1, start=2).header == {"A": "Lieferanten-Nr.", "B": "Lieferant",
                                                                "C": "Straße"}
    c, _ = client(tmp_path, [
        tool_response("submit_regions", {"regions": [region("suppliers", "Supplier")]}),
        tool_response("submit_mapping", {"produces": [{"entity": "Supplier", "key": {"column": "Lieferanten-Nr."}}],
                                         "columns": [col("Lieferanten-Nr."), col("Lieferant", "fact", "Supplier.name"),
                                                     col("Straße", "fact", "Supplier.address")]}),
    ])
    store = GraphStore(":memory:", review=False)
    report = ingest(path, c, store=store, specs_dir=tmp_path / "gen")
    assert report.encoding == "cp1252" and report.errors == []
    assert store.field_values("Supplier", "S1")["address"][0].value == "Hauptstraße 1"
    assert store.field_values("Supplier", "S2")["name"][0].value == "Bäcker AG"


def test_quoted_newline_stays_in_its_cell(tmp_path):
    path = tmp_path / "notes.csv"
    path.write_text('id,note\nS1,"two\nlines"\nS2,x\n')
    rows = TableFile(path).region(None, header_row=1, start=2).rows
    assert [r.cells["B"] for r in rows] == ["two\nlines", "x"]


# -- 1.2 references to entities in the graph or the batch ----------------------

BOM_MAPPING = {
    "produces": [{"relation": "CONTAINS", "from": {"column": "Parent"}, "to": {"column": "Child"},
                  "identity": {"assembly": {"column": "Assembly"}}}],
    "columns": [col("Parent", confidence=0.97), col("Child", confidence=0.97), col("Assembly", confidence=0.96),
                col("Quantity", "fact", "CONTAINS.quantity")],
}


@pytest.fixture
def bom_csv(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text("Parent,Child,Assembly,Quantity\nC1,C2,A1,2\nC1,C3,A1,4\nC2,C3,A1,1\n")
    return path


def components_graph():
    src = SourceLocation("components.xlsx", "Components", 2, "A")
    store = GraphStore(":memory:", review=False)
    store.load([Fact("entity", "Component", (c,), src) for c in ("C1", "C2", "C3")]
               + [Fact("entity", "Assembly", ("A1",), src)])
    return store


def test_bom_against_a_loaded_graph_needs_no_repair(tmp_path, bom_csv):
    store = components_graph()
    c, transport = client(tmp_path, [tool_response("submit_regions", {"regions": [region("bom", "CONTAINS")]}),
                                     tool_response("submit_mapping", BOM_MAPPING)])
    report = ingest(bom_csv, c, store=store, specs_dir=tmp_path / "gen")
    [r] = report.regions
    assert transport.calls == 2 and r.repair_rounds == 0 and r.problems == []
    keys = r.spec["regions"][0]["key_columns"]
    assert keys["Parent"]["confidence"] == 0.97 and keys["Child"]["confidence"] == 0.97
    assert [e.key for e in store.edges("CONTAINS")] == [("C1", "C2", "A1"), ("C1", "C3", "A1"), ("C2", "C3", "A1")]


def test_bom_alone_flags_unresolved_references(tmp_path, bom_csv):
    """Without the graph or the batch, the same file spends its repair rounds and loses confidence."""
    c, transport = client(tmp_path, [tool_response("submit_regions", {"regions": [region("bom", "CONTAINS")]}),
                                     tool_response("submit_mapping", BOM_MAPPING),
                                     tool_response("submit_mapping", BOM_MAPPING),
                                     tool_response("submit_mapping", BOM_MAPPING)])
    report = ingest(bom_csv, c, store=None, specs_dir=tmp_path / "gen")
    [r] = report.regions
    assert transport.calls == 4 and r.repair_rounds == 2
    assert any("do not resolve" in p for p in r.problems)
    assert r.spec["regions"][0]["key_columns"]["Parent"]["confidence"] == 0.0


def test_batch_values_resolve_references_of_any_type(tmp_path, bom_csv):
    c, transport = client(tmp_path, [tool_response("submit_regions", {"regions": [region("bom", "CONTAINS")]}),
                                     tool_response("submit_mapping", BOM_MAPPING)])
    other = {"Component ID", "C1", "C2", "C3", "A1", "Widget"}
    report = ingest(bom_csv, c, store=None, specs_dir=tmp_path / "gen", batch_values=other)
    assert transport.calls == 2 and report.regions[0].problems == []


def test_known_ids_by_type_and_loose():
    store = components_graph()
    known = Known(graph_ids(store), {"X9"})
    assert known.has("Component", "C1") and not known.has("Supplier", "C1")
    assert known.has("Supplier", "X9")


def test_proposed_nodes_count_as_known():
    store = GraphStore(":memory:", threshold=0.9)
    src = SourceLocation("s.csv", None, 2, "A")
    store.load([Fact("entity", "Supplier", ("S7",), src, confidence=0.5, method="mapping")])
    assert not store.has_node("Supplier", "S7")
    assert graph_ids(store) == {"Supplier": {"S7"}}


# -- 1.3 summary rows ------------------------------------------------------------

@pytest.mark.parametrize("label", ["TOTAL", "Total:", "Grand total", "Sub-total", "subtotal", "Summe", "Gesamt",
                                   "SUM", "Totale", "Итого", " total. "])
def test_summary_labels(label):
    assert is_summary_label(label)


@pytest.mark.parametrize("label", ["Total Steel AG", "C-TOTAL-1", "Summit", "S1", "", None, 12])
def test_not_summary_labels(label):
    assert not is_summary_label(label)


@pytest.fixture
def erp_export(tmp_path):
    """A component export with title rows above the header and a totals row under the table."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append(["Component master data export"])
    ws.append(["Generated 2026-10-12 by ERP"])
    ws.append([])
    ws.append(["Component ID", "Description", "Annual Spend"])
    for i in range(1, 6):
        ws.append([f"C{i}", f"Part {i}", 100.0 * i])
    ws.append(["TOTAL", None, 1500.0])
    path = tmp_path / "components_erp_export.xlsx"
    wb.save(path)
    return path


COMPONENT_MAPPING = {"produces": [{"entity": "Component", "key": {"column": "Component ID"}}],
                     "columns": [col("Component ID"), col("Description", "fact", "Component.description"),
                                 col("Annual Spend", "fact", "Component.annual_spend")]}


def test_totals_row_is_excluded_without_a_model_call(tmp_path, erp_export):
    plan = region("export_components", "Component", header_row=4, start_row=5, stop_at_blank="Component ID",
                  sheet="Export")
    c, transport = client(tmp_path, [tool_response("submit_regions", {"regions": [plan]}),
                                     tool_response("submit_mapping", COMPONENT_MAPPING)])
    store = GraphStore(":memory:", review=False)
    report = ingest(erp_export, c, store=store, specs_dir=tmp_path / "gen")
    assert transport.calls == 2
    assert [i for _, i, _ in store.nodes("Component")] == ["C1", "C2", "C3", "C4", "C5"]
    [r] = report.regions
    assert r.excluded_rows == [{"sheet": "Export", "row": 10, "label": "TOTAL", "reason": "summary row under the table"}]
    stored = json.loads(r.path.read_text())
    assert stored["regions"][0]["rows"]["end"] == 9 and stored["excluded_rows"] == r.excluded_rows

    # The stored spec is reused for the next export of the same layout: still no TOTAL, no model call.
    again = ingest(erp_export, None, store=GraphStore(":memory:", review=False), specs_dir=tmp_path / "gen",
                   reuse_only=True)
    assert again.regions[0].source == "reused" and again.regions[0].excluded_rows == r.excluded_rows
    assert not any(f.key == ("TOTAL",) for f in again.facts)


def test_reused_spec_without_end_still_drops_the_totals_row(tmp_path, erp_export):
    spec = {"version": 1, "file_kind": "xlsx", "metadata": {"describes": "Component"},
            "regions": [{"id": "c", "sheet": "Export", "header_row": 4, "rows": {"start": 5},
                         "produces": [{"entity": "Component", "key": {"column": "Component ID"}}],
                         "key_columns": {"Component ID": {"role": "key", "confidence": 1.0}}, "columns": {}}]}
    cut = exclude_summary_rows(spec, TableFile(erp_export))
    assert cut["regions"][0]["rows"] == {"start": 5, "end": 9} and spec["regions"][0]["rows"] == {"start": 5}
    assert exclude_summary_rows(cut, TableFile(erp_export)) is cut
    _, findings = dry_run({"c": cut}, TableFile(erp_export))
    assert findings["c"].duplicate_keys == []
