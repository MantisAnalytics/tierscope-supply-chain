"""Impact preview: how accepting a proposal would change exposure, computed on a copy of the view.

The base view and its exposure are cached per graph version. A preview copies the view, applies the proposal's
facts, and recomputes only the suppliers, sites and components the change can reach; a change of assembly revenue
changes the total, so it reaches every entity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from scgraph.exposure.compute import EXPOSED_TYPES, Exposure
from scgraph.graph.facts import Fact
from scgraph.graph.rows import to_json
from scgraph.graph.schema import SCHEMA, key_id
from scgraph.graph.store import GraphStore
from scgraph.graph.view import assembly_root, build_view

MAX_EFFECTS = 50  # stored per proposal; the counts cover all
# What exposure is computed from: a proposal touching one of these can change exposure once revenue exists.
EXPOSURE_FIELDS = {("Assembly", "revenue_eur"), ("Site", "recovery_time_months"), ("Component", "single_source")}
EXPOSURE_RELATIONS = {"CONTAINS", "SUPPLIES", "DELIVERED_TO"}


@dataclass
class _Base:
    version: int
    graph: nx.MultiDiGraph
    exposure: Exposure
    numbers: dict[tuple[str, str], dict[str, float | None] | None] = field(default_factory=dict)


def _base(store: GraphStore) -> _Base:
    version = store.version()
    cached = getattr(store, "_preview_base", None)
    if cached is None or cached.version != version:
        parameters = {name: p.value for name, p in store.parameters().items()}
        graph = build_view(store)
        cached = _Base(version, graph, Exposure(graph, parameters))
        store._preview_base = cached
    return cached


def numbers(exposure: Exposure, type_name: str, node_id: str) -> dict[str, float | None] | None:
    """Exposure, adjusted exposure and Business Impact of one entity; exposure 0 while there is no revenue."""
    if (type_name, node_id) not in exposure.graph:
        return None
    revenue = bool(exposure.total_revenue)
    be = exposure.business_exposure(type_name, node_id) if revenue else 0.0
    out: dict[str, float | None] = {"exposure_pct": be}
    if type_name == "Component":
        out["adjusted_pct"] = be * exposure.single_source_factor(node_id)
    if type_name == "Supplier":
        out["impact_eur"] = exposure.business_impact(node_id) if revenue else None
    return out


def _base_numbers(base: _Base, type_name: str, node_id: str) -> dict[str, float | None] | None:
    key = (type_name, node_id)
    if key not in base.numbers:
        base.numbers[key] = numbers(base.exposure, type_name, node_id)
    return base.numbers[key]


def overlay(graph: nx.MultiDiGraph, facts: list[Fact]) -> tuple[nx.MultiDiGraph, set[tuple[str, str]], bool]:
    """A copy of the view with the facts applied; returns it, the nodes the facts touch, and whether revenue moved.

    As in the store, a field of an edge only shows on an edge that exists (in the view or in the same facts).
    """
    g = graph.copy()
    touched: set[tuple[str, str]] = set()
    revenue = False
    assemblies: set[str] = set()
    edges = {(d["type"], d["edge_key"]): (u, v, k) for u, v, k, d in g.edges(keys=True, data=True)}
    next_key = -1

    def node(type_name: str, node_id: str) -> tuple[str, str]:
        n = (type_name, node_id)
        if n not in g:
            g.add_node(n, type=type_name, id=node_id, referenced_only=False, fields={})
        return n

    for fact in facts:
        if fact.kind == "entity":
            touched.add(node(fact.type, key_id(fact.key)))
        elif fact.kind == "relation" or fact.type in SCHEMA.relations:
            rel = SCHEMA.relation(fact.type)
            key = tuple(fact.key)
            if fact.kind == "relation" and (fact.type, key) not in edges:
                u, v = node(rel.src, key[0]), node(rel.dst, key[1])
                identity = {f.name: x for f, x in zip(rel.identity, key[2:], strict=True)}
                g.add_edge(u, v, key=next_key, type=fact.type, edge_key=key, identity=identity, fields={},
                           occurrences=[])
                edges[(fact.type, key)] = (u, v, next_key)
                next_key -= 1
                touched |= {u, v}
                if fact.type == "CONTAINS":
                    assemblies.add(identity["assembly"])
            if fact.kind == "field" and fact.field not in rel.occurrence_fields and (fact.type, key) in edges:
                u, v, k = edges[(fact.type, key)]
                data = g.edges[u, v, k]
                data["fields"] = {**data["fields"], fact.field: fact.value}
                touched |= {u, v}
        else:
            n = node(fact.type, key_id(fact.key))
            g.nodes[n]["fields"] = {**g.nodes[n]["fields"], fact.field: fact.value}
            touched.add(n)
            if fact.type == "Assembly" and fact.field == "revenue_eur":
                revenue = True
            if fact.type == "Component" and fact.field == "part_number":
                assemblies.add(str(fact.value))
    for (type_name, node_id), data in g.nodes(data=True):
        if type_name == "Assembly" and (node_id in assemblies or "root" not in data):
            data["root"] = assembly_root(g, node_id)
    return g, touched, revenue


def reach(g: nx.MultiDiGraph, touched: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Suppliers, sites and components whose exposure can change when the touched nodes change."""
    out: set[tuple[str, str]] = set()

    def suppliers_and_sites(component: tuple[str, str]) -> None:
        out.add(component)
        for u, _, d in g.in_edges(component, data=True):
            if d["type"] == "SUPPLIES":
                out.add(u)
        for _, v, d in g.out_edges(component, data=True):
            if d["type"] == "DELIVERED_TO":
                out.add(v)

    for n in touched:
        type_name = n[0]
        if type_name == "Component":
            suppliers_and_sites(n)
        elif type_name == "Supplier":
            out.add(n)
        elif type_name == "Site":
            out.add(n)
            for u, _, d in g.in_edges(n, data=True):  # recovery time reaches the suppliers of its components
                if d["type"] == "DELIVERED_TO":
                    suppliers_and_sites(u)
        elif type_name == "Assembly":
            for u, v, d in g.edges(data=True):
                if d["type"] == "CONTAINS" and d["identity"].get("assembly") == n[1]:
                    suppliers_and_sites(u)
                    suppliers_and_sites(v)
    return {n for n in out if n[0] in EXPOSED_TYPES}


