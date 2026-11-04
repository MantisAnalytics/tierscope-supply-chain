"""A pasted news or alert text turned into a draft disruption by the model, matched against the graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scgraph.llm import LLMClient, Request
from scgraph.ops.company import CONTINENTS, Company, country_name
from scgraph.ops.scenarios import TYPES, Disruption, DisruptionError

TOOL = {
    "name": "draft_disruption",
    "description": "Describe the disruption in the text as structured fields. Use only names from the lists given.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "short title, under 60 characters"},
            "type": {"type": "string", "enum": list(TYPES)},
            "description": {"type": "string", "description": "two sentences: what happened and what it blocks"},
            "start_week": {"type": "integer", "description": "weeks from today until it starts (0 = already)"},
            "duration_weeks": {"type": "integer", "description": "expected length in weeks"},
            "suppliers": {"type": "array", "items": {"type": "string"}, "description": "supplier ids (supplier_outage)"},
            "countries": {"type": "array", "items": {"type": "string"}, "description": "supplier countries (export_restriction)"},
            "commodities": {"type": "array", "items": {"type": "string"}, "description": "commodities hit, empty = all"},
            "origin_countries": {"type": "array", "items": {"type": "string"},
                                 "description": "countries or continents goods leave from (route_closure, tariff)"},
            "destination_countries": {"type": "array", "items": {"type": "string"},
                                      "description": "countries or continents goods go to; empty = everywhere"},
            "sites": {"type": "array", "items": {"type": "string"}, "description": "our plant ids (plant_outage)"},
            "extra_weeks": {"type": "integer", "description": "added transit weeks (route_closure)"},
            "surcharge": {"type": "number", "description": "freight surcharge as a share of value (route_closure)"},
            "rate": {"type": "number", "description": "tariff rate, 0.25 = 25% (tariff)"},
            "lost_share": {"type": "number", "description": "share of capacity lost, 1 = all (outage, restriction)"},
            "low_point": {"type": "number", "description": "lowest share of supply still available (shortage)"},
        },
        "required": ["name", "type", "description", "start_week", "duration_weeks"],
    },
}

SYSTEM = """You turn supply-chain news and alerts into one structured disruption for a manufacturer's risk system. \
Pick the type that fits the effect on supply: a named supplier that cannot deliver is a supplier_outage; a country \
that stops exports (licences, sanctions, controls) is an export_restriction; a closed canal, strait or port that \
makes shipments late is a route_closure; damage at one of our own plants is a plant_outage; a duty is a tariff; a \
market-wide lack of a commodity is a shortage. Use only supplier ids, countries, plants and commodities from the \
lists given; leave a list empty if nothing fits. Call draft_disruption once."""


@dataclass
class Draft:
    disruption: Disruption
    warnings: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


def _catalogue(company: Company) -> str:
    countries = company.countries()
    suppliers = "; ".join(f"{s.id} {s.name} ({s.country})" for s in sorted(company.suppliers.values(), key=lambda s: s.id))
    sites = "; ".join(f"{s.id} {s.name} ({s.country})" for s in company.sites.values())
    return (f"Supplier countries: {', '.join(countries['suppliers'])}\nPlant countries: {', '.join(countries['sites'])}\n"
            f"Continents usable as countries: {', '.join(CONTINENTS)}\nCommodities: {', '.join(company.commodities())}\n"
            f"Our plants: {sites}\nSuppliers: {suppliers}")


def draft_from_news(company: Company, text: str, client: LLMClient) -> Draft:
    text = text.strip()
    if not text:
        raise DisruptionError("paste a news or alert text first")
    request = Request(messages=[{"role": "user", "content": f"{_catalogue(company)}\n\n# News\n{text[:6000]}"}],
                      system=SYSTEM, tools=[TOOL], max_tokens=1500)
    response = client.create(request)
    call = next((c for c in response.tool_calls if c.name == "draft_disruption"), None)
    if call is None:
        raise DisruptionError("the model did not return a disruption; try a more specific text")
    return to_draft(company, call.input, cost=response.cost_usd)


def to_draft(company: Company, raw: dict[str, Any], cost: float = 0.0) -> Draft:
    warnings: list[str] = []
    kind = raw.get("type") if raw.get("type") in TYPES else "supplier_outage"
    known_countries = set(company.countries()["suppliers"]) | set(company.countries()["sites"]) | set(CONTINENTS)

    def countries(key: str) -> list[str]:
        out = []
        for value in raw.get(key) or []:
            name = value if value in CONTINENTS else country_name(value)
            if name in known_countries:
                out.append(name)
            else:
                warnings.append(f"{key.replace('_', ' ')}: {value!r} is not in the graph")
        return out

    commodities = []
    lookup = {c.casefold(): c for c in company.commodities()}
    for value in raw.get("commodities") or []:
        if str(value).casefold() in lookup:
            commodities.append(lookup[str(value).casefold()])
        else:
            warnings.append(f"commodity {value!r} is not in the graph")
    params: dict[str, Any] = {}
    if kind == "supplier_outage":
        params = {"suppliers": [s for s in raw.get("suppliers") or [] if s in company.suppliers],
                  "lost_share": float(raw.get("lost_share") or 1.0)}
        warnings += [f"supplier {s!r} is not in the graph" for s in raw.get("suppliers") or []
                     if s not in company.suppliers]
    elif kind == "export_restriction":
        params = {"countries": countries("countries") or countries("origin_countries"), "commodities": commodities,
                  "lost_share": float(raw.get("lost_share") or 1.0)}
    elif kind == "route_closure":
        params = {"origin_countries": countries("origin_countries"),
                  "destination_countries": countries("destination_countries"),
                  "extra_weeks": int(raw.get("extra_weeks") or 2), "surcharge": float(raw.get("surcharge") or 0.2)}
    elif kind == "plant_outage":
        params = {"sites": [s for s in raw.get("sites") or [] if s in company.sites]}
    elif kind == "tariff":
        params = {"origin_countries": countries("origin_countries") or countries("countries"),
                  "destination_countries": countries("destination_countries"), "rate": float(raw.get("rate") or 0.25)}
    elif kind == "shortage":
        low = min(1.0, max(0.0, float(raw.get("low_point") or 0.5)))
        weeks = max(2, int(raw.get("duration_weeks") or 8))
        half = weeks // 2
        curve = [round(1 - (1 - low) * min(k + 1, weeks - k) / max(half, 1), 3) for k in range(weeks)]
        params = {"commodities": commodities, "curve": [max(low, min(1.0, v)) for v in curve]}
    d = Disruption(name=str(raw.get("name") or "Disruption from news")[:80], type=kind,
                   start_week=max(0, int(raw.get("start_week") or 0)),
                   duration_weeks=max(1, min(52, int(raw.get("duration_weeks") or 8))),
                   description=str(raw.get("description") or ""), params=params, source="news")
    try:
        d.validate(company)
    except DisruptionError as e:
        warnings.append(f"needs editing before saving: {e}")
    return Draft(d, warnings, cost)
