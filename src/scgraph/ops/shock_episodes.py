"""ShockBench episodes on this company's graph: a year of events from ShockBench-Flow's generator, felt by our suppliers.

`benchmark/scripts/export_episodes.py` exports episodes of ShockBench's `small` task (its public generator on our own
root): every event that starts in the 52 weeks, and every decoy (an announcement of an event that never happens), with
the weeks their messages are published. ShockBench decides when, where, what kind and how hard; this module maps each
event by geography onto disruptions of our graph. ShockBench's goods (fuel, wafers, chips) are not our commodities, so
the mapping goes through places, not goods (`docs/operations.md`, "Episode benchmark", lists every assumption):

- regions: each supplier and site country belongs to one of ShockBench's 14 regions (`REGION_COUNTRIES`, else ROW);
- tariff (region R, on imports from C or from every other region): a tariff on components from C into plants in R;
- sanction or export control on a route from A to B: an export restriction from A's countries to B's plants;
- strait closure (militarised, weather, piracy): a delay, plus an insurance surcharge, on the lanes through the strait
  (`CHOKEPOINT_LANES`); a port strike: a delay on the lanes out of and into the struck region;
- energy shock in a region: energy-intensive suppliers there (castings, forgings, steel, chemicals) lose output;
- material outage: a shortage of chemicals (fuel sources) or electromechanics (materials) for everyone;
- regional conflict: suppliers in the region lose output, and trade with its counterpart stops.

An episode is a timeline: `view(t)` is what is known at the start of week t (events announced or started, decoys
announced and not yet lapsed; before an event ends its duration is the type's median, and before it starts its
severity too), `truth()` is what happens. Planning runs on views, scoring on the truth.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from scgraph.ops.company import Company
from scgraph.ops.scenarios import Compound, Disruption, effects

DEFAULT_PATH = Path("benchmarks/shockbench_episodes.json.gz")
HORIZON = 52
STOCK_COVER = 3.0

REGION_COUNTRIES = {
    "TW": ["Taiwan"], "KR": ["South Korea"], "JP": ["Japan"], "CN": ["China", "Hong Kong", "Hong Kong S.A.R."],
    "US": ["United States"], "IN": ["India"], "AU": ["Australia", "New Zealand"], "RU": ["Russia", "Belarus"],
    "UA": ["Ukraine"], "KZ": ["Kazakhstan"],
    "SEA": ["Vietnam", "Thailand", "Malaysia", "Singapore", "Indonesia", "Philippines"],
    "GULF": ["United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Bahrain", "Iran", "Iraq"],
    "EU": ["Austria", "Belgium", "Bulgaria", "Croatia", "Czech Republic", "Denmark", "Estonia", "Finland", "France",
           "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Netherlands",
           "Norway", "Poland", "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden",
           "Switzerland", "United Kingdom"],
}
REGION_NAMES = {"TW": "Taiwan", "KR": "South Korea", "JP": "Japan", "CN": "China", "US": "the United States",
                "EU": "Europe", "GULF": "the Gulf", "RU": "Russia", "AU": "Australia", "SEA": "South-East Asia",
                "IN": "India", "UA": "Ukraine", "KZ": "Kazakhstan", "ROW": "the rest of the world"}
EAST_ASIA = ("CN", "TW", "KR", "JP")
# region pairs whose sea lanes pass each strait (both directions), and the weeks a closure adds to them
CHOKEPOINT_LANES = {
    "chk_suez": [(EAST_ASIA + ("SEA", "IN", "GULF"), ("EU",))],
    "chk_malacca": [(EAST_ASIA, ("EU", "IN", "GULF"))],
    "chk_taiwan": [(EAST_ASIA, ("SEA", "IN", "EU", "GULF"))],
    "chk_hormuz": [(("GULF",), tuple(REGION_NAMES))],
    "chk_panama": [(EAST_ASIA + ("SEA",), ("US",))],
    "chk_turkish": [(("RU", "UA", "KZ"), tuple(REGION_NAMES))],
    "chk_cape": [],  # the detour around Africa: no lane of ours passes it in normal times
}
DETOUR_WEEKS = {"chk_suez": 2, "chk_malacca": 1, "chk_taiwan": 1, "chk_hormuz": 2, "chk_panama": 1, "chk_turkish": 2}
STRAIT_NAMES = {"chk_suez": "the Suez Canal", "chk_malacca": "the Strait of Malacca", "chk_taiwan": "the Taiwan Strait",
                "chk_hormuz": "the Strait of Hormuz", "chk_panama": "the Panama Canal",
                "chk_turkish": "the Turkish Straits", "chk_cape": "the Cape route"}
RISK_SURCHARGE = {"militarised_closure": 0.3, "piracy": 0.25, "regional_conflict": 0.3}  # insurance share at severity 1
ENERGY_INTENSIVE = ("CASTINGS", "FORGINGS & FORMINGS", "STEEL COMPONENTS", "CHEMICALS")
MATERIAL_SHORTAGE = {"source": ("CHEMICALS", 0.3), "material": ("ELECTROMECHANICS & A", 0.5)}  # commodity, depth
# What the planner reads: plain trade and logistics words. ShockBench's own type names stay internal (some
# providers' content filters refuse prompts that read like news about conflicts).
TYPE_LABELS = {"tariff": "Tariff", "sanction": "Trade restriction", "material_outage": "Raw material outage",
               "militarised_closure": "Strait closure", "regional_conflict": "Regional disruption",
               "piracy": "Shipping slowdown", "energy_shock": "Power shortage", "weather_closure": "Weather closure",
               "port_strike": "Port stoppage"}
CHANNEL_LABELS = {"tariff_formal": "formal tariff proposal", "tariff_informal": "informal tariff signal",
                  "tariff_final": "final tariff notice", "sanction_legal": "official notice of trade restrictions",
                  "ties_threat": "early warning of trade restrictions", "mid_threat": "early warning of a strait closure"}


def region_of(country: str | None) -> str:
    for region, countries in REGION_COUNTRIES.items():
        if country in countries:
            return region
    return "ROW"


def _countries(company: Company, regions: tuple[str, ...] | list[str], *, sites: bool = False) -> list[str]:
    pool = {s.country for s in (company.sites if sites else company.suppliers).values() if s.country}
    return sorted(c for c in pool if region_of(c) in regions)


def _others(company: Company, region: str) -> list[str]:
    return sorted({s.country for s in company.suppliers.values() if s.country and region_of(s.country) != region})


@dataclass
class Item:
    """One ShockBench event or decoy as it reaches our graph."""

    id: str
    kind: str  # event | decoy
    type: str  # ShockBench's event type
    title: str
    parts: list[Disruption]  # the true effects on our graph (a decoy's are what it threatens)
    known_week: int  # first week it is visible: its first message, or its start
    start_week: int
    end_week: int  # first week after it (a decoy's stated end)
    severity: float
    messages: list[tuple[int, str]] = field(default_factory=list)  # (week published, channel)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "type": self.type, "title": self.title,
                "known_week": self.known_week, "start_week": self.start_week, "end_week": self.end_week,
                "severity": self.severity, "messages": self.messages, "parts": [p.to_dict() for p in self.parts]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Item:
        return cls(**{**data, "parts": [Disruption.from_dict(p) for p in data["parts"]],
                      "messages": [tuple(m) for m in data["messages"]]})


def _weeks(row: dict[str, Any]) -> tuple[int, int]:
    start = max(0, math.floor(row["onset"]))
    end = min(HORIZON, max(start + 1, math.ceil(row["onset"] + row["duration"])))
    return start, end


def _part(name: str, type_: str, start: int, end: int, description: str, **params: Any) -> Disruption:
    return Disruption(name, type_, start, max(1, min(end, HORIZON) - start), description, params,
                      stock_cover_weeks=STOCK_COVER, horizon_weeks=HORIZON, source="shockbench")


def map_event(company: Company, row: dict[str, Any]) -> tuple[str, list[Disruption]]:
    """(title, our disruptions) of one exported ShockBench event; no disruptions when it touches nothing of ours."""
    t, sev = row["type"], float(row["severity"])
    start, end = _weeks(row)
    region = row.get("region") or "ROW"
    rname = REGION_NAMES.get(region, region)
    parts: list[Disruption] = []
    if t == "tariff":
        rate = float(row.get("rate") or 0.1)
        counterpart = row.get("counterpart")
        origins = _countries(company, [counterpart]) if counterpart else _others(company, region)
        dests = _countries(company, [region], sites=True)
        source = REGION_NAMES.get(counterpart, counterpart) if counterpart else "every other region"
        title = f"{rname} puts a {rate:.0%} tariff on imports from {source}"
        if origins and dests:
            parts.append(_part(title, "tariff", start, end, title, origin_countries=origins,
                               destination_countries=dests, rate=rate))
    elif t == "sanction":
        a, b = row.get("from_region") or region, row.get("to_region") or "ROW"
        title = f"New trade rules limit shipments from {REGION_NAMES.get(a, a)} to {REGION_NAMES.get(b, b)}"
        origins, dests = _countries(company, [a]), _countries(company, [b], sites=True)
        if origins and dests:
            parts.append(_part(title, "export_restriction", start, end, title, countries=origins,
                               destination_countries=dests, lost_share=min(1.0, sev)))
    elif t in ("militarised_closure", "weather_closure", "piracy"):
        cp = row.get("chokepoint", "")
        word = {"militarised_closure": "closed to shipping", "weather_closure": "closed by weather",
                "piracy": "slowed, with higher insurance"}[t]
        title = f"{STRAIT_NAMES.get(cp, cp)} {word}"
        extra = DETOUR_WEEKS.get(cp, 1) if sev >= 0.5 else 0
        surcharge = round(RISK_SURCHARGE.get(t, 0.0) * sev, 3)
        if extra or surcharge:
            for left, right in CHOKEPOINT_LANES.get(cp, []):
                for o, d in ((left, right), (right, left)):
                    origins, dests = _countries(company, o), _countries(company, d, sites=True)
                    if origins and dests:
                        parts.append(_part(title, "route_closure", start, end, title, origin_countries=origins,
                                           destination_countries=dests, extra_weeks=extra, surcharge=surcharge))
    elif t == "port_strike":
        title = f"Port stoppage in {rname}"
        extra = max(1, round((end - start) * sev))
        inside, outside = _countries(company, [region]), _others(company, region)
        here = _countries(company, [region], sites=True)
        if inside:
            parts.append(_part(title, "route_closure", start, end, title, origin_countries=inside, extra_weeks=extra,
                               surcharge=0.0))
        if outside and here:
            parts.append(_part(title, "route_closure", start, end, title, origin_countries=outside,
                               destination_countries=here, extra_weeks=extra, surcharge=0.0))
    elif t == "energy_shock":
        title = f"Power shortage in {rname}: {sev:.0%} of supply lost"
        hit = sorted(c.supplier for c in company.components.values() if c.commodity in ENERGY_INTENSIVE
                     and region_of(company.suppliers[c.supplier].country) == region)
        if hit:
            parts.append(_part(title, "supplier_outage", start, end, title, suppliers=sorted(set(hit)),
                               lost_share=round(min(1.0, sev), 3)))
    elif t == "material_outage":
        commodity, depth = MATERIAL_SHORTAGE.get(row.get("node_type"), ("CHEMICALS", 0.3))
        title = f"Raw material outage in {rname}"
        if any(c.commodity == commodity for c in company.components.values()):
            weeks = end - start
            parts.append(_part(title, "shortage", start, end, title, commodities=[commodity],
                               curve=[round(1 - depth * min(1.0, sev), 3)] * weeks))
    elif t == "regional_conflict":
        counterpart = row.get("counterpart")
        title = f"Regional disruption in {rname}" + (f", trade with {REGION_NAMES.get(counterpart, counterpart)} "
                                                     "stopped" if counterpart else "")
        hit = sorted({s for s, sup in company.suppliers.items() if region_of(sup.country) == region}
                     & {c.supplier for c in company.components.values()})
        if hit:
            parts.append(_part(title, "supplier_outage", start, end, title, suppliers=hit,
                               lost_share=round(min(1.0, sev), 3)))
        if counterpart:
            for a, b in ((region, counterpart), (counterpart, region)):
                origins, dests = _countries(company, [a]), _countries(company, [b], sites=True)
                if origins and dests:
                    parts.append(_part(title, "export_restriction", start, end, title, countries=origins,
                                       destination_countries=dests, lost_share=1.0))
    else:
        title = TYPE_LABELS.get(t, t)
    # keep only parts that reach a component
    return title, [p for p in parts if effects(company, p).reasons]


# The kinds of event the benchmark keeps: economic and operational shocks. Trade restrictions on routes, strait
# closures by military action, regional conflicts and piracy are left out (the hackathon rules exclude anything
# political or involving war, and the Meta Model API's content filter refuses prompts about them).
EVENT_TYPES = ("tariff", "energy_shock", "material_outage", "port_strike", "weather_closure")


def items_of(company: Company, episode: dict[str, Any], types: tuple[str, ...] = EVENT_TYPES) -> list[Item]:
    out = []
    for i, row in enumerate(episode["events"]):
        if row["type"] not in types:
            continue
        title, parts = map_event(company, row)
        if not parts:
            continue
        start, end = _weeks(row)
        published = row["messages"]
        if row["kind"] == "decoy":  # only its decoy-bearing channel is published; it never happens
            published = {ch: w for ch, w in published.items() if ch == row["channel"]}
        messages = sorted((max(0, math.floor(w)), ch) for ch, w in published.items() if w < HORIZON)
        known = max(0, min([start] + [w for w, _ in messages])) if row["kind"] == "event" else (
            messages[0][0] if messages else None)
        if known is None or known >= HORIZON or (row["kind"] == "decoy" and known > start):
            continue
        out.append(Item(f"{'e' if row['kind'] == 'event' else 'd'}{i}", row["kind"], row["type"], title, parts,
                        known, start, end, float(row["severity"]), messages))
    return out


@dataclass
class Priors:
    """Median duration (weeks) and severity per ShockBench type over the candidate episodes: what a planner may
    assume before an event ends or starts."""

    duration: dict[str, float]
    severity: dict[str, float]

    @classmethod
    def of(cls, episodes: list[dict[str, Any]]) -> Priors:
        dur, sev = defaultdict(list), defaultdict(list)
        for ep in episodes:
            for row in ep["events"]:
                if row["kind"] == "event":
                    dur[row["type"]].append(row["duration"])
                    sev[row["type"]].append(row["severity"])
        return cls({t: round(statistics.median(v), 2) for t, v in dur.items()},
                   {t: round(statistics.median(v), 3) for t, v in sev.items()})


def _scaled(p: Disruption, factor: float) -> Disruption:
    """A part with its severity replaced by `factor` of it (the prior over the true severity)."""
    params = dict(p.params)
    if "lost_share" in params:
        params["lost_share"] = round(min(1.0, params["lost_share"] * factor), 3)
    if "curve" in params:
        params["curve"] = [round(1 - min(1.0, (1 - v) * factor), 3) for v in params["curve"]]
    if params.get("surcharge"):
        params["surcharge"] = round(params["surcharge"] * factor, 3)
    return replace(p, params=params)


@dataclass
class Episode:
    id: str
    episode: int  # ShockBench episode index on the export's root
    items: list[Item]

    def truth(self) -> Compound:
        parts = [p for it in self.items if it.kind == "event" for p in it.parts]
        return Compound(f"Episode {self.id}", "compound", 0, HORIZON, f"{len(parts)} disruptions", {},
                        STOCK_COVER, HORIZON, "shockbench", parts=parts)

    def visible(self, week: int) -> list[Item]:
        """Items known at the start of `week`: events announced or started; decoys announced and not yet due."""
        return [it for it in self.items if it.known_week <= week and (it.kind == "event" or week <= it.start_week)]

    def status(self, it: Item, week: int) -> str:
        if it.kind == "decoy" or week < it.start_week:
            return "announced"
        return "ended" if week >= it.end_week else "in force"

    def view(self, week: int, priors: Priors) -> Compound:
        """What a planner at the start of `week` may know, as one compound disruption."""
        parts, lines = [], []
        for it in self.visible(week):
            status = self.status(it, week)
            if status == "ended":
                shown = it.parts
                when = f"weeks {it.start_week} to {it.end_week - 1}, ended"
            else:
                est = self.estimate(it, week, priors)
                end = min(HORIZON, max(it.start_week + est, week + 1))
                factor = 1.0
                if status == "announced":
                    true = it.severity or 1.0
                    factor = priors.severity.get(it.type, true) / true
                shown = [replace(_scaled(p, factor), duration_weeks=max(1, end - p.start_week)) for p in it.parts]
                median = self.estimate(it, it.start_week, priors)
                when = (f"announced to start in week {it.start_week}" if status == "announced" else
                        f"in force since week {it.start_week}") + \
                    (f"; duration not known (estimate {est} weeks, the median for its kind)" if est == median else
                     f"; it has lasted {week - it.start_week} weeks, as long as or longer than the median of {median} "
                     f"for its kind; duration not known (estimate now {est} weeks in all)")
            parts += shown
            heard = "; ".join(f"week {w}: {CHANNEL_LABELS.get(ch, ch)}" for w, ch in it.messages if w <= week)
            lines.append(f"- {TYPE_LABELS.get(it.type, it.type)}: {it.title} ({when})" + (f". News: {heard}" if heard
                                                                                          else ""))
        description = (f"Week {week} of {HORIZON}. Events known so far (announced events may not happen):\n"
                       + "\n".join(lines)) if lines else f"Week {week} of {HORIZON}. No events known."
        return Compound(f"Episode {self.id}, week {week}", "compound", 0, HORIZON, description, {}, STOCK_COVER,
                        HORIZON, "shockbench", parts=parts)

    @staticmethod
    def estimate(it: Item, week: int, priors: Priors) -> int:
        """The estimated duration of an event at `week`: the median for its kind; once it has lasted that long,
        double the estimate (so it runs out again at the next overrun check, see `overrun_weeks`)."""
        est = max(1, round(priors.duration.get(it.type, 4)))
        while it.start_week + est <= week:
            est *= 2
        return est

    def overrun_weeks(self, it: Item, priors: Priors) -> list[int]:
        """Weeks where an event in force outlasts its estimate: start + median, start + 2 x median, ... while it
        lasts. A planner would notice then that it is not over."""
        if it.kind != "event":
            return []
        out, est = [], self.estimate(it, it.start_week, priors)
        while it.start_week + est < min(it.end_week, HORIZON):
            out.append(it.start_week + est)
            est *= 2
        return out

    def signature(self, week: int) -> tuple:
        return tuple((it.id, self.status(it, week)) for it in self.visible(week))

    def decision_weeks(self) -> list[int]:
        """Weeks where what is known changes: an item appears, starts, ends or (a decoy) lapses."""
        out, last = [], ()
        for week in range(HORIZON):
            sig = self.signature(week)
            if sig != last and sig:
                out.append(week)
            last = sig
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "episode": self.episode, "items": [it.to_dict() for it in self.items]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Episode:
        return cls(data["id"], data["episode"], [Item.from_dict(it) for it in data["items"]])


def load_export(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def episodes_of(company: Company, export: dict[str, Any]) -> list[Episode]:
    return [Episode(f"sb-{ep['episode']}", ep["episode"], items_of(company, ep)) for ep in export["episodes"]]
