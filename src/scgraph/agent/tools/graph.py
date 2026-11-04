"""Read-only graph tools: find entities, node details with provenance, impacted assemblies, single-source
components, exposure rankings and paths between nodes. All read the current graph under the world's lock."""

from __future__ import annotations

from typing import Any

import networkx as nx

from scgraph.agent.tools import Tool, Turn, obj
from scgraph.exposure.compute import EXPOSED_TYPES, ExposureError
from scgraph.graph.schema import SCHEMA
from scgraph.graph.store import GraphStore

NAME_FIELDS = {"Supplier": "name", "Site": "name", "Component": "description", "Assembly": "business_unit"}
TYPES = ("Supplier", "Component", "Site", "Assembly", "RiskIndicator")
MAX_ROWS = 25
MAX_NEIGHBOURS = 8
NODE = {"id": {"type": "string", "description": "node id, e.g. S523036, DMC-2221, P01"},
        "type": {"type": "string", "enum": list(TYPES), "description": "node type when the id is ambiguous"}}


class GraphTools:
    def __init__(self, turn: Turn) -> None:
        self.world = turn.world
        self.store: GraphStore = turn.world.graph()

    def tools(self) -> list[Tool]:
        return [
            Tool("find_entities", "Find suppliers, components, sites or assemblies whose id or name contains the "
                 "text (case-insensitive).", obj({"text": {"type": "string"}, "type": NODE["type"]}, ["text"]),
                 self.find_entities),
            Tool("node_info", "One node: its fields with their source (file, sheet, cell or quote), its neighbours "
                 "by relation, and its Business Exposure and Impact.", obj(NODE, ["id"]), self.node_info),
            Tool("impacted_assemblies", "Assemblies that depend on a supplier, site or component, with their "
                 "revenue and the components that link them.", obj(NODE, ["id"]), self.impacted_assemblies),
            Tool("single_source", "Single-source components, optionally of suppliers in one country, of one "
                 "commodity or of one supplier.", obj({"country": {"type": "string"},
                                                       "commodity": {"type": "string"},
                                                       "supplier": {"type": "string"}}), self.single_source),
            Tool("exposure_by", "Suppliers, sites or components ranked by Business Exposure (and suppliers by "
                 "Business Impact), optionally only those in one country.",
                 obj({"kind": {"type": "string", "enum": ["supplier", "site", "component"]},
                      "country": {"type": "string"}, "top": {"type": "integer"}}, ["kind"]), self.exposure_by),
            Tool("path_between", "The shortest chain of relations between two nodes.",
                 obj({"from": {"type": "string"}, "to": {"type": "string"}}, ["from", "to"]), self.path_between),
        ]

    # helpers -------------------------------------------------------------------------------------------------

    def _base(self):
        from scgraph.review.preview import _base

        return _base(self.store)

    @staticmethod
    def _fields(g: nx.MultiDiGraph, node: tuple[str, str]) -> dict[str, Any]:
        return g.nodes[node]["fields"]

    def _name(self, g: nx.MultiDiGraph, node: tuple[str, str]) -> str | None:
        return self._fields(g, node).get(NAME_FIELDS.get(node[0], "name"))

    def _resolve(self, g: nx.MultiDiGraph, node_id: str, type_name: str | None = None) -> tuple[str, str]:
        node_id = str(node_id).strip()
        found = [n for n in g if n[1] == node_id and (type_name is None or n[0] == type_name)]
        if not found:
            folded = node_id.casefold()
            found = [n for n in g if str(self._name(g, n) or "").casefold() == folded
                     and (type_name is None or n[0] == type_name)]
        if not found:
            raise ValueError(f"no node {node_id!r} in the graph; use find_entities")
        if len(found) > 1:
            raise ValueError(f"{node_id!r} names {', '.join(f'{t} {i}' for t, i in found)}; give the type")
        return found[0]

    def _supplier_of(self, g: nx.MultiDiGraph, component: str) -> list[str]:
        return sorted({u[1] for u, _, data in g.in_edges(("Component", component), data=True)
                       if data["type"] == "SUPPLIES"})

    def _country(self, g: nx.MultiDiGraph, node: tuple[str, str]) -> str | None:
        if node[0] == "Component":
            countries = {self._fields(g, ("Supplier", s)).get("country") for s in self._supplier_of(g, node[1])}
            return ", ".join(sorted(c for c in countries if c)) or None
        return self._fields(g, node).get("country")

    def _row(self, g: nx.MultiDiGraph, node: tuple[str, str]) -> dict[str, Any]:
        out = {"type": node[0], "id": node[1], "name": self._name(g, node)}
        if country := self._country(g, node):
            out["country"] = country
        return out

    # tools ---------------------------------------------------------------------------------------------------

    def find_entities(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        text = str(args.get("text", "")).strip().casefold()
        if not text:
            raise ValueError("give a text to search for")
        with self.world.reading():
            g = self._base().graph
            hits = [n for n in g if (args.get("type") in (None, "", n[0]))
                    and (text in n[1].casefold() or text in str(self._name(g, n) or "").casefold())]
            hits.sort(key=lambda n: (n[1].casefold() != text, TYPES.index(n[0]) if n[0] in TYPES else 9, n[1]))
            return [self._row(g, n) for n in hits[:MAX_ROWS]]

    def node_info(self, args: dict[str, Any]) -> dict[str, Any]:
        with self.world.reading():
            base = self._base()
            g = base.graph
            node = self._resolve(g, args.get("id", ""), args.get("type"))
            fields = {}
            for name, values in self.store.field_values(node[0], node[1]).items():
                active = next((v for v in values if v.active and v.review == "accepted"), None)
                if active is not None and name in self._fields(g, node):
                    fields[name] = {"value": active.value, "source": active.source.label(), "method": active.method,
                                    "confidence": active.confidence}
            neighbours: dict[str, list[str]] = {}
            for u, v, data in [*g.out_edges(node, data=True), *g.in_edges(node, data=True)]:
                other = v if u == node else u
                arrow = "->" if u == node else "<-"
                neighbours.setdefault(f"{data['type']} {arrow} {other[0]}", []).append(other[1])
            out: dict[str, Any] = {**self._row(g, node), "fields": fields,
                                   "neighbours": {k: {"count": len(set(v)), "ids": sorted(set(v))[:MAX_NEIGHBOURS]}
                                                  for k, v in sorted(neighbours.items())}}
            if node[0] in EXPOSED_TYPES and base.exposure.total_revenue:
                s = base.exposure.summary(*node)
                out["business_exposure_pct"] = round(s.business_exposure_pct, 2)
                if s.adjusted_pct is not None:
                    out["adjusted_exposure_pct"] = round(s.adjusted_pct, 2)
                if node[0] == "Supplier":
                    out["recovery_time_months"] = s.recovery_time_months
                    out["business_impact_eur"] = round(s.business_impact_eur) if s.business_impact_eur else None
            return out

    def impacted_assemblies(self, args: dict[str, Any]) -> dict[str, Any]:
        with self.world.reading():
            base = self._base()
            g = base.graph
            node = self._resolve(g, args.get("id", ""), args.get("type"))
            try:
                paths = base.exposure.impacted_with_paths(*node)
            except ExposureError as e:
                raise ValueError(str(e)) from None
            components = set(base.exposure.components(*node))
            out = []
            for assembly, found in paths.items():
                fields = self._fields(g, ("Assembly", assembly)) if ("Assembly", assembly) in g else {}
                linking = sorted({p[-1] for p in found} & components) or sorted(
                    c for c in components if assembly in base.exposure._assemblies.get(c, ()))
                out.append({"assembly": assembly, "business_unit": fields.get("business_unit"),
                            "revenue_eur": fields.get("revenue_eur"), "via_components": linking,
                            "bom_paths": [" > ".join(p) for p in found[:3]]})
            return {**self._row(g, node), "assemblies": out}

    def single_source(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        country = str(args.get("country") or "").casefold()
        commodity = str(args.get("commodity") or "").casefold()
        supplier = str(args.get("supplier") or "")
        with self.world.reading():
            g = self._base().graph
            out = []
            for node in sorted(n for n in g if n[0] == "Component"):
                f = self._fields(g, node)
                if f.get("single_source") is not True:
                    continue
                suppliers = self._supplier_of(g, node[1])
                if supplier and supplier not in suppliers:
                    continue
                if commodity and commodity not in str(f.get("commodity") or "").casefold():
                    continue
                where = self._country(g, node) or ""
                if country and country not in where.casefold():
                    continue
                out.append({"id": node[1], "description": f.get("description"), "commodity": f.get("commodity"),
                            "suppliers": suppliers, "supplier_country": where or None})
            return out[:50]

    def exposure_by(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        kind = {"supplier": "Supplier", "site": "Site", "component": "Component"}.get(str(args.get("kind")))
        if kind is None:
            raise ValueError("kind is supplier, site or component")
        country = str(args.get("country") or "").casefold()
        top = max(1, min(int(args.get("top") or 15), 50))
        with self.world.reading():
            base = self._base()
            g = base.graph
            if not base.exposure.total_revenue:
                raise ValueError("no assembly revenue in the graph, so exposure is not defined yet")
            out = []
            for s in base.exposure.ranking(kind):
                node = (kind, s.id)
                if country and country not in str(self._country(g, node) or "").casefold():
                    continue
                row = {**self._row(g, node), "business_exposure_pct": round(s.business_exposure_pct, 2),
                       "assemblies": len(s.assemblies)}
                if s.adjusted_pct is not None:
                    row["adjusted_exposure_pct"] = round(s.adjusted_pct, 2)
                if kind == "Supplier":
                    row["business_impact_eur"] = round(s.business_impact_eur) if s.business_impact_eur else None
                out.append(row)
                if len(out) >= top:
                    break
            if kind == "Supplier":
                out.sort(key=lambda r: (-(r["business_impact_eur"] or 0), -r["business_exposure_pct"], r["id"]))
            return out

    def path_between(self, args: dict[str, Any]) -> dict[str, Any]:
        with self.world.reading():
            g = self._base().graph
            a = self._resolve(g, args.get("from", ""))
            b = self._resolve(g, args.get("to", ""))
            undirected = nx.Graph()
            for u, v, data in g.edges(data=True):
                undirected.add_edge(u, v, type=data["type"], forward=(u, v))
                # nodes a relation names in its identity (the assembly of a BOM line, the site of a supply)
                for f in SCHEMA.relation(data["type"]).identity:
                    ref = (f.ref, data["identity"].get(f.name))
                    if f.ref and ref in g:
                        for end in (u, v):
                            undirected.add_edge(ref, end, type=f"{data['type']} ({f.name})", forward=(end, ref))
            try:
                nodes = nx.shortest_path(undirected, a, b)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return {"from": a[1], "to": b[1], "path": None, "note": "no chain of relations links them"}
            steps = []
            for u, v in zip(nodes, nodes[1:], strict=False):
                data = undirected.edges[u, v]
                arrow = "->" if data["forward"] == (u, v) else "<-"
                steps.append(f"{u[0]} {u[1]} {arrow}{data['type']}{arrow} {v[0]} {v[1]}")
            return {"from": a[1], "to": b[1], "path": steps}
