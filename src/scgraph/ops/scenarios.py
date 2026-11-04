"""Disruptions: what goes wrong, where and when, and its weekly effect on each bought-in component."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np

from scgraph.ops.company import CONTINENTS, Company, country_name

TYPES = ("supplier_outage", "export_restriction", "route_closure", "plant_outage", "tariff", "shortage")
TYPE_LABELS = {
    "supplier_outage": "Supplier outage", "export_restriction": "Export restriction", "route_closure": "Route closure",
    "plant_outage": "Plant outage", "tariff": "Tariff", "shortage": "Supply shortage", "compound": "Several events",
}
DEFAULT_HORIZON = 26
DEFAULT_STOCK_COVER = 3.0
RECOVERY_RAMP_WEEKS = 2


class DisruptionError(ValueError):
    pass


@dataclass
class Disruption:
    name: str
    type: str
    start_week: int = 0
    duration_weeks: int = 8
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    stock_cover_weeks: float = DEFAULT_STOCK_COVER
    horizon_weeks: int = DEFAULT_HORIZON
    source: str = "form"  # form | library | news | shockbench

    def validate(self, company: Company | None = None) -> None:
        if self.type not in TYPES:
            raise DisruptionError(f"type must be one of {', '.join(TYPES)}, got {self.type!r}")
        if not self.name.strip():
            raise DisruptionError("a disruption needs a name")
        if not 4 <= self.horizon_weeks <= 52:
            raise DisruptionError("horizon must be 4 to 52 weeks")
        if not 0 <= self.start_week < self.horizon_weeks:
            raise DisruptionError(f"start week must be within the horizon (0 to {self.horizon_weeks - 1})")
        if not 1 <= self.duration_weeks <= 52:
            raise DisruptionError("duration must be 1 to 52 weeks")
        if not 0 <= self.stock_cover_weeks <= 12:
            raise DisruptionError("stock cover must be 0 to 12 weeks")
        p = self.params
        need = {"supplier_outage": ["suppliers"], "export_restriction": ["countries"],
                "route_closure": ["origin_countries", "extra_weeks"], "plant_outage": ["sites"],
                "tariff": ["origin_countries", "rate"], "shortage": ["commodities", "curve"]}[self.type]
        missing = [k for k in need if not p.get(k) and p.get(k) != 0]
        if missing:
            raise DisruptionError(f"{TYPE_LABELS[self.type]} needs {', '.join(missing)}")
        if self.type == "shortage" and not all(0 <= float(v) <= 1 for v in p["curve"]):
            raise DisruptionError("shortage curve values must be within 0..1")
        for key in ("lost_share", "rate", "surcharge"):
            if key in p and not 0 <= float(p[key]) <= (5 if key == "rate" else 1):
                raise DisruptionError(f"{key} is out of range")
        if company is not None:
            unknown = [s for s in p.get("suppliers", []) if s not in company.suppliers]
            unknown += [s for s in p.get("sites", []) if s not in company.sites]
            if unknown:
                raise DisruptionError(f"not in the graph: {', '.join(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Disruption:
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass
class Effects:
    """Weekly effect of one disruption on every affected component; unaffected components are absent."""

    horizon: int
    capacity: dict[str, np.ndarray] = field(default_factory=dict)  # supplier capacity share, 1 = normal
    gap: dict[str, np.ndarray] = field(default_factory=dict)  # True where arrivals are delayed (route closure)
    surcharge: dict[str, np.ndarray] = field(default_factory=dict)  # extra cost share on primary arrivals
    tariff: dict[str, np.ndarray] = field(default_factory=dict)  # tariff rate on primary arrivals
    usable: dict[str, np.ndarray] = field(default_factory=dict)  # share usable at our plants (plant outage)
    reasons: dict[str, str] = field(default_factory=dict)
    affected_suppliers: set[str] = field(default_factory=set)
    affected_sites: set[str] = field(default_factory=set)

    @property
    def components(self) -> list[str]:
        return sorted(self.reasons)

    def onset(self, cid: str) -> int:
        """The first week the component is affected (the horizon if never)."""
        hit = np.zeros(self.horizon, dtype=bool)
        for table, normal in ((self.capacity, 1.0), (self.usable, 1.0), (self.surcharge, 0.0), (self.tariff, 0.0)):
            if cid in table:
                hit |= table[cid] != normal
        if cid in self.gap:
            hit |= self.gap[cid]
        weeks = np.flatnonzero(hit)
        return int(weeks[0]) if weeks.size else self.horizon

    def merge(self, other: Effects) -> None:
        """Add another disruption's effects over the same horizon: capacity and usable shares multiply, delays
        combine, tariffs and surcharges add."""
        for mine, theirs, combine in ((self.capacity, other.capacity, np.multiply),
                                      (self.usable, other.usable, np.multiply),
                                      (self.gap, other.gap, np.logical_or),
                                      (self.surcharge, other.surcharge, np.add),
                                      (self.tariff, other.tariff, np.add)):
            for cid, values in theirs.items():
                mine[cid] = combine(mine[cid], values) if cid in mine else values.copy()
        for cid, reason in other.reasons.items():
            self.reasons[cid] = f"{self.reasons[cid]}; {reason}" if cid in self.reasons else reason
        self.affected_suppliers |= other.affected_suppliers
        self.affected_sites |= other.affected_sites


@dataclass
class Compound(Disruption):
    """Several disruptions at once over one horizon (an episode of the episode benchmark). Its start is the earliest
    part's; its effects are the parts' effects merged."""

    type: str = "compound"
    parts: list[Disruption] = field(default_factory=list)

    def validate(self, company: Company | None = None) -> None:
        for p in self.parts:
            p.validate(company)

    def duration_text(self) -> str:
        return f"{len(self.parts)} events over {self.horizon_weeks} weeks"


def _countries(values: list[str]) -> set[str]:
    out = set()
    for v in values or []:
        if v in CONTINENTS:  # a continent name stands for its countries
            out |= set(CONTINENTS[v])
        elif name := country_name(v):
            out.add(name)
    return out


def _window(d: Disruption) -> np.ndarray:
    """1.0 while the disruption is active, ramping back to 0 over the recovery weeks after it ends."""
    w = np.zeros(d.horizon_weeks)
    end = d.start_week + d.duration_weeks
    w[d.start_week:min(end, d.horizon_weeks)] = 1.0
    for k in range(1, RECOVERY_RAMP_WEEKS + 1):
        if end + k - 1 < d.horizon_weeks:
            w[end + k - 1] = max(w[end + k - 1], 1 - k / (RECOVERY_RAMP_WEEKS + 1))
    return w


def _matches_commodity(company: Company, cid: str, commodities: list[str] | None) -> bool:
    if not commodities:
        return True
    comp = company.components[cid]
    wanted = {c.casefold() for c in commodities}
    return (comp.commodity or "").casefold() in wanted or (comp.sub_commodity or "").casefold() in wanted


def _parts_by(d: Compound, week: int | None) -> list[Disruption]:
    """A compound's parts that start by `week` (all when None): a later event cannot undo an action taken before
    it (the weekly model does not cut a new source's supply when a later event hits it)."""
    return [p for p in d.parts if week is None or p.start_week <= week]


def supplier_affected(company: Company, d: Disruption, supplier_id: str, by_week: int | None = None) -> bool:
    """Whether a supplier's own output is hit (used to refuse new-supplier actions); in a compound, by events that
    start by `by_week` (the week the new supplier would start delivering)."""
    if isinstance(d, Compound):
        return any(supplier_affected(company, part, supplier_id) for part in _parts_by(d, by_week))
    p = d.params
    sup = company.suppliers.get(supplier_id)
    if d.type == "supplier_outage":
        return supplier_id in p.get("suppliers", [])
    if d.type == "export_restriction":
        return sup is not None and sup.country in _countries(p.get("countries", []))
    return False


def blocked_origin(d: Disruption, country: str | None, by_week: int | None = None) -> bool:
    """Whether a supplier in this country ships on the affected lanes (a route closure or tariff from it); in a
    compound, by events that start by `by_week`."""
    if isinstance(d, Compound):
        return any(blocked_origin(part, country) for part in _parts_by(d, by_week))
    return d.type in ("route_closure", "tariff") and country in _countries(d.params.get("origin_countries", []))


def effects(company: Company, d: Disruption) -> Effects:
    h = d.horizon_weeks
    out = Effects(h)
    if isinstance(d, Compound):
        for part in d.parts:
            out.merge(effects(company, replace(part, horizon_weeks=h) if part.horizon_weeks != h else part))
        return out
    window = _window(d)
    p = d.params
    for cid, comp in company.components.items():
        sup = company.suppliers[comp.supplier]
        if d.type == "supplier_outage" and comp.supplier in p["suppliers"]:
            lost = float(p.get("lost_share", 1.0))
            out.capacity[cid] = 1 - lost * window
            out.reasons[cid] = f"supplier {comp.supplier} loses {lost:.0%} of capacity"
            out.affected_suppliers.add(comp.supplier)
        elif (d.type == "export_restriction" and sup.country in _countries(p["countries"])
              and _matches_commodity(company, cid, p.get("commodities"))):
            # with destinations, only the share of the component's lanes into them is stopped
            dests = _countries(p.get("destination_countries", []))
            lanes = company.lane(cid)
            share = sum(1 for _, s in lanes if s in dests) / len(lanes) if dests else 1.0
            if share > 0:
                lost = float(p.get("lost_share", 1.0)) * share
                out.capacity[cid] = 1 - lost * window
                out.reasons[cid] = f"export restriction on {sup.country}" + (" (some destinations)" if share < 1 else "")
                out.affected_suppliers.add(comp.supplier)
        elif d.type == "route_closure":
            origins = _countries(p["origin_countries"])
            dests = _countries(p.get("destination_countries", []))
            lanes = [(o, s) for o, s in company.lane(cid) if o in origins and (not dests or s in dests) and o != s]
            if lanes:
                gap = np.zeros(h, dtype=bool)
                extra = int(p["extra_weeks"])
                gap[d.start_week:min(d.start_week + min(extra, d.duration_weeks), h)] = True
                out.gap[cid] = gap
                surcharge = np.zeros(h)
                surcharge[d.start_week:min(d.start_week + d.duration_weeks, h)] = float(p.get("surcharge", 0.0))
                out.surcharge[cid] = surcharge
                out.reasons[cid] = f"lane {lanes[0][0]} -> {lanes[0][1]} takes {extra} more weeks"
                out.affected_suppliers.add(comp.supplier)
        elif d.type == "plant_outage":
            down = [s for s in comp.sites if s in p["sites"]]
            if down:
                share = len(down) / len(comp.sites)
                out.usable[cid] = 1 - share * window
                out.reasons[cid] = f"delivered to {', '.join(down)}, which is down"
                out.affected_sites.update(down)
        elif d.type == "tariff":
            origins = _countries(p["origin_countries"])
            dests = _countries(p.get("destination_countries", []))
            if any(o in origins and (not dests or s in dests) for o, s in company.lane(cid)):
                rate = np.zeros(h)
                rate[d.start_week:min(d.start_week + d.duration_weeks, h)] = float(p["rate"])
                out.tariff[cid] = rate
                out.reasons[cid] = f"{float(p['rate']):.0%} tariff on imports from {sup.country}"
                out.affected_suppliers.add(comp.supplier)
        elif d.type == "shortage" and _matches_commodity(company, cid, p["commodities"]):
            curve = [float(v) for v in p["curve"]]
            cap = np.ones(h)
            for k, value in enumerate(curve[:d.duration_weeks]):
                if d.start_week + k < h:
                    cap[d.start_week + k] = value
            out.capacity[cid] = cap
            out.reasons[cid] = f"{comp.commodity} supply limited to {min(curve):.0%} at the low point"
            out.affected_suppliers.add(comp.supplier)
    return out


def library(company: Company, shockbench_curve: list[float] | None = None) -> list[Disruption]:
    """Ready disruptions that name entities of this graph. The semiconductor shortage follows a ShockBench episode."""
    if shockbench_curve is None:
        from scgraph.ops import shockbench

        shockbench_curve = shockbench.curve()
    out: list[Disruption] = []
    if company.supplier_impact:
        top = max(company.supplier_impact, key=company.supplier_impact.get)
        sup = company.suppliers[top]
        out.append(Disruption(
            f"Fire at {sup.name}", "supplier_outage", 0, 10,
            f"A fire stops production at {sup.name} ({sup.country}), the supplier with the largest Business Impact. "
            "Restart is expected in about ten weeks.", {"suppliers": [top], "lost_share": 1.0}, source="library"))
    chinese = [c for c in company.components.values() if company.suppliers[c.supplier].country == "China"]
    if chinese:
        commodity = max({c.commodity for c in chinese if c.commodity},
                        key=lambda k: sum(1 for c in chinese if c.commodity == k), default=None)
        out.append(Disruption(
            f"Export licences for {(commodity or 'components').title()} from China", "export_restriction", 2, 12,
            f"New export licensing in China halts shipments of {commodity or 'components'} from week 2 for about "
            "three months. Announced today.", {"countries": ["China"], "commodities": [commodity] if commodity else [],
                                                "lost_share": 1.0}, source="library"))
    out.append(Disruption(
        "Suez Canal closed to container ships", "route_closure", 0, 10,
        "Container traffic between Asia and Europe goes around the Cape of Good Hope; with the port backlog this adds "
        "about four weeks of transit and a freight surcharge.",
        {"origin_countries": ["Asia"], "destination_countries": ["Europe", "Africa"], "extra_weeks": 4,
         "surcharge": 0.3}, source="library"))
    out.append(Disruption(
        "Shanghai port congestion", "route_closure", 1, 8,
        "Terminal closures in Shanghai delay exports from China by about four weeks for eight weeks.",
        {"origin_countries": ["China"], "destination_countries": [], "extra_weeks": 4, "surcharge": 0.15},
        source="library"))
    out.append(Disruption(
        "25% US tariff on imports from China and Mexico", "tariff", 0, 26,
        "A new 25% duty on components imported into the United States from China and Mexico.",
        {"origin_countries": ["China", "Mexico"], "destination_countries": ["United States"], "rate": 0.25},
        source="library"))
    electro = [k for k in company.commodities() if k.casefold().startswith("electro")]
    if electro:
        curve = shockbench_curve or [1.0, 0.9, 0.7, 0.5, 0.4, 0.4, 0.45, 0.55, 0.7, 0.85, 0.95, 1.0]
        out.append(Disruption(
            "Semiconductor shortage", "shortage", 0, len(curve),
            "Chip supply falls as fabs lose power and a strait closes upstream; the weekly capacity follows "
            + ("the ShockBench network." if shockbench_curve else "a typical shortage curve."),
            {"commodities": electro, "curve": curve}, source="shockbench" if shockbench_curve else "library"))
    return out
