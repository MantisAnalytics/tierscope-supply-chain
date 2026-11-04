"""Graph read and exposure endpoints."""

from __future__ import annotations

import difflib
from collections import defaultdict, deque
from dataclasses import asdict
from typing import Any

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException, Query

from scgraph.api.app import AppState, state
from scgraph.api.models import ExposureDetail, ExposureSummary, NodeDetail, SearchHit, Stats, Subgraph, field_value
from scgraph.exposure.compute import EXPOSED_TYPES, Exposure, ExposureError
from scgraph.graph.schema import KEY_SEPARATOR, SCHEMA
from scgraph.ingest.resolve import NAME_FIELDS, normalise_name
from scgraph.review.preview import _base, overlay

router = APIRouter(tags=["graph"])
DEFAULT_LIMIT, MAX_LIMIT = 300, 1000
VIRTUAL = 1_000_000  # ids of virtual edges are below -VIRTUAL
SUMMARY_NAMES = NAME_FIELDS | {"RiskIndicator": "indicator"}


@router.get("/stats", response_model=Stats)
def stats(st: AppState = Depends(state)) -> dict[str, Any]:
    return st.store.stats() | {"version": st.store.version(), "review_threshold": st.store.threshold}


@router.get("/schema")
def schema() -> dict[str, Any]:
    return SCHEMA.describe()


def _name(data: dict[str, Any]) -> str:
    fields = data.get("fields", {})
    return str(fields.get(NAME_FIELDS.get(data["type"], "name")) or data["id"])


def _summary(graph: nx.MultiDiGraph, type_name: str, node_id: str) -> dict[str, Any]:
    """The other end of an edge: its display name and the active scalar values of its fields, with the parts of a
    composite id (a risk indicator's supplier, category and indicator) among them."""
    identity = SCHEMA.entity(type_name).identity
    parts = node_id.split(KEY_SEPARATOR)
    values = dict(zip(identity, parts)) if len(identity) > 1 and len(parts) == len(identity) else {}
    fields = graph.nodes[(type_name, node_id)].get("fields", {}) if (type_name, node_id) in graph else {}
    values |= {k: v for k, v in fields.items() if not isinstance(v, (dict, list))}
    name = values.get(SUMMARY_NAMES.get(type_name, "name"))
    return {"type": type_name, "id": node_id, "name": str(name or node_id), "values": values}


def _node(data: dict[str, Any], pending: bool = False) -> dict[str, Any]:
    out = {"type": data["type"], "id": data["id"], "name": _name(data),
           "referenced_only": data.get("referenced_only", False)}
    return out | ({"pending": True} if pending else {})


def _edge(u: tuple, v: tuple, key: int, data: dict[str, Any], pending: bool = False) -> dict[str, Any]:
    out = {"id": key, "type": data["type"], "key": list(data["edge_key"]), "src": {"type": u[0], "id": u[1]},
           "dst": {"type": v[0], "id": v[1]}, "identity": data["identity"], "fields": data["fields"],
           "occurrences": len(data.get("occurrences", []))}
    return out | ({"pending": True} if pending else {})


