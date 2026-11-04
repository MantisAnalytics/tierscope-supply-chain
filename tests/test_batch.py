"""Batch ingest: archive expansion rules, limits, order, cross-file references, per-file reports and failures."""

import io
import threading
import zipfile

import openpyxl
import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.graph.store import GraphStore
from scgraph.ingest.batch import BatchError, expand, run_batch, safe_name
from scgraph.llm import LLMClient


def xlsx_bytes(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def files_under(directory):
    return sorted(p for p in directory.rglob("*") if p.is_file())


@pytest.mark.parametrize("entry, expected", [
    ("exports/bom.csv", "exports/bom.csv"),
    ("./exports//bom.csv", "exports/bom.csv"),
    ("../../etc/passwd.csv", "passwd.csv"),
    ("/etc/passwd.csv", "passwd.csv"),
    ("C:\\Users\\x\\bom.csv", "bom.csv"),
    ("exports\\bom.csv", "exports/bom.csv"),
    ("__MACOSX/exports/._bom.csv", None),
    (".DS_Store", None),
    ("exports/.hidden/bom.csv", None),
    ("exports/", None),
])
def test_safe_names(entry, expected):
    assert safe_name(entry) == expected


def test_zip_with_folder_junk_and_traversal(tmp_path):
    archive = make_zip(tmp_path / "batch.zip", {
        "exports/notes.txt": "Supplier S1 is Acme Inc.\n",
        "exports/components.xlsx": xlsx_bytes([["Component ID"], ["C1"]]),
        "exports/suppliers.csv": "Supplier ID,Name\nS1,Acme\n",
        "exports/photo.png": b"\x89PNG",
        "exports/inner.zip": b"PK\x05\x06" + b"\0" * 18,
        "__MACOSX/exports/._suppliers.csv": b"junk",
        "exports/.DS_Store": b"junk",
        "../../etc/passwd.csv": "id\nx\n",
    })
    plain = tmp_path / "contract.md"
    plain.write_text("# Contract\n")
    work = tmp_path / "work"
    batch = expand([("contract.md", plain), ("batch.zip", archive)], work)

    assert [(f.name, f.kind, f.status) for f in batch.files] == [
        ("batch.zip/exports/components.xlsx", "table", "queued"),
        ("batch.zip/exports/suppliers.csv", "table", "queued"),
        ("batch.zip/passwd.csv", "table", "queued"),
        ("contract.md", "document", "queued"),
        ("batch.zip/exports/notes.txt", "document", "queued"),
        ("batch.zip/exports/photo.png", "skipped", "skipped"),
        ("batch.zip/exports/inner.zip", "skipped", "skipped"),
    ]
    assert {f.reason for f in batch.files if f.kind == "skipped"} == {"unsupported type", "archive inside an archive"}
    # Archive members are written only inside the work directory; nothing lands next to it.
    assert not (tmp_path / "etc").exists() and not (tmp_path.parent / "etc" / "passwd.csv").exists()
    written = files_under(work)
    assert len(written) == 4 and all(p.resolve().is_relative_to(work.resolve()) for p in written)
    assert {p.name for p in written} == {"components.xlsx", "suppliers.csv", "passwd.csv", "notes.txt"}
    assert next(f for f in batch.files if f.name.endswith("passwd.csv")).path.read_text() == "id\nx\n"


def test_limits_refuse_the_batch_before_writing(tmp_path):
    archive = make_zip(tmp_path / "big.zip", {f"t{i}.csv": "id\n" + "x\n" * 1000 for i in range(3)})
    with pytest.raises(BatchError, match="limit of 5000 bytes"):
        expand([("big.zip", archive)], tmp_path / "w1", max_bytes=5000)
    assert files_under(tmp_path / "w1") == []
    with pytest.raises(BatchError, match="more than the limit of 2 files"):
        expand([("big.zip", archive)], tmp_path / "w2", max_files=2)
    assert files_under(tmp_path / "w2") == []


def test_broken_archive_is_refused(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(BatchError, match="not a readable zip"):
        expand([("bad.zip", bad)], tmp_path / "w")


# -- running a batch ---------------------------------------------------------------

def plan(rid, describes):
    return {"regions": [{"id": rid, "sheet": "x", "kind": "data", "header_row": 1, "start_row": 2,
                         "describes": describes, "domains": ["core"], "reason": "r"}]}


def col(header, role="key", target=None):
    return {"header": header, "role": role, "confidence": 0.95, "rationale": "r"} | ({"target": target} if target else {})


COMPONENTS = {"produces": [{"entity": "Component", "key": {"column": "Component ID"}},
                           {"relation": "SUPPLIES", "from": {"column": "Supplier ID"}, "to": {"column": "Component ID"}}],
              "columns": [col("Component ID"), col("Supplier ID")]}
SUPPLIERS = {"produces": [{"entity": "Supplier", "key": {"column": "Supplier ID"}}],
             "columns": [col("Supplier ID"), col("Name", "fact", "Supplier.name")]}
CONTRACT = "Framework contract\n\nAcme Inc. supplies component C1 to us.\n"
CONTRACT_FACTS = {"facts": [{"kind": "field", "type": "Supplier", "mention": "Acme Inc.", "field": "risk_notes",
                             "value": "framework contract", "quote": "Acme Inc. supplies component C1 to us.",
                             "confidence": 0.9}]}


@pytest.fixture
def exports_zip(tmp_path):
    return make_zip(tmp_path / "batch.zip", {
        "exports/contract.txt": CONTRACT,
        "exports/components.csv": "Component ID,Supplier ID\nC1,S1\nC2,S2\n",
        "exports/suppliers.csv": "Supplier ID,Name\nS1,Acme Inc.\nS2,Bolt AG\n",
        "__MACOSX/exports/._components.csv": b"junk",
    })


def test_batch_of_tables_and_a_contract(tmp_path, exports_zip):
    transport = FakeTransport([
        tool_response("submit_regions", plan("components", "Component")),
        tool_response("submit_mapping", COMPONENTS),
        tool_response("submit_regions", plan("suppliers", "Supplier")),
        tool_response("submit_mapping", SUPPLIERS),
        tool_response("submit_document", {"domains": ["core"], "types": ["Supplier"]}),
        tool_response("submit_facts", CONTRACT_FACTS),
    ])
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport)
    store = GraphStore(":memory:", review=False)
    batch = expand([("batch.zip", exports_zip)], tmp_path / "work")
    snapshots = []
    run_batch(batch, client, store=store, specs_dir=tmp_path / "gen", lock=threading.Lock(),
              on_change=lambda b: snapshots.append(b.to_dict()))

    assert [(f.name, f.status) for f in batch.files] == [("batch.zip/exports/components.csv", "done"),
                                                       ("batch.zip/exports/suppliers.csv", "done"),
                                                       ("batch.zip/exports/contract.txt", "done")]
    # Components come first, yet their supplier ids resolve against the suppliers file of the same batch.
    components = batch.files[0].report
    assert components.regions[0].problems == [] and components.regions[0].repair_rounds == 0
    assert transport.calls == 6  # no repair round, no resolution call
    # The contract's fact attaches to the supplier the batch's table described, by name, without a proposal.
    contract = batch.files[2].report
    assert contract.unresolved == [] and contract.resolutions == {"name": 1}
    [note] = store.field_values("Supplier", "S1")["risk_notes"]
    assert note.value == "framework contract" and note.source.file == "batch.zip/exports/contract.txt"
    assert "batch.zip/exports/suppliers.csv" in {source.file for source, *_ in store.node_sources("Supplier", "S1")}

    finished = [sum(f["status"] in ("done", "failed") for f in s["files"]) for s in snapshots]
    assert finished == sorted(finished) and finished[-1] == 3
    assert any(s["current"] == "batch.zip/exports/suppliers.csv" for s in snapshots)
    totals = batch.to_dict()["totals"]
    assert totals["done"] == 3 and totals["failed"] == 0 and totals["llm_calls"] == 6
    assert totals["facts_applied"] == sum(f.report.load.applied for f in batch.files)
    assert batch.to_dict()["files"][0]["report"]["kind"] == "table"


NOTICE = ("Notice of force majeure\n\nA fire on 28 October stopped production at our Lexington plant. "
          "We expect to resume deliveries in about eight weeks.\n")


def test_notice_in_a_batch_becomes_a_draft_disruption(tmp_path):
    archive = make_zip(tmp_path / "batch.zip", {
        "components.csv": "Component ID,Supplier ID\nC1,S1\nC2,S2\n",
        "suppliers.csv": "Supplier ID,Name\nS1,Acme Inc.\nS2,Bolt AG\n",
        "fire-notice.txt": NOTICE,
    })
    transport = FakeTransport([
        tool_response("submit_regions", plan("components", "Component")),
        tool_response("submit_mapping", COMPONENTS),
        tool_response("submit_regions", plan("suppliers", "Supplier")),
        tool_response("submit_mapping", SUPPLIERS),
        tool_response("submit_document", {"domains": [], "types": [], "disruption_report": True,
                                          "summary": "A supplier's fire notice."}),
    ])
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport)
    store = GraphStore(":memory:", review=False)
    drafted = []

    def draft(text, name):
        drafted.append((text, name))
        return {"id": 7, "name": "Fire at Acme", "thread_id": 3, "warnings": ["supplier 'Acme' is not in the graph"]}

    batch = run_batch(expand([("batch.zip", archive)], tmp_path / "work"), client, store=store,
                      specs_dir=tmp_path / "gen", on_disruption=draft)
    assert [f.status for f in batch.files] == ["done", "done", "done"]
    assert store.has_node("Supplier", "S1") and store.has_node("Component", "C2")  # the tables are in the graph
    notice = batch.to_dict()["files"][2]
    assert notice["name"] == "batch.zip/fire-notice.txt"
    assert notice["report"]["disruption_report"] and notice["report"]["disruption"]["id"] == 7
    assert notice["report"]["chunks"] == 0 and notice["report"]["load"] == {}  # no facts from the notice
    assert drafted == [(NOTICE, "batch.zip/fire-notice.txt")] and transport.calls == 5


