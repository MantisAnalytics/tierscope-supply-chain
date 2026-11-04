import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.graph.facts import SourceLocation
from scgraph.ingest.step import StepError
from scgraph.ingest.textfields import (Cell, ColumnReport, apply_parser, extract_leftovers, generate_parser,
                                       parse_column, parse_parser)
from scgraph.llm import LLMClient

NOTES = [
    "Confidence: Medium | Supplies: Polyester yarn / polymer chips | Notes: Upstream relationship identified "
    "from public / trade-record sources; entity details withheld for anonymization.",
    "Confidence: High | Risk flags: CRITICAL_MATERIAL, SINGLE_SOURCE | Supplies: Nylon raw materials | Notes: x",
    "Corporate HQ - not a production site. | Confidence: Low | Risk flags: CRITICAL_MATERIAL, RELATED_PARTY | "
    "Supplies: Flat-rolled steel | Notes: y",
]
KV = {"kind": "kv", "item_sep": "|", "kv_sep": ":",
      "keys": {"Confidence": "PARENT_SUPPLIER.stated_confidence", "Supplies": "Supplier.supplies_material",
               "Risk flags": {"target": "RiskIndicator.flag", "list_sep": ","}},
      "ignore_keys": ["Notes"]}


def test_kv_parser_on_reference_samples():
    parser = parse_parser(KV)
    values, complete = apply_parser(parser, NOTES[1])
    assert complete
    assert values == {"PARENT_SUPPLIER.stated_confidence": ["High"],
                      "RiskIndicator.flag": ["CRITICAL_MATERIAL", "SINGLE_SOURCE"],
                      "Supplier.supplies_material": ["Nylon raw materials"]}
    assert apply_parser(parser, NOTES[0]) == ({"PARENT_SUPPLIER.stated_confidence": ["Medium"],
                                                "Supplier.supplies_material": ["Polyester yarn / polymer chips"]},
                                               True)
    values, complete = apply_parser(parser, NOTES[2])  # a free-text item the parser cannot place
    assert not complete and values["Supplier.supplies_material"] == ["Flat-rolled steel"]


def test_regex_parser():
    parser = parse_parser({"kind": "regex", "patterns": [
        {"pattern": r"Confidence: (?P<c>\w+)", "targets": {"c": "PARENT_SUPPLIER.stated_confidence"}},
        {"pattern": r"Supplies: (?P<s>[^|]+)", "targets": {"s": "Supplier.supplies_material"}, "required": False},
    ]})
    assert apply_parser(parser, NOTES[1]) == ({"PARENT_SUPPLIER.stated_confidence": ["High"],
                                                "Supplier.supplies_material": ["Nylon raw materials"]}, True)
    assert apply_parser(parser, "no structure here") == ({}, False)


@pytest.mark.parametrize("bad", [
    {"kind": "kv", "item_sep": "|", "kv_sep": ":", "keys": {"X": "Supplier.colour"}},
    {"kind": "regex", "patterns": [{"pattern": "(", "targets": {}}]},
    {"kind": "regex", "patterns": [{"pattern": "(?P<a>x)", "targets": {"b": "Supplier.name"}}]},
    {"kind": "python", "code": "import os"},
])
def test_invalid_parsers_rejected(bad):
    with pytest.raises(ValueError):
        parse_parser(bad)


def cells():
    keys = [{"Supplier": ("T2-1",), "PARENT_SUPPLIER": ("T2-1", "S1")},
            {"Supplier": ("T2-2",), "PARENT_SUPPLIER": ("T2-2", "S2")},
            {"Supplier": ("T2-3",), "PARENT_SUPPLIER": ("T2-3", "S3")}]
    return [Cell("suppliers", 2 + i, text, SourceLocation("f.xlsx", "Suppliers", 2 + i, "M"), keys[i], ORIGIN)
            for i, text in enumerate(NOTES)]


ORIGIN = {"spec": "gen/f__suppliers.json", "region": "suppliers", "column": "Risk Notes"}


