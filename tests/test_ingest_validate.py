import copy

import openpyxl
import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.ingest.classify import parse_regions
from scgraph.ingest.mapgen import Mapper
from scgraph.ingest.profile import Profiler
from scgraph.ingest.validate import apply_confidence, column_confidence, dry_run, validate_and_repair
from scgraph.llm import LLMClient


def test_confidence_capped_by_evidence():
    assert column_confidence(0.95, 0.70) == 0.70
    assert column_confidence(0.6, 0.9) == 0.6
    assert column_confidence(1.0, duplicates=True) == 0.8
    assert column_confidence(0.9, resolved_share=0.5) == 0.45


@pytest.fixture
def components(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Components"
    ws.append(["Component ID", "Spend", "Supplier ID"])
    spends = [100, 200, 300, 400, 500, 600, 700, "n/a", "?", "-"]  # 70% numeric
    for i, spend in enumerate(spends):
        ws.append([f"C{i}", spend, "S1" if i < 9 else "S9"])
    sup = wb.create_sheet("Suppliers")
    sup.append(["Supplier ID", "Name"])
    sup.append(["S1", "Acme"])
    path = tmp_path / "c.xlsx"
    wb.save(path)
    return path


REGIONS = {"regions": [
    {"id": "components", "sheet": "Components", "kind": "data", "header_row": 1, "start_row": 2,
     "describes": "Component", "domains": ["core"], "reason": "r"},
    {"id": "suppliers", "sheet": "Suppliers", "kind": "data", "header_row": 1, "start_row": 2,
     "describes": "Supplier", "domains": ["core"], "reason": "r"},
]}


def mapping(spend_transform=None):
    spend = {"header": "Spend", "role": "fact", "target": "Component.annual_spend", "confidence": 0.95,
             "rationale": "spend"}
    if spend_transform:
        spend["transform"] = spend_transform
    return {
        "produces": [{"entity": "Component", "key": {"column": "Component ID"}},
                     {"relation": "SUPPLIES", "from": {"column": "Supplier ID"}, "to": {"column": "Component ID"}}],
        "columns": [{"header": "Component ID", "role": "key", "confidence": 1.0, "rationale": "id"},
                    {"header": "Supplier ID", "role": "key", "confidence": 1.0, "rationale": "supplier"}, spend],
    }


SUPPLIERS = {"produces": [{"entity": "Supplier", "key": {"column": "Supplier ID"}}],
             "columns": [{"header": "Supplier ID", "role": "key", "confidence": 1.0, "rationale": "id"},
                         {"header": "Name", "role": "fact", "target": "Supplier.name", "confidence": 1.0,
                          "rationale": "n"}]}


def build(tmp_path, path, script):
    transport = FakeTransport(script)
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport)
    plans = parse_regions(REGIONS, ["Components", "Suppliers"])
    profiler = Profiler(path)
    return Mapper(client, profiler, plans, run_id="r"), {p.id: p for p in plans}, profiler, transport


def test_findings_and_capped_confidence(tmp_path, components):
    mapper, plans, profiler, _ = build(tmp_path, components, [])
    specs = {"components": mapper.build(plans["components"], mapping()),
             "suppliers": mapper.build(plans["suppliers"], SUPPLIERS)}
    _, findings = dry_run(specs, profiler.table)
    f = findings["components"]
    assert (f.failures["Spend"].bad, f.failures["Spend"].total) == (3, 10)
    assert (f.references["Supplier ID"].bad, f.references["Supplier ID"].total) == (1, 10)
    assert f.references["Supplier ID"].samples == ["S9"]
    spec = apply_confidence(specs["components"], f)
    region = spec["regions"][0]
    assert region["columns"]["Spend"]["confidence"] <= 0.70
    assert region["columns"]["Spend"]["model_confidence"] == 0.95
    assert region["key_columns"]["Supplier ID"]["confidence"] == 0.9
    assert region["confidence"] == 0.9


def test_repair_keeps_the_fixed_spec(tmp_path, components):
    fixed = mapping([{"empty": ["n/a", "?", "-"]}, "number"])
    mapper, plans, profiler, transport = build(tmp_path, components, [tool_response("submit_mapping", fixed)])
    bad = mapper.build(plans["components"], mapping("date"))
    specs = {"components": bad, "suppliers": mapper.build(plans["suppliers"], SUPPLIERS)}
    validated = validate_and_repair(mapper, plans, specs, profiler.table)
    # Round 1 fixes the transform; the unresolved S9 remains, round 2's call fails and changes nothing.
    assert transport.calls == 3 and validated.repaired == {"components": 1}
    column = validated.specs["components"]["regions"][0]["columns"]["Spend"]
    assert column["transform"] == [{"empty": ["n/a", "?", "-"]}, "number"]
    assert validated.findings["components"].failures["Spend"].bad == 0
    assert "findings" in transport.requests[0].messages[0]["content"]


def test_repair_that_gets_worse_keeps_the_better_original(tmp_path, components):
    worse = mapping("date")
    mapper, plans, profiler, transport = build(
        tmp_path, components, [tool_response("submit_mapping", worse), tool_response("submit_mapping", worse)])
    original = mapper.build(plans["components"], mapping())
    specs = {"components": copy.deepcopy(original), "suppliers": mapper.build(plans["suppliers"], SUPPLIERS)}
    validated = validate_and_repair(mapper, plans, specs, profiler.table)
    assert transport.calls == 2  # two rounds, both worse
    assert "transform" not in validated.specs["components"]["regions"][0]["columns"]["Spend"]
    assert validated.flagged()["components"]  # problems remain and are reported


def test_list_valued_keys_are_a_problem(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Assemblies"
    for r in [["Assembly ID", "Revenue"], ["A1", 1.0], ["A2", 2.0]]:
        ws.append(r)
    bu = wb.create_sheet("BU")
    for r in [["BU", "Assembly ID"], ["RI", "A1, A2"]]:
        bu.append(r)
    path = tmp_path / "a.xlsx"
    wb.save(path)
    regions = {"regions": [
        {"id": "assemblies", "sheet": "Assemblies", "kind": "data", "header_row": 1, "start_row": 2,
         "describes": "Assembly", "domains": ["core"], "reason": "r"},
        {"id": "bu", "sheet": "BU", "kind": "data", "header_row": 1, "start_row": 2, "describes": "Assembly",
         "domains": ["core"], "reason": "r"}]}
    transport = FakeTransport()
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport)
    plans = parse_regions(regions, ["Assemblies", "BU"])
    mapper = Mapper(client, Profiler(path), plans, run_id="r")
    key = {"entity": "Assembly", "key": {"column": "Assembly ID"}}
    specs = {"assemblies": mapper.build(plans[0], {"produces": [key], "columns": [
                 {"header": "Assembly ID", "role": "key", "confidence": 1, "rationale": "id"},
                 {"header": "Revenue", "role": "fact", "target": "Assembly.revenue_eur", "confidence": 1,
                  "rationale": "r"}]}),
             "bu": mapper.build(plans[1], {"produces": [key], "columns": [
                 {"header": "Assembly ID", "role": "key", "confidence": 1, "rationale": "id"},
                 {"header": "BU", "role": "fact", "target": "Assembly.business_unit", "confidence": 1,
                  "rationale": "bu"}]})}
    _, findings = dry_run(specs, Profiler(path).table)
    assert findings["bu"].list_keys == ["A1, A2"] and "lists of ids" in findings["bu"].problems()[0]
    assert findings["assemblies"].problems() == []
