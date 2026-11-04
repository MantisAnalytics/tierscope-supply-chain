import json

import pytest

from scgraph.graph.facts import Fact, FactError, SourceLocation
from scgraph.graph.schema import SCHEMA

SRC = SourceLocation(file="f.xlsx", sheet="Suppliers", row=2, column="F")


def test_schema_lists_three_domains_with_typed_fields():
    described = SCHEMA.describe()
    assert list(described) == ["core", "tiers", "risk"]
    core = described["core"]
    assert set(core["entities"]) == {"Site", "Supplier", "Component", "Assembly"}
    assert set(core["relations"]) == {"CONTAINS", "SUPPLIES", "DELIVERED_TO"}
    assert core["entities"]["Supplier"]["fields"]["tier"] == "int"
    assert core["relations"]["CONTAINS"]["identity"] == {"assembly": "Assembly"}
    assert described["tiers"]["relations"]["PARENT_SUPPLIER"]["from"] == "Supplier"
    assert described["risk"]["entities"]["RiskIndicator"]["identity"] == ["supplier", "category", "indicator"]


@pytest.mark.parametrize(
    ("fact", "name"),
    [
        (Fact("entity", "Vendor", ("V1",), SRC), "Vendor"),
        (Fact("field", "Supplier", ("S1",), SRC, field="colour", value="red"), "colour"),
        (Fact("relation", "BUYS_FROM", ("A", "B"), SRC), "BUYS_FROM"),
    ],
)
def test_unknown_type_or_field_names_it(fact, name):
    with pytest.raises(FactError, match=name):
        fact.validate()


def test_field_value_type_checked():
    with pytest.raises(FactError, match="tier"):
        Fact("field", "Supplier", ("S1",), SRC, field="tier", value="one").validate()


def test_relation_key_shape():
    Fact("relation", "SUPPLIES", ("S1", "C1", None), SRC).validate()  # site is optional
    with pytest.raises(FactError, match="assembly"):
        Fact("relation", "CONTAINS", ("C1", "C2", None), SRC).validate()


def test_valid_fact_round_trips_through_json():
    fact = Fact("field", "Supplier", ("S1",), SRC, field="country", value="Germany", confidence=0.9, run_id="r1")
    fact.validate()
    again = Fact.from_dict(json.loads(json.dumps(fact.to_dict())))
    assert again == fact


def test_fact_without_source_rejected():
    with pytest.raises(FactError, match="no source"):
        Fact("entity", "Supplier", ("S1",), None).validate()


def test_confidence_out_of_range_rejected():
    with pytest.raises(FactError, match="confidence"):
        Fact("entity", "Supplier", ("S1",), SRC, confidence=1.5).validate()


def test_text_source_round_trip():
    src = SourceLocation(file="notes.pdf", page=3, span=(10, 42))
    assert SourceLocation.from_dict(json.loads(json.dumps(src.to_dict()))) == src


def test_text_field_parsing_schema_additions():
    from scgraph.graph.schema import FLAG_CATEGORY

    described = SCHEMA.describe()
    assert described["core"]["entities"]["Supplier"]["fields"]["supplies_material"] == "str"
    assert described["tiers"]["relations"]["PARENT_SUPPLIER"]["fields"] == {"stated_confidence": "str"}
    Fact("field", "PARENT_SUPPLIER", ("T2-DM001", "S1"), SRC, field="stated_confidence", value="Medium").validate()
    flag = ("T2-DM001", FLAG_CATEGORY, "CRITICAL_MATERIAL")
    Fact("field", "RiskIndicator", flag, SRC, field="value", value=True).validate()
    Fact("relation", "HAS_RISK", ("T2-DM001", "::".join(flag)), SRC).validate()