def test_coverage_caps_parser_confidence_and_keeps_leftovers():
    report = ColumnReport("suppliers", "Risk Notes")
    facts, leftovers = parse_column(parse_parser(KV), cells(), column_confidence=0.9, run_id="r", report=report)
    assert (report.cells, report.parsed) == (3, 2) and round(report.coverage, 4) == 0.6667
    assert {f.confidence for f in facts} == {0.6667} and {f.method for f in facts} == {"parser"}
    assert [c.row for c in leftovers] == [4]
    flags = [f.key for f in facts if f.type == "RiskIndicator" and f.kind == "entity"]
    assert flags == [("T2-2", "flag", "CRITICAL_MATERIAL"), ("T2-2", "flag", "SINGLE_SOURCE")]
    stated = [f for f in facts if f.field == "stated_confidence"]
    assert [(f.key, f.value) for f in stated] == [(("T2-1", "S1"), "Medium"), (("T2-2", "S2"), "High")]
    for f in facts:
        f.validate()
    assert all(f.origin == ORIGIN for f in facts)  # parsed values are reviewed with their text column


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


def test_parser_generation_checks_samples(tmp_path):
    weak = {"kind": "kv", "item_sep": ";", "kv_sep": "=", "keys": {"Supplies": "Supplier.supplies_material"}}
    c, transport = client(tmp_path, [tool_response("submit_parser", weak), tool_response("submit_parser", KV)])
    targets = ["Supplier.supplies_material", "PARENT_SUPPLIER.stated_confidence", "RiskIndicator.flag"]
    parser = generate_parser(c, "Risk Notes", targets, NOTES).value
    assert parser.kind == "kv" and transport.calls == 2
    assert "fully parses only 0%" in str(transport.requests[1].messages[2])


def test_leftover_with_correct_quote_is_loaded_with_method_llm(tmp_path):
    leftover = cells()[2]
    answer = {"cells": [{"id": 0, "fields": [
        {"target": "Supplier.supplies_material", "value": "Flat-rolled steel", "quote": "Supplies: Flat-rolled steel",
         "confidence": 0.85}]}]}
    c, _ = client(tmp_path, [tool_response("submit_fields", answer)])
    report = ColumnReport("suppliers", "Risk Notes")
    facts, _ = extract_leftovers(c, [leftover], ["Supplier.supplies_material"], run_id="r", report=report)
    [fact] = facts
    assert (fact.method, fact.confidence, fact.value, fact.source.row) == ("llm", 0.85, "Flat-rolled steel", 4)
    assert fact.origin == ORIGIN
    assert report.llm_loaded == 1 and report.unparsed == []


def test_hallucinated_quote_rejected_and_cell_listed_unparsed(tmp_path):
    leftover = cells()[2]
    answer = {"cells": [{"id": 0, "fields": [
        {"target": "Supplier.supplies_material", "value": "Aluminium", "quote": "Supplies: Aluminium",
         "confidence": 0.9}]}]}
    c, _ = client(tmp_path, [tool_response("submit_fields", answer)])
    report = ColumnReport("suppliers", "Risk Notes")
    facts, _ = extract_leftovers(c, [leftover], ["Supplier.supplies_material"], run_id="r", report=report)
    assert facts == [] and report.rejected_quotes == 1
    assert report.unparsed == ["f.xlsx Suppliers!M4"]


def test_failed_parser_generation(tmp_path):
    c, _ = client(tmp_path, [tool_response("submit_parser", {"kind": "python"})] * 2)
    with pytest.raises(StepError):
        generate_parser(c, "Risk Notes", ["Supplier.supplies_material"], NOTES)


def test_regex_list_targets_and_flag_list_sep_required():
    parser = parse_parser({"kind": "regex", "patterns": [
        {"pattern": r"Risk flags: (?P<f>[^|]+)", "targets": {"f": {"target": "RiskIndicator.flag", "list_sep": ","}}},
    ]})
    assert apply_parser(parser, NOTES[1])[0] == {"RiskIndicator.flag": ["CRITICAL_MATERIAL", "SINGLE_SOURCE"]}
    with pytest.raises(ValueError, match="list_sep"):
        parse_parser({"kind": "kv", "item_sep": "|", "kv_sep": ":", "keys": {"Risk flags": "RiskIndicator.flag"}})


def test_parser_limited_to_given_targets(tmp_path):
    raw = {**KV, "keys": {**KV["keys"], "Notes": "Supplier.risk_notes"}, "ignore_keys": []}
    c, transport = client(tmp_path, [tool_response("submit_parser", raw), tool_response("submit_parser", KV)])
    generate_parser(c, "Risk Notes", ["Supplier.supplies_material", "PARENT_SUPPLIER.stated_confidence",
                                      "RiskIndicator.flag"], NOTES)
    assert "Supplier.risk_notes" in str(transport.requests[1].messages[2])