@router.get("/graph", response_model=Subgraph)
def subgraph(around: str | None = Query(None, description="Type:id"), depth: int = Query(1, ge=1, le=3),
             pending: bool = False, limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
             relations: str | None = Query(None, description="comma-separated relation types to follow"),
             st: AppState = Depends(state)) -> dict[str, Any]:
    """The accepted graph, or at most `limit` nodes within `depth` hops of a node; `pending` adds what proposals
    would add, `relations` keeps only those edge types.

    A hop follows an edge, or an edge's identity reference: the components of an assembly's BOM are one hop from
    the assembly, which is linked to its root component by a virtual `ROOT` edge."""
    base = _base(st.store).graph
    graph = base
    if pending:
        facts = [f for p in st.store.proposals(status="pending") for f, _ in st.store.proposal_rows(p["id"])]
        graph = overlay(base, facts)[0]
    kinds = None
    if relations:
        kinds = {r.strip() for r in relations.split(",") if r.strip()}
        if unknown := kinds - set(SCHEMA.relations):
            raise HTTPException(422, f"unknown relation type(s): {', '.join(sorted(unknown))}")
    nodes: set = set(graph)
    omitted = 0
    if around:
        type_name, _, node_id = around.partition(":")
        start = (type_name, node_id)
        if start not in graph:
            raise HTTPException(404, f"no {type_name} {node_id!r} in the graph")
        nodes, omitted = _within(_adjacency(graph, kinds), start, depth, limit)
    sub = graph.subgraph(nodes)
    edges = [_edge(u, v, k, d, pending and k < 0) for u, v, k, d in sub.edges(keys=True, data=True)
             if kinds is None or d["type"] in kinds]
    for i, ((type_name, node_id), data) in enumerate(sorted(sub.nodes(data=True))):
        root = data.get("root")
        if type_name == "Assembly" and root and ("Component", root) in nodes:
            edges.append({"id": -VIRTUAL - i, "type": "ROOT", "key": [node_id, root], "identity": {}, "fields": {},
                          "src": {"type": "Assembly", "id": node_id}, "dst": {"type": "Component", "id": root},
                          "occurrences": 0, "virtual": True})
    return {"nodes": [_node(d, pending and n not in base) for n, d in sorted(sub.nodes(data=True))],
            "edges": edges, "truncated": omitted > 0, "omitted": omitted}


def _adjacency(graph: nx.MultiDiGraph, kinds: set[str] | None) -> dict[tuple, set[tuple]]:
    """Neighbours through edges of the given kinds and through the entities their identities reference."""
    adj: dict[tuple, set[tuple]] = defaultdict(set)
    for u, v, d in graph.edges(data=True):
        if kinds is not None and d["type"] not in kinds:
            continue
        adj[u].add(v)
        adj[v].add(u)
        for f in SCHEMA.relation(d["type"]).identity:
            ref = (f.ref, d["identity"].get(f.name))
            if f.ref and ref[1] and ref in graph:
                adj[ref] |= {u, v}
                adj[u].add(ref)
                adj[v].add(ref)
    return adj


def _within(adj: dict[tuple, set[tuple]], start: tuple, depth: int, limit: int) -> tuple[set, int]:
    """Nodes within `depth` hops; past the limit, keep the closest, then best-connected (by id on ties).
    Returns the kept nodes and how many were left out."""
    distance = {start: 0}
    queue = deque([start])
    while queue:
        n = queue.popleft()
        if distance[n] == depth:
            continue
        for m in adj.get(n, ()):
            if m not in distance:
                distance[m] = distance[n] + 1
                queue.append(m)
    if len(distance) <= limit:
        return set(distance), 0
    ordered = sorted(distance, key=lambda n: (distance[n], -len(adj.get(n, ())), n))
    return set(ordered[:limit]), len(distance) - limit


@router.get("/nodes/{type_name}/{node_id:path}", response_model=NodeDetail, response_model_exclude_unset=True)
def node(type_name: str, node_id: str, st: AppState = Depends(state)) -> dict[str, Any]:
    """A node with every candidate of every field and its provenance, and its edges."""
    store = st.store
    if type_name not in SCHEMA.entities:
        raise HTTPException(404, f"unknown entity type {type_name!r}")
    if not store.has_node(type_name, node_id):
        if (target := store.alias_of(type_name, node_id)) is not None:
            return {"type": type_name, "id": node_id, "hidden": "merged",
                    "merged_into": store.resolve_alias(type_name, target)}
        if (marker := store.deletion("node", type_name, node_id)) is not None:
            return {"type": type_name, "id": node_id, "hidden": "deleted", "deletion": marker}
        raise HTTPException(404, f"no {type_name} {node_id!r} in the graph")
    referenced_only = dict(((t, i), r) for t, i, r in store.nodes(type_name))[(type_name, node_id)]
    sources = [{"source": s.to_dict(), "label": s.label(), "method": m, "confidence": c, "resolution": r}
               for s, m, c, r in store.node_sources(type_name, node_id)]
    fields = {name: [field_value(v) for v in values]
              for name, values in store.field_values(type_name, node_id).items()}
    base = _base(store).graph
    edges = []
    for e in store.edges_of(type_name, node_id):
        out = (e.src_type, e.src_id) == (type_name, node_id)
        other = (e.dst_type, e.dst_id) if out else (e.src_type, e.src_id)
        edges.append({
            "id": e.id, "type": e.type, "direction": "out" if out else "in", "key": list(e.key),
            "other": _summary(base, *other), "identity": e.identity(),
            "fields": {name: [field_value(v) for v in values] for name, values in store.field_values(e.type, e.key)
                       .items()},
            "sources": [s | {"source": s["source"].to_dict(), "label": s["source"].label()}
                        for s in store.edge_sources(e.id)],
        })
    out = {"type": type_name, "id": node_id, "referenced_only": referenced_only, "sources": sources,
           "fields": fields, "edges": edges}
    if type_name == "Assembly":  # its BOM, which no edge of the assembly names: the root and every component in it
        out["root"] = base.nodes.get((type_name, node_id), {}).get("root")
        out["components"] = sorted({c for u, v, d in base.edges(data=True) if d["type"] == "CONTAINS"
                                    and d["identity"].get("assembly") == node_id for _, c in (u, v)})
    return out