def test_one_broken_file_does_not_stop_the_others(tmp_path):
    good = tmp_path / "suppliers.csv"
    good.write_text("Supplier ID,Name\nS1,Acme\n")
    broken = tmp_path / "broken.csv"
    broken.write_text("Part;Qty\nX;1\n")
    worse = tmp_path / "components.xlsx"
    worse.write_bytes(b"this is no workbook")
    transport = FakeTransport([
        tool_response("submit_regions", plan("suppliers", "Supplier")),
        tool_response("submit_mapping", SUPPLIERS),
    ])  # then plain text: the model never maps broken.csv
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport)
    store = GraphStore(":memory:", review=False)
    batch = expand([(p.name, p) for p in (good, broken, worse)], tmp_path / "work")
    run_batch(batch, client, store=store, specs_dir=tmp_path / "gen")
    status = {f.name: (f.status, f.error) for f in batch.files}
    assert status["suppliers.csv"] == ("done", None)
    assert status["broken.csv"][0] == "failed" and "model failed twice" in status["broken.csv"][1]
    assert status["components.xlsx"][0] == "failed" and status["components.xlsx"][1]
    assert store.has_node("Supplier", "S1")
    totals = batch.totals()
    assert (totals["done"], totals["failed"]) == (1, 2)


def test_reuse_only_without_specs_fails_each_file_with_its_error(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("id\n1\n")
    b.write_text("id\n2\n")
    batch = expand([("a.csv", a), ("b.csv", b)], tmp_path / "work")
    run_batch(batch, None, store=None, specs_dir=tmp_path / "gen", reuse_only=True)
    assert [f.status for f in batch.files] == ["failed", "failed"]
    assert all("no stored spec" in f.error for f in batch.files)


def test_document_resolves_against_the_batch_tables_without_the_graph(tmp_path):
    """Mentions resolve to entities the batch's tables described even when the graph does not hold them yet."""
    from scgraph.graph.facts import Fact, SourceLocation
    from scgraph.ingest.docingest import ingest_document

    src = SourceLocation("batch.zip/suppliers.csv", None, 2, "A")
    context = [Fact("entity", "Supplier", ("S1",), src), Fact("field", "Supplier", ("S1",), src, field="name",
                                                              value="Acme Inc.")]
    path = tmp_path / "contract.txt"
    path.write_text(CONTRACT)
    transport = FakeTransport([tool_response("submit_document", {"domains": ["core"], "types": ["Supplier"]}),
                               tool_response("submit_facts", CONTRACT_FACTS)])
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport)
    report = ingest_document(path, client, store=None, specs_dir=tmp_path / "gen", name="batch.zip/contract.txt",
                             context_facts=context)
    assert report.file == "batch.zip/contract.txt" and report.unresolved == [] and transport.calls == 2
    [fact] = report.facts
    assert fact.key == ("S1",) and fact.source.file == "batch.zip/contract.txt"