def _differs(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def effects(store: GraphStore, facts: list[Fact], *, limited: bool = True) -> list[dict[str, Any]]:
    """Entities whose exposure numbers change if the facts are applied, largest impact change first.

    `limited=False` recomputes every entity (used to check the reachability-limited recompute).
    """
    base = _base(store)
    g, touched, revenue = overlay(base.graph, facts)
    after = Exposure(g, base.exposure.parameters)
    if revenue or not limited:
        candidates = {n for n in g if n[0] in EXPOSED_TYPES}
    else:
        candidates = reach(g, touched) | reach(base.graph, touched & set(base.graph))
    out = []
    for type_name, node_id in sorted(candidates):
        before = _base_numbers(base, type_name, node_id)
        now = numbers(after, type_name, node_id)
        if now is None:
            continue
        before = before or {k: (0.0 if k != "impact_eur" else None) for k in now}
        if not any(_differs(before.get(k), now.get(k)) for k in now):
            continue
        effect = {"type": type_name, "id": node_id}
        for k in now:
            effect[f"{k}_before"], effect[f"{k}_after"] = before.get(k), now.get(k)
        effect["exposure_pp"] = now["exposure_pct"] - (before.get("exposure_pct") or 0.0)
        effect["impact_eur"] = (now.get("impact_eur") or 0.0) - (before.get("impact_eur") or 0.0)
        out.append(effect)
    return sorted(out, key=lambda e: (-abs(e["impact_eur"]), -abs(e["exposure_pp"]), e["type"], e["id"]))


def preview(store: GraphStore, proposal_id: int) -> dict[str, Any]:
    """The proposal's impact on the current graph, cached on the proposal for this graph version."""
    proposal = store.proposal(proposal_id)
    version = store.version()
    cached = proposal["impact"]
    if cached is not None and proposal["impact_version"] == version and "depends_on" in cached:
        return cached
    facts = [f for f, _ in store.proposal_rows(proposal_id, review="proposed")]
    found = effects(store, facts)
    depends_on: list[int] = []
    if not found and feeds_exposure(facts) and not _base(store).exposure.total_revenue:
        depends_on = [p for p in revenue_proposals(store) if p != proposal_id]
    impact = {
        "version": version,
        "no_effect": not found,
        "depends_on": depends_on,
        "changed": len(found),
        "max_impact_eur": max((abs(e["impact_eur"]) for e in found), default=0.0),
        "max_exposure_pp": max((abs(e["exposure_pp"]) for e in found), default=0.0),
        "effects": found[:MAX_EFFECTS],
    }
    with store._db:
        store._db.execute("UPDATE proposals SET impact_json = ?, impact_version = ? WHERE id = ?",
                          (to_json(impact), version, proposal_id))
    return impact


def feeds_exposure(facts: list[Fact]) -> bool:
    """Whether any fact sets an exposure input (revenue, recovery time, single-source flag, a BOM or sourcing edge)."""
    return any(f.type in EXPOSURE_RELATIONS if f.kind == "relation" else (f.type, f.field) in EXPOSURE_FIELDS
               for f in facts)


def revenue_proposals(store: GraphStore) -> list[int]:
    """Pending proposals that would set assembly revenue."""
    return [p["id"] for p in store.proposals(status="pending")
            if any(f.type == "Assembly" and f.field == "revenue_eur"
                   for f, _ in store.proposal_rows(p["id"], review="proposed"))]


def impact_order(impact: dict[str, Any] | None) -> tuple:
    """Sort key: largest Business Impact change, then exposure change; no effect last."""
    if not impact or impact["no_effect"]:
        return (1, 0.0, 0.0)
    return (0, -impact["max_impact_eur"], -impact["max_exposure_pp"])


def by_impact(store: GraphStore, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Proposals with their impact (computed when stale), largest first; pending ones only get a preview. A proposal
    that depends on another listed one comes right after it."""
    for p in proposals:
        if p["status"] == "pending":
            p["impact"] = preview(store, p["id"])
    ordered = sorted(proposals, key=lambda p: (*impact_order(p["impact"]), p["id"]))
    leaders = {p["id"] for p in ordered if not (p["impact"] or {}).get("depends_on")}
    dependents: dict[int, list[dict[str, Any]]] = {}
    out = []
    for p in ordered:
        blocking = [d for d in (p["impact"] or {}).get("depends_on", []) if d in leaders]
        if blocking:
            dependents.setdefault(blocking[0], []).append(p)
    moved = {id(p) for ps in dependents.values() for p in ps}
    for p in ordered:
        if id(p) in moved:
            continue
        out.append(p)
        out += dependents.get(p["id"], [])
    return out
