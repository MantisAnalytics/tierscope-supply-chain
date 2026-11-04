"""A read-only snapshot of the company for operations: assemblies, bought-in components and their suppliers.

Built once from the graph (under the API lock) and then used outside it by the simulator and the agent.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from scgraph.exposure.compute import Exposure
from scgraph.graph.store import GraphStore
from scgraph.graph.view import build_view

DEFAULT_MARGIN = 0.2
WEEKS_PER_YEAR = 52

COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u.s.": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "great britain": "United Kingdom", "england": "United Kingdom",
    "korea": "South Korea", "republic of korea": "South Korea", "czechia": "Czech Republic",
    "prc": "China", "people's republic of china": "China", "holland": "Netherlands", "türkiye": "Turkey",
}
CONTINENTS = {
    "Europe": ["Austria", "Belgium", "Bulgaria", "Croatia", "Czech Republic", "Denmark", "Estonia", "Finland", "France",
               "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Netherlands",
               "Norway", "Poland", "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden",
               "Switzerland", "Turkey", "United Kingdom"],
    "Asia": ["China", "Hong Kong", "India", "Indonesia", "Israel", "Japan", "Malaysia", "Philippines", "Singapore",
             "South Korea", "Taiwan", "Thailand", "Vietnam", "United Arab Emirates", "Saudi Arabia", "Pakistan"],
    "Americas": ["United States", "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia"],
    "Africa": ["Morocco", "Tunisia", "Egypt", "South Africa", "Nigeria", "Kenya"],
    "Oceania": ["Australia", "New Zealand"],
}
CONTINENT_OF = {country: continent for continent, countries in CONTINENTS.items() for country in countries}


def country_name(raw: Any) -> str | None:
    """`China (Jilin)` -> `China`, `USA` -> `United States`; None for blanks."""
    if raw is None or not str(raw).strip():
        return None
    text = re.sub(r"\s*\(.*?\)\s*", " ", str(raw)).strip()
    return COUNTRY_ALIASES.get(text.casefold(), text)


def continent(country: str | None) -> str | None:
    return CONTINENT_OF.get(country) if country else None


@dataclass
class Supplier:
    id: str
    name: str
    country: str | None
    continent: str | None
    tier: int | None
    risk_score: float | None
    flags: list[str] = field(default_factory=list)  # risk indicators that are true flags, e.g. financial distress


@dataclass
class Site:
    id: str
    name: str
    country: str | None
    continent: str | None
    recovery_time_months: float | None


@dataclass
class Component:
    id: str
    description: str
    commodity: str | None
    sub_commodity: str | None
    criticality: str | None
    single_source: bool | None
    supplier: str
    sites: list[str]
    weekly_value: float  # EUR per week of demand
    value_estimated: bool  # True when annual spend was missing and a median stands in
    assemblies: list[str]

    @property
    def high_criticality(self) -> bool:
        return (self.criticality or "").casefold() == "high"


@dataclass
class Assembly:
    id: str
    business_unit: str | None
    revenue: float  # EUR per year
    components: list[str]  # bought-in components in its BOM

    @property
    def weekly_revenue(self) -> float:
        return self.revenue / WEEKS_PER_YEAR


@dataclass
class Company:
    assemblies: dict[str, Assembly]
    components: dict[str, Component]  # bought-in only
    suppliers: dict[str, Supplier]
    sites: dict[str, Site]
    margin: float
    margin_from_graph: bool
    exposure_pct: dict[str, float] = field(default_factory=dict)  # component -> Business Exposure %
    supplier_impact: dict[str, float] = field(default_factory=dict)  # supplier -> Business Impact EUR

    @property
    def total_revenue(self) -> float:
        return sum(a.revenue for a in self.assemblies.values())

    def candidates(self, component_id: str) -> list[str]:
        """Suppliers that already supply another component of the same sub-commodity (or commodity)."""
        comp = self.components[component_id]
        key = comp.sub_commodity or comp.commodity
        if not key:
            return []
        out = {c.supplier for c in self.components.values()
               if (c.sub_commodity or c.commodity) == key and c.supplier != comp.supplier}
        return sorted(out)

    def lane(self, component_id: str) -> list[tuple[str | None, str | None]]:
        """(supplier country, site country) for each site the component is delivered to."""
        comp = self.components[component_id]
        origin = self.suppliers[comp.supplier].country
        return [(origin, self.sites[s].country) for s in comp.sites if s in self.sites] or [(origin, None)]

    def countries(self) -> dict[str, list[str]]:
        return {"suppliers": sorted({s.country for s in self.suppliers.values() if s.country}),
                "sites": sorted({s.country for s in self.sites.values() if s.country})}

    def commodities(self) -> list[str]:
        return sorted({c.commodity for c in self.components.values() if c.commodity})


def build_company(store: GraphStore) -> Company:
    return company_from_graph(build_view(store), {n: p.value for n, p in store.parameters().items()})


def company_from_graph(graph: nx.MultiDiGraph, parameters: dict[str, Any]) -> Company:
    fields = {node: data["fields"] for node, data in graph.nodes(data=True)}
    supplier_of: dict[str, str] = {}
    sites_of: dict[str, list[str]] = defaultdict(list)
    flags: dict[str, list[str]] = defaultdict(list)
    for (src_type, src), (dst_type, dst), data in graph.edges(data=True):
        kind = data["type"]
        if kind == "SUPPLIES":
            supplier_of.setdefault(dst, src)
        elif kind == "DELIVERED_TO":
            sites_of[src].append(dst)
        elif kind == "HAS_RISK":
            indicator = fields.get((dst_type, dst), {})
            if indicator.get("value") is True:
                flags[src].append(dst.split("::")[-1])

    exposure = Exposure(graph, parameters)
    suppliers = {}
    for (t, sid), f in fields.items():
        if t != "Supplier":
            continue
        country = country_name(f.get("country"))
        suppliers[sid] = Supplier(sid, f.get("name") or sid, country, continent(country), f.get("tier"),
                                  f.get("risk_score_internal"), sorted(flags.get(sid, [])))
    sites = {}
    for (t, sid), f in fields.items():
        if t != "Site":
            continue
        country = country_name(f.get("country"))
        sites[sid] = Site(sid, f.get("name") or sid, country, continent(country), f.get("recovery_time_months"))

    spends = [float(f["annual_spend"]) for (t, _), f in fields.items()
              if t == "Component" and isinstance(f.get("annual_spend"), int | float) and f["annual_spend"] > 0]
    by_commodity: dict[str, list[float]] = defaultdict(list)
    for (t, _), f in fields.items():
        if t == "Component" and isinstance(f.get("annual_spend"), int | float) and f["annual_spend"] > 0:
            by_commodity[f.get("commodity") or ""].append(float(f["annual_spend"]))
    overall = statistics.median(spends) if spends else 52_000.0

    assemblies = {aid: Assembly(aid, fields.get(("Assembly", aid), {}).get("business_unit"), rev, [])
                  for aid, rev in exposure.revenue.items() if rev > 0}
    components = {}
    for cid, sid in sorted(supplier_of.items()):
        f = fields.get(("Component", cid), {})
        spend = f.get("annual_spend")
        estimated = not (isinstance(spend, int | float) and spend > 0)
        if estimated:
            pool = by_commodity.get(f.get("commodity") or "")
            spend = statistics.median(pool) if pool else overall
        in_assemblies = sorted(a for a in exposure._assemblies.get(cid, ()) if a in assemblies)
        components[cid] = Component(
            cid, f.get("description") or cid, f.get("commodity"), f.get("sub_commodity"), f.get("criticality"),
            f.get("single_source"), sid, sorted(set(sites_of.get(cid, []))), float(spend) / WEEKS_PER_YEAR, estimated,
            in_assemblies)
        for a in in_assemblies:
            assemblies[a].components.append(cid)
        if sid not in suppliers:
            suppliers[sid] = Supplier(sid, sid, None, None, None, None)

    margin = parameters.get("margin")
    company = Company(assemblies, components, suppliers, sites,
                      float(margin) if isinstance(margin, int | float) else DEFAULT_MARGIN,
                      isinstance(margin, int | float))
    if exposure.total_revenue:
        company.exposure_pct = {cid: exposure.business_exposure("Component", cid) for cid in components}
        for sid in {c.supplier for c in components.values()}:
            if ("Supplier", sid) in graph.nodes:
                impact = exposure.business_impact(sid)
                if impact is not None:
                    company.supplier_impact[sid] = impact
    return company
