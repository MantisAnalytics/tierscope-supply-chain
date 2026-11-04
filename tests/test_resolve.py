import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.store import GraphStore
from scgraph.ingest.resolve import Pending, Resolver, adjudicate, normalise_name
from scgraph.llm import LLMClient

SRC = SourceLocation("ref.xlsx", "S", 1, "A")


@pytest.fixture
def resolver():
    store = GraphStore(":memory:")
    facts = []
    for sid, name, city, country in [("S861502", "Nemkan Springs & Stampings Inc.", "Lexington, KY", "USA"),
                                     ("S1", "Porben Metal Forming GmbH", "Potsdam", "Germany"),
                                     ("S2", "Porben Metal Forming GmbH", "Bremen", "Germany")]:
        facts += [Fact("entity", "Supplier", (sid,), SRC),
                  Fact("field", "Supplier", (sid,), SRC, field="name", value=name),
                  Fact("field", "Supplier", (sid,), SRC, field="city", value=city),
                  Fact("field", "Supplier", (sid,), SRC, field="country", value=country)]
    facts.append(Fact("entity", "Component", ("DMC-2221",), SRC))
    store.load(facts)
    return Resolver(store)


def test_normalise_name():
    assert normalise_name("Shangrao Gurkon Steel Products Co., Ltd.") == "shangrao gurkon steel products"
    assert normalise_name("Nemkan Springs & Stampings Inc.") == "nemkan springs stampings"


def test_exact_id_case_insensitive(resolver):
    r = resolver.resolve_local("Component", " dmc-2221 ")
    assert (r.id, r.method, r.confidence) == ("DMC-2221", "id", 1.0)


def test_entity_described_in_document_resolves_by_id(resolver):
    resolver.add("Site", "DMA-US-03", {"name": "DM Auto Menifee – Airbag Systems"})
    assert resolver.resolve_local("Site", "DM Auto Menifee - Airbag Systems").id == "DMA-US-03"


def test_name_without_legal_form(resolver):
    r = resolver.resolve_local("Supplier", "Nemkan Springs & Stampings")
    assert (r.id, r.method, r.confidence) == ("S861502", "name", 0.95)


def test_same_normalised_name_twice_needs_the_model(resolver):
    assert resolver.resolve_local("Supplier", "Porben Metal Forming") is None
    assert {c["id"] for c in resolver.candidates("Supplier", "Porben Metal Forming")} == {"S1", "S2"}


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


def test_model_chooses_by_city_and_none_is_unresolved(resolver, tmp_path):
    pending = [
        Pending("Supplier", "Porben Metal Forming", "Porben Metal Forming in Bremen ships the frames.",
                resolver.candidates("Supplier", "Porben Metal Forming")),
        Pending("Supplier", "Porbek Metals", "Porbek Metals of Lyon", resolver.candidates("Supplier", "Porbek Metals")),
        Pending("Supplier", "Zzz Unknown", "Zzz Unknown", []),
    ]
    c, transport = client(tmp_path, [tool_response("submit_resolutions", {"resolutions": [
        {"mention_id": 0, "choice": "S2", "confidence": 0.7},
        {"mention_id": 1, "choice": "none", "confidence": 0.9}]})])
    results, _ = adjudicate(c, pending)
    assert (results[0].id, results[0].method, results[0].confidence) == ("S2", "model", 0.7)
    assert results[0].record()["candidates"] == ["S1", "S2"]
    assert results[1].unresolved and results[1].id == "mention:Porbek Metals"
    assert results[2].unresolved and transport.calls == 1  # no candidates: not asked
    assert "Bremen" in transport.requests[0].messages[0]["content"]


def test_invalid_choice_is_retried(resolver, tmp_path):
    pending = [Pending("Supplier", "Porben", "Porben", resolver.candidates("Supplier", "Porben Metal Forming"))]
    c, transport = client(tmp_path, [
        tool_response("submit_resolutions", {"resolutions": [{"mention_id": 0, "choice": "S9", "confidence": 1}]}),
        tool_response("submit_resolutions", {"resolutions": [{"mention_id": 0, "choice": "S1", "confidence": 0.6}]})])
    results, _ = adjudicate(c, pending)
    assert results[0].id == "S1" and transport.calls == 2