@router.get("/search", response_model=list[SearchHit])
def search(q: str, type: str | None = None, limit: int = Query(20, ge=1, le=200),
           st: AppState = Depends(state)) -> list[dict[str, Any]]:
    """Nodes whose id or name matches the text, best first."""
    text = q.strip().casefold()
    target = normalise_name(q)
    fields = st.store.active_fields()
    scored = []
    for type_name, node_id, _ in st.store.nodes(type):
        name = str(fields.get(("node", type_name, node_id), {}).get(NAME_FIELDS.get(type_name, "name")) or "")
        ident = node_id.casefold()
        if ident == text:
            score = 1.0
        elif ident.startswith(text):
            score = 0.95
        elif text and (text in ident or text in name.casefold()):
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, target, normalise_name(name)).ratio() * 0.85 if name else 0.0
            if score < 0.5:
                continue
        scored.append((-score, type_name, node_id, name))
    return [{"type": t, "id": i, "name": n or i, "score": round(-s, 3)} for s, t, i, n in sorted(scored)[:limit]]


def _exposure(st: AppState) -> Exposure:
    return _base(st.store).exposure


@router.get("/exposure/ranking", response_model=list[ExposureSummary])
def ranking(type: str = Query(..., description="Component, Site or Supplier"), by: str = "exposure",
            limit: int = Query(50, ge=1, le=1000), st: AppState = Depends(state)) -> list[dict[str, Any]]:
    if type not in EXPOSED_TYPES or by not in ("exposure", "impact"):
        raise HTTPException(422, f"type must be one of {EXPOSED_TYPES} and by exposure or impact")
    try:
        summaries = _exposure(st).ranking(type)
    except ExposureError as e:
        raise HTTPException(422, str(e)) from None
    if by == "impact":
        summaries = sorted(summaries, key=lambda s: (-(s.business_impact_eur or 0.0), -s.business_exposure_pct, s.id))
    return [asdict(s) for s in summaries[:limit]]


@router.get("/exposure/{type_name}/{node_id:path}", response_model=ExposureDetail)
def exposure(type_name: str, node_id: str, st: AppState = Depends(state)) -> dict[str, Any]:
    """Exposure, adjusted exposure, Business Impact and impacted assemblies with BOM paths."""
    e = _exposure(st)
    if type_name not in EXPOSED_TYPES:
        raise HTTPException(422, f"exposure is defined for {', '.join(EXPOSED_TYPES)}")
    if (type_name, node_id) not in e.graph:
        raise HTTPException(404, f"no {type_name} {node_id!r} in the graph")
    try:
        summary = e.summary(type_name, node_id)
        paths = e.impacted_with_paths(type_name, node_id)
    except ExposureError as err:
        raise HTTPException(422, str(err)) from None
    assemblies = [{"id": a, "revenue_eur": e.revenue.get(a, 0.0), "paths": p} for a, p in paths.items()]
    extra = {"single_source_factor": e.single_source_factor(node_id)} if type_name == "Component" else {}
    return asdict(summary) | {"total_revenue_eur": e.total_revenue, "impacted_assemblies": assemblies} | extra
