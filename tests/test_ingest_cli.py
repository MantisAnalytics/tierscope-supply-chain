import json

import openpyxl
import pytest

from fakes import FakeTransport
from scgraph.cli import main
from scgraph.graph.store import GraphStore
from scgraph.ingest.compare import compare
from scgraph.ingest.layout import layout_of
from scgraph.ingest.profile import Profiler


def run(argv, transport=None):
    return main(argv, transport=transport, load_env=lambda: None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCGRAPH_GRAPH_DB", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("SCGRAPH_LLM_DB", str(tmp_path / "llm.sqlite"))
    monkeypatch.setenv("SCGRAPH_LLM_PROVIDER", "meta")  # the fake transport stands in for the provider
    monkeypatch.setenv("SCGRAPH_LLM_CACHE", "record")  # the fake transport answers; a shell's replay must not apply
    monkeypatch.setenv("MODEL_API_KEY", "k")
    return tmp_path


@pytest.fixture
def suppliers_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Suppliers"
    for r in [["Supplier ID", "Name", "Country"], ["S1", "Acme", "Germany"], ["S2", "Bolt", "France"]]:
        ws.append(r)
    path = tmp_path / "s.xlsx"
    wb.save(path)
    return path


def write_spec(directory, path, columns, name="s__suppliers.json", status="draft"):
    _, profiles = Profiler(path).region("Suppliers", header_row=1, start=2)
    spec = {
        "version": 1, "file_kind": "xlsx",
        "metadata": {"author": "model", "model": "muse-spark-1.1", "status": status, "region_kind": "data"},
        "regions": [{"id": "suppliers", "sheet": "Suppliers", "header_row": 1, "rows": {"start": 2},
                     "confidence": 0.9, "produces": [{"entity": "Supplier", "key": {"column": "Supplier ID"}}],
                     "key_columns": {"Supplier ID": {"role": "key", "confidence": 0.9, "rationale": "id"}},
                     "columns": columns}],
        "layout": {"kind": "region", "sheet": "Suppliers", "header_row": 1, **layout_of(profiles)},
    }
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(json.dumps(spec))
    return directory / name


COLUMNS = {"Name": {"role": "fact", "target": "Supplier.name", "confidence": 0.7, "rationale": "name"},
           "Country": {"role": "fact", "target": "Supplier.country", "confidence": 0.6, "rationale": "country"}}


def test_reuse_only_fails_when_nothing_matches(env, suppliers_xlsx, capsys):
    transport = FakeTransport()
    code = run(["ingest", str(suppliers_xlsx), "--reuse-only", "--specs-dir", str(env / "empty")], transport)
    assert code == 1 and transport.calls == 0
    assert "no stored spec" in capsys.readouterr().err


def test_reuse_only_uses_stored_spec_with_zero_calls(env, suppliers_xlsx, capsys):
    write_spec(env / "gen", suppliers_xlsx, COLUMNS)
    transport = FakeTransport()
    assert run(["ingest", str(suppliers_xlsx), "--reuse-only", "--specs-dir", str(env / "gen")], transport) == 0
    out = capsys.readouterr().out
    assert transport.calls == 0 and "[data, reused]" in out and "LLM: 0 calls" in out
    store = GraphStore(env / "graph.sqlite")
    [country] = store.field_values("Supplier", "S1")["country"]
    assert country.confidence == 0.6


def test_approve_then_load_with_confidence_one(env, suppliers_xlsx, capsys):
    path = write_spec(env / "gen", suppliers_xlsx, COLUMNS)
    assert run(["ingest", "spec", "show", str(path)]) == 0
    assert "status draft" in capsys.readouterr().out
    assert run(["ingest", "spec", "approve", str(path)]) == 0
    spec = json.loads(path.read_text())
    assert spec["metadata"]["status"] == "approved"
    assert {c["confidence"] for c in spec["regions"][0]["columns"].values()} == {1.0}
    assert run(["ingest", str(suppliers_xlsx), "--reuse-only", "--specs-dir", str(env / "gen")]) == 0
    store = GraphStore(env / "graph.sqlite")
    values = [v for vs in store.field_values("Supplier", "S1").values() for v in vs]
    assert {(v.confidence, v.method) for v in values} == {(1.0, "mapping")}


def test_compare_two_specs_differing_in_one_role(env, suppliers_xlsx):
    generated = {**COLUMNS, "Country": {"role": "ignore", "confidence": 0.5, "rationale": "unsure"}}
    write_spec(env / "gen", suppliers_xlsx, generated)
    reference = write_spec(env / "ref", suppliers_xlsx, COLUMNS, name="reference.json", status="approved")
    c = compare(suppliers_xlsx, reference, env / "gen")
    assert (c.nodes.share, c.edges.share) == (1.0, 1.0)
    assert (c.fields.matched, c.fields.total) == (2, 4)
    assert c.role_differences == [("Suppliers", "country", "fact", "ignore")]
    assert c.derived_missing == []


def test_compare_cli(env, suppliers_xlsx, capsys):
    write_spec(env / "gen", suppliers_xlsx, COLUMNS)
    reference = write_spec(env / "ref", suppliers_xlsx, COLUMNS, name="reference.json")
    assert run(["ingest", "compare", str(suppliers_xlsx), "--reference", str(reference),
                "--specs-dir", str(env / "gen")]) == 0
    out = capsys.readouterr().out
    assert "node keys     2/2 (100.0%)" in out and "role differences 0" in out


def test_missing_file(env, capsys):
    assert run(["ingest", str(env / "nope.xlsx"), "--specs-dir", str(env / "gen")]) == 1
    assert "ingest failed" in capsys.readouterr().err


def test_batch_of_files_and_a_zip(env, capsys):
    import zipfile

    from fakes import tool_response
    from test_batch import COMPONENTS, SUPPLIERS, plan

    suppliers = env / "suppliers.csv"
    suppliers.write_text("Supplier ID,Name\nS1,Acme Inc.\nS2,Bolt AG\n")
    with zipfile.ZipFile(env / "exports.zip", "w") as z:
        z.writestr("erp/components.csv", "Component ID,Supplier ID\nC1,S1\nC2,S2\nTOTAL,\n")
        z.writestr("erp/logo.png", b"\x89PNG")
        z.writestr("__MACOSX/erp/._components.csv", b"junk")
    transport = FakeTransport([
        tool_response("submit_regions", plan("suppliers", "Supplier")),
        tool_response("submit_mapping", SUPPLIERS),
        tool_response("submit_regions", plan("components", "Component")),
        tool_response("submit_mapping", COMPONENTS),
    ])
    assert run(["ingest", str(suppliers), str(env / "exports.zip"), "--specs-dir", str(env / "gen")], transport) == 0
    out = capsys.readouterr().out
    assert "batch of 2 files" in out and "skipped  exports.zip/erp/logo.png (unsupported type)" in out
    assert "== suppliers.csv: done" in out and "== exports.zip/erp/components.csv: done" in out
    assert "excluded row 4 'TOTAL': summary row under the table" in out
    assert "batch: 2 done, 0 failed, 1 skipped;" in out and "summary rows excluded 1" in out
    assert "LLM 4 calls" in out and transport.calls == 4
    store = GraphStore(env / "graph.sqlite")
    assert [i for _, i, _ in store.nodes("Component")] == ["C1", "C2"]
    assert [(e.src_id, e.dst_id) for e in store.edges("SUPPLIES")] == [("S1", "C1"), ("S2", "C2")]


def test_batch_over_the_limit_is_refused(env, capsys, monkeypatch):
    import scgraph.ingest.batch as batch

    monkeypatch.setattr(batch, "MAX_FILES", 1)
    a, b = env / "a.csv", env / "b.csv"
    a.write_text("id\n1\n")
    b.write_text("id\n2\n")
    transport = FakeTransport()
    assert run(["ingest", str(a), str(b), "--specs-dir", str(env / "gen")], transport) == 1
    assert "more than the limit of 1 files" in capsys.readouterr().err and transport.calls == 0
