"""Business Exposure, adjusted exposure, Business Impact and impacted assemblies, from the current graph.

The rules reproduce the customer data-capture template's formulas.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import networkx as nx

from scgraph.graph.store import GraphStore
from scgraph.graph.view import build_view

EXPOSED_TYPES = ("Component", "Site", "Supplier")
DEFAULT_SINGLE_SOURCE_FACTOR = 1.0


class ExposureError(ValueError):
    pass


@dataclass(frozen=True)
class Summary:
    type: str
    id: str
    business_exposure_pct: float
    assemblies: tuple[str, ...]
    components: tuple[str, ...]
    adjusted_pct: float | None = None  # components only
    recovery_time_months: float | None = None  # suppliers only
    business_impact_eur: float | None = None  # suppliers only; None when unavailable


class Exposure:
    def __init__(self, graph: nx.MultiDiGraph, parameters: dict[str, Any]) -> None:
        self.graph = graph
        self.parameters = parameters
        self._assemblies: dict[str, set[str]] = defaultdict(set)  # component -> assemblies
        self._bom: dict[str, nx.DiGraph] = defaultdict(nx.DiGraph)  # assembly -> parent/child graph
        self._delivered: dict[str, list[str]] = defaultdict(list)  # site -> components
        self._supplied: dict[str, list[str]] = defaultdict(list)  # supplier -> components
        self._sites_of: dict[str, list[str]] = defaultdict(list)  # component -> sites
        for (_, src), (_, dst), data in graph.edges(data=True):
            kind = data["type"]
            if kind == "CONTAINS":
                assembly = data["identity"]["assembly"]
                self._assemblies[src].add(assembly)
                self._assemblies[dst].add(assembly)
                self._bom[assembly].add_edge(src, dst)
            elif kind == "DELIVERED_TO":
                self._delivered[dst].append(src)
                self._sites_of[src].append(dst)
            elif kind == "SUPPLIES":
                self._supplied[src].append(dst)
        self.revenue = {
            node_id: float(data["fields"].get("revenue_eur") or 0.0)
            for (type_name, node_id), data in graph.nodes(data=True)
            if type_name == "Assembly"
        }
        self.total_revenue = sum(self.revenue.values())

    @classmethod
    def from_store(cls, store: GraphStore) -> Exposure:
        return cls(build_view(store), {name: p.value for name, p in store.parameters().items()})

    def _fields(self, type_name: str, node_id: str) -> dict[str, Any]:
        node = self.graph.nodes.get((type_name, node_id))
        if node is None:
            raise ExposureError(f"no {type_name} {node_id!r} in the graph")
        return node["fields"]

    def ids(self, type_name: str) -> list[str]:
        return sorted(n[1] for n in self.graph.nodes if n[0] == type_name)

    def components(self, type_name: str, node_id: str) -> list[str]:
        """Components through which an entity feeds assemblies."""
        if type_name not in EXPOSED_TYPES:
            raise ExposureError(f"exposure is defined for {', '.join(EXPOSED_TYPES)}, not {type_name}")
        self._fields(type_name, node_id)
        if type_name == "Component":
            return [node_id]
        index = self._delivered if type_name == "Site" else self._supplied
        return sorted(set(index.get(node_id, [])))

    def impacted_assemblies(self, type_name: str, node_id: str) -> set[str]:
        return {a for c in self.components(type_name, node_id) for a in self._assemblies.get(c, ())}

    def bom_paths(self, assembly: str, component: str) -> list[list[str]]:
        """Paths from the assembly's root component down to `component` in that assembly's BOM."""
        root = self.graph.nodes.get(("Assembly", assembly), {}).get("root")
        bom = self._bom.get(assembly)
        if root is None or bom is None or component not in bom:
            return []
        if component == root:
            return [[root]]
        return sorted(nx.all_simple_paths(bom, root, component))

    def impacted_with_paths(self, type_name: str, node_id: str) -> dict[str, list[list[str]]]:
        """Assembly -> BOM paths from its root to each of the entity's components it contains."""
        out: dict[str, list[list[str]]] = {}
        for c in self.components(type_name, node_id):
            for a in sorted(self._assemblies.get(c, ())):
                out.setdefault(a, []).extend(self.bom_paths(a, c))
        return dict(sorted(out.items()))

    def business_exposure(self, type_name: str, node_id: str) -> float:
        if not self.total_revenue:
            raise ExposureError("total assembly revenue is zero; load Assembly.revenue_eur")
        assemblies = self.impacted_assemblies(type_name, node_id)
        return sum(self.revenue.get(a, 0.0) for a in assemblies) / self.total_revenue * 100

    def single_source_factor(self, component: str) -> float:
        flag = self._fields("Component", component).get("single_source")
        name = {True: "yes", False: "no"}.get(flag, "missing")
        return float(self.parameters.get(f"single_source_factor.{name}", DEFAULT_SINGLE_SOURCE_FACTOR))

    def adjusted_exposure(self, component: str) -> float:
        return self.business_exposure("Component", component) * self.single_source_factor(component)

    def recovery_time(self, supplier: str) -> float | None:
        """Max recovery time of the sites the supplier's components are delivered to; None if none has one."""
        times = [
            self._fields("Site", site).get("recovery_time_months")
            for c in self.components("Supplier", supplier)
            for site in self._sites_of.get(c, [])
        ]
        times = [t for t in times if t is not None]
        return max(times) if times else None

    def business_impact(self, supplier: str) -> float | None:
        """BE/100 x total revenue x margin x recovery months / 12; None when recovery time or margin is unknown."""
        months = self.recovery_time(supplier)
        margin = self.parameters.get("margin")
        if months is None or margin is None:
            return None
        return self.business_exposure("Supplier", supplier) / 100 * self.total_revenue * margin * months / 12

    def summary(self, type_name: str, node_id: str) -> Summary:
        extra: dict[str, Any] = {}
        if type_name == "Component":
            extra["adjusted_pct"] = self.adjusted_exposure(node_id)
        if type_name == "Supplier":
            extra["recovery_time_months"] = self.recovery_time(node_id)
            extra["business_impact_eur"] = self.business_impact(node_id)
        return Summary(
            type=type_name,
            id=node_id,
            business_exposure_pct=self.business_exposure(type_name, node_id),
            assemblies=tuple(sorted(self.impacted_assemblies(type_name, node_id))),
            components=tuple(self.components(type_name, node_id)),
            **extra,
        )

    def ranking(self, type_name: str) -> list[Summary]:
        """All entities of a type by descending exposure, then id."""
        summaries = [self.summary(type_name, i) for i in self.ids(type_name)]
        return sorted(summaries, key=lambda s: (-s.business_exposure_pct, s.id))
