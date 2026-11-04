"""Overview figures and the facility network for the web app, from the cached base view."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from scgraph.api.app import AppState, state
from scgraph.api.models import Network, Overview
from scgraph.api.routes.graph import _name
from scgraph.review.preview import _base, _base_numbers, revenue_proposals

router = APIRouter(tags=["overview"])
TOP = 5
JOURNAL = 5


def _numbers(st: AppState, type_name: str, node_id: str) -> dict[str, float | None]:
    return _base_numbers(_base(st.store), type_name, node_id) or {}


@router.get("/network", response_model=Network)
def network(st: AppState = Depends(state)) -> dict[str, Any]:
    """Every site and supplier, with or without coordinates, with supply links (supplier to site: the site a SUPPLIES
    edge names, else the sites the supplied component is delivered to) carrying their parts, and tier links (supplier
    to parent supplier); computed once per graph version."""
    store = st.store
    version = store.version()
    cached = getattr(store, "_network", None)
    if cached is not None and cached["version"] == version:
        return cached
    base = _base(store)
    g, revenue = base.graph, bool(base.exposure.total_revenue)
    delivered: dict[str, list[str]] = {}
    risks: dict[str, int] = {}
    tier_links = set()
    for (src_type, src), (dst_type, dst), d in g.edges(data=True):
        if d["type"] == "DELIVERED_TO":
            delivered.setdefault(src, []).append(dst)
        elif d["type"] == "HAS_RISK":
            risks[src] = risks.get(src, 0) + 1
        elif d["type"] == "PARENT_SUPPLIER" and src_type == dst_type == "Supplier":
            tier_links.add((src, dst))
    links: dict[tuple[str, str], set[str]] = {}
    for (src_type, supplier), (_, component), d in g.edges(data=True):
        if d["type"] != "SUPPLIES" or src_type != "Supplier":
            continue
        sites = [d["identity"]["site"]] if d["identity"].get("site") else delivered.get(component, [])
        for site in sites:
            if ("Site", site) in g:
                links.setdefault((supplier, site), set()).add(component)

    parts: dict[str, dict[str, Any]] = {}

    def part(component: str) -> dict[str, Any]:
        if component not in parts:
            fields = g.nodes[("Component", component)]["fields"] if ("Component", component) in g else {}
            exposure = _numbers(st, "Component", component).get("exposure_pct") if revenue else None
            parts[component] = {"id": component, "description": fields.get("description"),
                                "annual_spend": fields.get("annual_spend"), "single_source": fields.get("single_source"),
                                "exposure_pct": exposure}
        return parts[component]

    single: dict[tuple[str, str], set[str]] = {}
    supply_links = []
    for (supplier, site), components in sorted(links.items()):
        rows = sorted((part(c) for c in components), key=lambda p: (-(p["annual_spend"] or 0), p["id"]))
        supply_links.append({"supplier": supplier, "site": site, "parts": rows})
        for p in rows:
            if p["single_source"] is True:
                single.setdefault(("Supplier", supplier), set()).add(p["id"])
                single.setdefault(("Site", site), set()).add(p["id"])
    entities = []
    for (type_name, node_id), data in sorted(g.nodes(data=True)):
        if type_name not in ("Site", "Supplier"):
            continue
        fields = data["fields"]
        numbers = _numbers(st, type_name, node_id)
        entities.append({"type": type_name, "id": node_id, "name": _name(data), "country": fields.get("country"),
                         "latitude": fields.get("latitude"), "longitude": fields.get("longitude"),
                         "tier": fields.get("tier"),
                         "exposure_pct": numbers.get("exposure_pct") if revenue else None,
                         "impact_eur": numbers.get("impact_eur") if revenue else None,
                         "risk_flags": risks.get(node_id, 0) if type_name == "Supplier" else 0,
                         "single_source_parts": len(single.get((type_name, node_id), ()))})
    out = {"version": version, "entities": entities, "supply_links": supply_links, "exposure_available": revenue,
           "tier_links": [{"supplier": s, "parent": p} for s, p in sorted(tier_links)]}
    store._network = out
    return out


@router.get("/overview", response_model=Overview)
def overview(st: AppState = Depends(state)) -> dict[str, Any]:
    """Key figures, top risks, pending review and the latest journal; computed once per graph version."""
    store = st.store
    version = store.version()
    cached = getattr(store, "_overview", None)
    if cached is not None and cached["version"] == version:
        return cached
    base = _base(store)
    g, exposure = base.graph, base.exposure
    revenue = bool(exposure.total_revenue)
    counts = {t: 0 for t in ("Assembly", "Supplier", "Site", "Component")}
    single_source = 0
    for (type_name, _), data in g.nodes(data=True):
        if type_name in counts:
            counts[type_name] += 1
        if type_name == "Component" and data["fields"].get("single_source") is True:
            single_source += 1

    def ranked(type_name: str, key: str) -> list[dict[str, Any]]:
        if not revenue:
            return []
        rows = []
        for node_id in exposure.ids(type_name):
            numbers = _numbers(st, type_name, node_id)
            rows.append({"type": type_name, "id": node_id, "name": _name(g.nodes[(type_name, node_id)]),
                         "exposure_pct": numbers["exposure_pct"], "impact_eur": numbers.get("impact_eur")})
        rows = [r for r in rows if r[key] is not None]
        return sorted(rows, key=lambda r: (-r[key], r["id"]))[:TOP]

    pending = store.stats()["proposals"]
    out = {
        "version": version,
        "total_revenue_eur": exposure.total_revenue if revenue else None,
        "exposure_available": revenue,
        "revenue_proposals": [] if revenue else revenue_proposals(store),
        "counts": {"assemblies": counts["Assembly"], "suppliers": counts["Supplier"], "sites": counts["Site"],
                   "components": counts["Component"], "single_source_components": single_source},
        "pending_proposals": pending,
        "pending_total": sum(pending.values()),
        "top_suppliers_by_impact": ranked("Supplier", "impact_eur"),
        "top_suppliers_by_exposure": ranked("Supplier", "exposure_pct"),
        "top_sites_by_exposure": ranked("Site", "exposure_pct"),
        "latest_journal": store.journal(limit=JOURNAL),
    }
    store._overview = out
    return out
