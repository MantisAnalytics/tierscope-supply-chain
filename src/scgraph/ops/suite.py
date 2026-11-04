"""The benchmark suite: seeded disruptions of every type drawn from the graph, split into dev and test.

Dev holds the library disruptions and one generated case per type; it is for working on the agent. Test is held out
and only reported. The file keeps a fingerprint of the graph it was made from, so it is never scored on another one.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scgraph.ops import shockbench
from scgraph.ops.company import Company
from scgraph.ops.scenarios import TYPES, Disruption, library
from scgraph.ops.simulate import MIN_GAIN_EUR, Simulator

GENERATOR_VERSION = 1
DEFAULT_PATH = Path("benchmarks/ops_suite.json")
DEFAULT_SEED = 7
DEV_PER_TYPE = 1
TEST_PER_TYPE = 5
MIN_DO_NOTHING_EUR = 50_000.0
MIN_SAVING_EUR = 25_000.0  # below this the optimiser has nothing to win and skill is undefined
MAX_TRIES = 400
MAX_CURVE_WEEKS = 22  # shortage curves are cut to fit the horizon


class SuiteError(ValueError):
    pass


@dataclass
class Case:
    id: str
    split: str  # dev | test
    family: str
    disruption: Disruption

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "split": self.split, "family": self.family, "disruption": self.disruption.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Case:
        return cls(data["id"], data["split"], data["family"], Disruption.from_dict(data["disruption"]))


def fingerprint(company: Company) -> dict[str, Any]:
    return {"components": len(company.components), "suppliers": len(company.suppliers),
            "assemblies": len(company.assemblies), "revenue_eur": round(company.total_revenue)}


# --- families --------------------------------------------------------------------------------------------------------

def _supplier_outage(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    used = sorted({c.supplier for c in co.components.values()})
    top = sorted(co.supplier_impact, key=lambda s: -co.supplier_impact[s])[:15]
    sid = rng.choice(top) if top and rng.random() < 0.5 else rng.choice(used)
    sup = co.suppliers[sid]
    lost = rng.choice((0.5, 0.75, 1.0, 1.0))
    weeks = rng.choice((3, 4, 6, 8, 10, 12, 16))
    return "supplier_outage", Disruption(
        f"Outage at {sup.name}", "supplier_outage", rng.choice((0, 0, 1, 2, 4)), weeks,
        f"{sup.name} ({sup.country}) loses {lost:.0%} of its capacity for {weeks} weeks after a breakdown.",
        {"suppliers": [sid], "lost_share": lost}, stock_cover_weeks=rng.choice((1, 2, 3, 4, 6)), source="suite")


def _regional_outage(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    by_country: dict[str, set[str]] = {}
    for c in co.components.values():
        country = co.suppliers[c.supplier].country
        if country:
            by_country.setdefault(country, set()).add(c.supplier)
    country = rng.choice(sorted(k for k, v in by_country.items() if len(v) >= 2))
    picked = sorted(rng.sample(sorted(by_country[country]), min(len(by_country[country]), rng.choice((2, 3)))))
    weeks = rng.choice((2, 3, 4, 6, 8))
    return "regional_outage", Disruption(
        f"Flooding hits {len(picked)} suppliers in {country}", "supplier_outage", rng.choice((0, 0, 1)), weeks,
        f"Flooding stops {len(picked)} suppliers in {country} for about {weeks} weeks.",
        {"suppliers": picked, "lost_share": 1.0}, stock_cover_weeks=rng.choice((2, 3, 4)), source="suite")


def _export_restriction(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    counts = Counter(co.suppliers[c.supplier].country for c in co.components.values())
    country = rng.choice(sorted(k for k, n in counts.items() if k and n >= 3))
    commodities = sorted({c.commodity for c in co.components.values()
                          if c.commodity and co.suppliers[c.supplier].country == country})
    commodity = rng.choice(commodities) if commodities and rng.random() < 0.6 else None
    start = rng.choice((0, 2, 3, 4))
    weeks = rng.choice((6, 8, 10, 12, 16))
    what = commodity.title() if commodity else "all components"
    return "export_restriction", Disruption(
        f"Export licences for {what} from {country}", "export_restriction", start, weeks,
        f"New export licensing in {country} halts shipments of {what.lower()} "
        + (f"from week {start}" if start else "from now") + f" for about {weeks} weeks.",
        {"countries": [country], "commodities": [commodity] if commodity else [], "lost_share": rng.choice((0.7, 1.0))},
        stock_cover_weeks=rng.choice((2, 3, 4)), source="suite")


ROUTES = [(["Asia"], ["Europe", "Africa"], "Asia to Europe"), (["China"], [], "out of China"),
          (["Asia"], ["Americas"], "Asia to the Americas"), (["Europe"], ["Americas"], "Europe to the Americas"),
          (["Mexico"], ["United States"], "Mexico to the United States"), (["Americas"], ["Europe"], "the Americas to Europe")]


def _route_closure(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    origins, dests, label = rng.choice(ROUTES)
    extra = rng.choice((2, 3, 4, 5, 6))
    weeks = rng.choice((4, 6, 8, 10, 12))
    surcharge = rng.choice((0.0, 0.1, 0.2, 0.3, 0.4))
    return "route_closure", Disruption(
        f"Shipping lanes {label} delayed", "route_closure", rng.choice((0, 0, 1, 2)), weeks,
        f"Shipments {label} take {extra} more weeks for {weeks} weeks, with a {surcharge:.0%} freight surcharge.",
        {"origin_countries": origins, "destination_countries": dests, "extra_weeks": extra, "surcharge": surcharge},
        stock_cover_weeks=rng.choice((1, 2, 3, 4)), source="suite")


def _plant_outage(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    served = sorted({s for c in co.components.values() for s in c.sites if s in co.sites})
    site = co.sites[rng.choice(served)]
    weeks = rng.choice((2, 3, 4, 6, 8))
    return "plant_outage", Disruption(
        f"Power cut at {site.name}", "plant_outage", rng.choice((0, 0, 1)), weeks,
        f"A grid failure stops {site.name} ({site.country}) for {weeks} weeks; parts delivered there cannot be used.",
        {"sites": [site.id]}, stock_cover_weeks=rng.choice((1, 2, 3)), source="suite")


def _tariff(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    counts = Counter(co.suppliers[c.supplier].country for c in co.components.values())
    origins = sorted(rng.sample(sorted(k for k, n in counts.items() if k and n >= 3), rng.choice((1, 2))))
    site_countries = sorted({s.country for s in co.sites.values() if s.country})
    dests = [rng.choice(site_countries)] if rng.random() < 0.5 else []
    rate = rng.choice((0.1, 0.25, 0.5))
    weeks = rng.choice((8, 13, 26))
    into = f" into {dests[0]}" if dests else ""
    return "tariff", Disruption(
        f"{rate:.0%} duty on imports from {' and '.join(origins)}{into}", "tariff", rng.choice((0, 0, 2)), weeks,
        f"A {rate:.0%} duty applies to components from {' and '.join(origins)}{into} for {weeks} weeks.",
        {"origin_countries": origins, "destination_countries": dests, "rate": rate},
        stock_cover_weeks=3, source="suite")


def _shortage(co: Company, rng: random.Random) -> tuple[str, Disruption]:
    episodes = shockbench.episodes()
    commodity = rng.choice([k for k in co.commodities() if sum(1 for c in co.components.values()
                                                                  if c.commodity == k) >= 4])
    if episodes:
        ep = rng.choice(episodes)["episode"]
        curve = shockbench.curve(ep) or [0.5] * 8
        source = f"ShockBench episode {ep}"
    else:  # without the ShockBench file: a generic dip
        low = rng.choice((0.2, 0.4, 0.6))
        curve = [1 - (1 - low) * min(1.0, k / 3) for k in range(10)]
        source = "a generic shortage curve"
    curve = curve[:MAX_CURVE_WEEKS]
    return "shortage", Disruption(
        f"{commodity.title()} shortage", "shortage", rng.choice((0, 0, 1, 2)), len(curve),
        f"Market supply of {commodity.lower()} drops to {min(curve):.0%} at the low point, following {source}.",
        {"commodities": [commodity], "curve": curve}, stock_cover_weeks=rng.choice((2, 3, 4)), source="suite")


FAMILIES = [_supplier_outage, _regional_outage, _export_restriction, _route_closure, _plant_outage, _tariff, _shortage]


TARGETS = ("suppliers", "countries", "commodities", "origin_countries", "destination_countries", "sites")


def _key(d: Disruption) -> str:
    """What is hit, not how hard or when: two cases on the same targets count as duplicates."""
    return json.dumps([d.type, {k: sorted(v) for k, v in d.params.items() if k in TARGETS}], sort_keys=True)


def _worth_it(co: Company, d: Disruption) -> bool:
    """Not trivial: doing nothing costs something and the optimiser can save a real amount."""
    try:
        d.validate(co)
    except ValueError:
        return False
    sim = Simulator(co, d)
    if not sim.comps:
        return False
    nothing = sim.run([]).total_cost
    if nothing < MIN_DO_NOTHING_EUR:
        return False
    # a quick lower estimate of what the optimiser saves: the playbook, everything allowed, or any single action
    full = [axis[-1] for c in sim.comps for axis in sim.axes(c) if axis[-1] is not None]
    plans = [sim.playbook(), full] + [[a] for a in sim.candidates()]
    best = min(sim.run(p).total_cost for p in plans)
    return nothing - best >= max(MIN_SAVING_EUR, MIN_GAIN_EUR)


def generate_suite(co: Company, seed: int = DEFAULT_SEED, dev_per_type: int = DEV_PER_TYPE,
                   test_per_type: int = TEST_PER_TYPE) -> dict[str, Any]:
    rng = random.Random(seed)
    cases: list[Case] = []
    seen: set[str] = set()
    for i, d in enumerate(library(co), start=1):
        if _worth_it(co, d):
            cases.append(Case(f"lib-{i}", "dev", "library", d))
            seen.add(_key(d))
    quota = dev_per_type + test_per_type
    taken: Counter[str] = Counter()
    for _ in range(MAX_TRIES):
        if all(taken[t] >= quota for t in TYPES):
            break
        family, d = rng.choice(FAMILIES)(co, rng)
        if taken[d.type] >= quota or _key(d) in seen or not _worth_it(co, d):
            continue
        seen.add(_key(d))
        n = taken[d.type]
        taken[d.type] += 1
        split = "dev" if n < dev_per_type else "test"
        cases.append(Case(f"{d.type}-{n + 1}", split, family, d))
    short = [t for t in TYPES if taken[t] < quota]
    return {"generator_version": GENERATOR_VERSION, "seed": seed, "graph": fingerprint(co),
            "missing": {t: quota - taken[t] for t in short},
            "cases": [c.to_dict() for c in cases]}


def save_suite(suite: dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite, indent=1, ensure_ascii=False) + "\n")


def load_suite(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise SuiteError(f"no suite at {path}; `scgraph ops suite` writes one")
    return json.loads(path.read_text())


def cases_for(suite: dict[str, Any], co: Company, split: str = "test", ids: list[str] | None = None) -> list[Case]:
    """The suite's cases of a split ("dev", "test" or "all"), after checking the graph is the one it was made from."""
    expected, actual = suite["graph"], fingerprint(co)
    if expected != actual:
        diff = ", ".join(f"{k} {expected.get(k)} in the suite, {actual.get(k)} now" for k in expected
                         if expected.get(k) != actual.get(k))
        raise SuiteError(f"the suite was made from a different graph: {diff}")
    cases = [Case.from_dict(c) for c in suite["cases"]]
    if ids:
        unknown = set(ids) - {c.id for c in cases}
        if unknown:
            raise SuiteError(f"unknown cases: {', '.join(sorted(unknown))}")
        return [c for c in cases if c.id in ids]
    return cases if split == "all" else [c for c in cases if c.split == split]
