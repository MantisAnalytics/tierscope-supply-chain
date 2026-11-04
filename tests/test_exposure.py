
from scgraph.exposure.compute import Exposure
from scgraph.graph.facts import Fact, Parameter, SourceLocation
from scgraph.graph.store import GraphStore

SRC = SourceLocation(file="t.xlsx", sheet="S", row=1, column="A")


def small_graph() -> GraphStore:
    def f(kind, type_name, key, field=None, value=None):
        return Fact(kind, type_name, key, SRC, field=field, value=value)

    store = GraphStore(":memory:")
    store.load(
        [
            f("entity", "Assembly", ("A1",)), f("field", "Assembly", ("A1",), "revenue_eur", 300.0),
            f("entity", "Assembly", ("A2",)), f("field", "Assembly", ("A2",), "revenue_eur", 100.0),
            f("entity", "Component", ("R1",)), f("field", "Component", ("R1",), "part_number", "A1"),
            f("entity", "Component", ("R2",)), f("field", "Component", ("R2",), "part_number", "A2"),
            f("entity", "Component", ("C",)),
            f("entity", "Component", ("D",)),
            f("relation", "CONTAINS", ("R1", "C", "A1")),
            f("relation", "CONTAINS", ("R2", "D", "A2")),
            f("entity", "Supplier", ("S1",)),
            f("entity", "Site", ("X",)),
            f("relation", "SUPPLIES", ("S1", "C", "X")),
            f("relation", "SUPPLIES", ("S1", "D", "X")),
            f("relation", "DELIVERED_TO", ("C", "X")),
        ],
        parameters=[Parameter("margin", 0.2, SRC)],
    )
    return store


def test_graph_change_changes_exposure():
    store = small_graph()
    assert Exposure.from_store(store).business_exposure("Supplier", "S1") == 100.0
    store.remove_edge("SUPPLIES", ("S1", "D", "X"))
    assert Exposure.from_store(store).business_exposure("Supplier", "S1") == 75.0


def test_missing_single_source_flag_uses_factor_one():
    exposure = Exposure.from_store(small_graph())
    assert exposure.single_source_factor("C") == 1.0
    assert exposure.adjusted_exposure("C") == exposure.business_exposure("Component", "C") == 75.0
