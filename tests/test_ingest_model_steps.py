import openpyxl
import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.ingest.classify import classify, parse_regions
from scgraph.ingest.mapgen import Mapper
from scgraph.ingest.profile import Profiler
from scgraph.llm import LLMClient
from scgraph.mapping.spec import parse_spec


@pytest.fixture
def workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Suppliers"
    for r in [["Supplier ID*", "Supplier Name*", "Country*", "Exposure"],
              ["S1", "Acme", "Germany", "=1+1"], ["S2", "Bolt", "France", "=2+2"]]:
        ws.append(r)
    weights = wb.create_sheet("Weights")
    weights["A1"], weights["B1"] = "Margin", 0.19
    readme = wb.create_sheet("ReadMe")
    readme["A1"] = "Fill in the yellow cells."
    path = tmp_path / "w.xlsx"
    wb.save(path)
    return path


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


REGIONS = {"regions": [
    {"id": "Suppliers", "sheet": "Suppliers", "kind": "data", "header_row": 1, "start_row": 2,
     "stop_at_blank": "Supplier ID*", "describes": "Supplier", "domains": ["core"], "reason": "supplier master"},
    {"id": "margin", "sheet": "Weights", "kind": "parameters", "start_row": 1, "end_row": 1,
     "reason": "margin cell"},
    {"id": "readme", "sheet": "ReadMe", "kind": "instructions", "start_row": 1, "reason": "instructions"},
]}


def test_classification_parsed_into_region_plans(tmp_path, workbook):
    c, transport = client(tmp_path, [tool_response("submit_regions", REGIONS)])
    plans = classify(c, Profiler(workbook), ["Suppliers", "Weights", "ReadMe"]).value
    assert [(p.id, p.sheet, p.kind) for p in plans] == [("suppliers", "Suppliers", "data"),
                                                         ("margin", "Weights", "parameters"),
                                                         ("readme", "ReadMe", "instructions")]
    assert plans[0].describes == "Supplier" and plans[0].domains == ["core"]
    assert plans[1].domains == ["core", "tiers", "risk"]  # unspecified domains: the whole schema
    assert "A2=S1" in transport.requests[0].messages[0]["content"]


def test_classification_rejects_missing_sheet_and_unknown_type():
    with pytest.raises(ValueError, match="sheets without any region: \\['ReadMe'\\]"):
        parse_regions({"regions": REGIONS["regions"][:2]}, ["Suppliers", "Weights", "ReadMe"])
    bad = {"regions": [{**REGIONS["regions"][0], "describes": "Vendor"}]}
    with pytest.raises(ValueError, match="Vendor"):
        parse_regions(bad, ["Suppliers"])


MAPPING = {
    "produces": [{"entity": "Supplier", "key": {"column": "Supplier ID*"}}],
    "columns": [
        {"header": "Supplier ID*", "role": "key", "confidence": 0.99, "rationale": "unique supplier id"},
        {"header": "Supplier Name*", "role": "fact", "target": "Supplier.name", "confidence": 0.95,
         "rationale": "name"},
        {"header": "Country*", "role": "fact", "target": "Supplier.country", "confidence": 0.9, "rationale": "c"},
        {"header": "Exposure", "role": "derived", "metric": "business_exposure_pct", "entity": "Supplier",
         "confidence": 0.8, "rationale": "formula"},
    ],
}


def test_mapping_result_validates_against_schema(tmp_path, workbook):
    plans = parse_regions(REGIONS, ["Suppliers", "Weights", "ReadMe"])
    c, transport = client(tmp_path, [tool_response("submit_mapping", MAPPING)])
    spec = Mapper(c, Profiler(workbook), plans, run_id="r1").generate(plans[0]).value
    parsed = parse_spec(spec)
    assert parsed.metadata == {"author": "model", "model": "muse-spark-1.1", "run_id": "r1", "status": "draft",
                               "source_file": "w.xlsx", "region_kind": "data", "describes": "Supplier"}
    region = spec["regions"][0]
    assert region["key_columns"] == {"Supplier ID*": {"role": "key", "confidence": 0.99,
                                                      "rationale": "unique supplier id"}}
    assert region["columns"]["Exposure"]["role"] == "derived"
    assert spec["layout"]["headers"] == ["supplier id", "supplier name", "country", "exposure"]
    assert "domain core" in transport.requests[0].system and "domain risk" not in transport.requests[0].system


def test_mapping_with_undecided_column_is_retried(tmp_path, workbook):
    plans = parse_regions(REGIONS, ["Suppliers", "Weights", "ReadMe"])
    partial = {**MAPPING, "columns": MAPPING["columns"][:2]}
    c, transport = client(tmp_path, [tool_response("submit_mapping", partial), tool_response("submit_mapping", MAPPING)])
    Mapper(c, Profiler(workbook), plans, run_id="r").generate(plans[0])
    assert "columns without a decision: ['Country*']" in str(transport.requests[1].messages[2])


def test_parameters_mapping(tmp_path, workbook):
    plans = parse_regions(REGIONS, ["Suppliers", "Weights", "ReadMe"])
    data = {"produces": [], "columns": [], "parameters": [
        {"name": "margin", "cell": "b1", "label_cell": "A1", "label": "Margin", "confidence": 0.9, "rationale": "m"}]}
    c, _ = client(tmp_path, [tool_response("submit_mapping", data)])
    spec = Mapper(c, Profiler(workbook), plans, run_id="r").generate(plans[1]).value
    assert spec["regions"] == [] and spec["parameters"][0]["cell"] == "B1"
    assert spec["layout"] == {"kind": "parameters", "sheet": "Weights", "labels": {"A1": "Margin"}}


def test_parameters_region_without_parameters_is_asked_again(tmp_path, workbook):
    plans = parse_regions(REGIONS, ["Suppliers", "Weights", "ReadMe"])
    empty = {"produces": [], "columns": []}
    c, transport = client(tmp_path, [tool_response("submit_mapping", empty), tool_response("submit_mapping", empty)])
    spec = Mapper(c, Profiler(workbook), plans, run_id="r").generate(plans[1]).value
    assert transport.calls == 2 and spec["parameters"] == []  # the retry may confirm there are none
    assert "returned no `parameters`" in str(transport.requests[1].messages[2])
