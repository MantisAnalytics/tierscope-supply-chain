import pytest

from fakes import FakeTransport, make_config, text_response, tool_response
from scgraph.ingest.documents import TextBlock, chunks, read_document
from scgraph.ingest.step import StepError
from scgraph.ingest.textextract import (DocClass, RawFact, classify_document, extract_chunk, parse_facts, typed_value,
                                        verify)
from scgraph.llm import LLMClient

TEXT = """Suppliers

Supplier S154292 is Nemkan Springs & Stampings Inc. Its city is Lexington, KY.

Component DMC-2221 (INFLATOR, SPI2 EVO V686 XAN) has DM part number DMP1. Annual spend is 1,234,567.5. It is single-sourced. It is supplied by Nemkan Springs & Stampings Inc. for delivery to DM Auto Menifee – Airbag Systems.
"""


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "r.txt"
    path.write_text(TEXT)
    return read_document(path)


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


FACTS = {"facts": [
    {"kind": "entity", "type": "Supplier", "mention": "S154292", "quote": "Supplier S154292 is Nemkan", "confidence": 1},
    {"kind": "field", "type": "Supplier", "mention": "S154292", "field": "name",
     "value": "Nemkan Springs & Stampings Inc.", "quote": "Supplier S154292 is Nemkan Springs & Stampings Inc.",
     "confidence": 0.95},
    {"kind": "relation", "type": "SUPPLIES", "from": "Nemkan Springs & Stampings Inc.", "to": "DMC-2221",
     "identity": {"site": "DM Auto Menifee – Airbag Systems"},
     "quote": "It is supplied by Nemkan Springs & Stampings Inc. for delivery to DM Auto Menifee – Airbag Systems.",
     "confidence": 0.9},
    {"kind": "relation", "type": "DELIVERED_TO", "from": "DMC-2221", "to": "DM Auto Menifee – Airbag Systems",
     "quote": "for delivery to DM Auto Menifee – Airbag Systems", "confidence": 0.9},
    {"kind": "field", "type": "Supplier", "mention": "S154292", "field": "colour", "value": "red",
     "quote": "x", "confidence": 1},
]}


def test_classification(tmp_path, doc):
    c, transport = client(tmp_path, [tool_response("submit_document", {
        "domains": ["core", "tiers"], "types": ["Supplier", "PARENT_SUPPLIER"], "summary": "supplier report"})])
    result = classify_document(c, doc).value
    assert result.domains == ["core", "tiers"] and result.types == ["Supplier", "PARENT_SUPPLIER"]
    assert "Suppliers" in transport.requests[0].messages[0]["content"]


def test_extraction_parsed_into_raw_facts(tmp_path, doc):
    c, transport = client(tmp_path, [tool_response("submit_facts", FACTS)])
    [chunk] = chunks(doc.blocks[0])
    result = extract_chunk(c, chunk, DocClass(["core"], ["Supplier", "SUPPLIES", "DELIVERED_TO"])).value
    assert [(f.kind, f.type) for f in result.facts] == [("entity", "Supplier"), ("field", "Supplier"),
                                                        ("relation", "SUPPLIES"), ("relation", "DELIVERED_TO")]
    supplies = result.facts[2]
    assert (supplies.src, supplies.dst, supplies.identity) == ("Nemkan Springs & Stampings Inc.", "DMC-2221",
                                                               {"site": "DM Auto Menifee – Airbag Systems"})
    assert len(result.invalid) == 1 and "colour" in result.invalid[0]
    system = transport.requests[0].system
    assert "domain core" in system and "domain risk" not in system


def test_chunk_failing_twice_raises_for_the_caller(tmp_path, doc):
    c, _ = client(tmp_path, [text_response("no"), tool_response("submit_facts", {"facts": "none"})])
    with pytest.raises(StepError):
        extract_chunk(c, chunks(doc.blocks[0])[0], DocClass(["core"], ["Supplier"]))


def raw(**kw):
    defaults = {"kind": "field", "type": "Component", "mention": "DMC-2221", "confidence": 0.9}
    return RawFact(**{**defaults, **kw})


def test_quote_absent_is_rejected(doc):
    chunk = chunks(doc.blocks[0])[0]
    assert not verify(raw(field="description", value="BALL", quote="Component DMC-2221 is a ball."), chunk, doc)


def test_value_missing_from_quote_caps_confidence_and_flags(doc):
    chunk = chunks(doc.blocks[0])[0]
    fact = raw(field="part_number", value="DMP9", quote="has DM part number DMP1")
    assert verify(fact, chunk, doc) and fact.flagged and fact.confidence == 0.5
    assert fact.source.lines == (5, 5) and fact.source.quote == "has DM part number DMP1"


def test_number_formatting_differences_are_not_flagged(doc):
    chunk = chunks(doc.blocks[0])[0]
    fact = raw(field="annual_spend", value="1234567.50", quote="Annual spend is 1,234,567.5.")
    assert verify(fact, chunk, doc) and not fact.flagged and fact.confidence == 0.9
    assert typed_value(fact) == 1234567.5
    single = raw(field="single_source", value="true", quote="It is single-sourced.")
    assert verify(single, chunk, doc) and not single.flagged and typed_value(single) is True


def test_whole_chunk_of_invalid_facts_retries():
    with pytest.raises(ValueError, match="colour"):
        parse_facts({"facts": [FACTS["facts"][4]]}, {"Supplier"})
    assert parse_facts({"facts": []}, {"Supplier"}).facts == []


def test_mostly_invalid_chunk_is_retried_and_the_retry_keeps_valid_facts(tmp_path, doc):
    bad = {"facts": [{"kind": "field", "type": "part_number", "mention": "DMC-2221", "value": "DMP1",
                      "quote": "DMP1", "confidence": 1}] * 3 + FACTS["facts"][:1]}
    second = {"facts": json_list(FACTS["facts"][:2] + bad["facts"][:1])}
    c, transport = client(tmp_path, [tool_response("submit_facts", bad, id="t1"), tool_response("submit_facts", second)])
    result = extract_chunk(c, chunks(doc.blocks[0])[0], DocClass(["core"], [])).value
    assert transport.calls == 2 and "3 of 4 facts do not fit" in str(transport.requests[1].messages[2])
    assert len(result.facts) == 2 and len(result.invalid) == 1


def json_list(items):
    import json

    return json.dumps(items)  # some replies send the list as a JSON string
