"""The weekly company model under a disruption, the response actions and their costs, and the reference policies.

Units are weeks of demand: every bought-in component needs 1.0 per week. Each week, supply is drawn from the
cheapest available sources (the usual supplier, then any action-provided source), stock covers shortfalls, the
available units are shared among the assemblies containing the component, and each assembly makes the share
allowed by its scarcest component. Lost revenue is weekly assembly revenue times its lost output share.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from scgraph.ops.company import Company
from scgraph.ops.scenarios import Compound, Disruption, Effects, blocked_origin, effects, supplier_affected

ACTION_TYPES = ("second_source", "new_supplier", "spot_buy", "air_freight", "buffer", "priority")
ACTION_LABELS = {
    "second_source": "Activate approved second source", "new_supplier": "Qualify new supplier",
    "spot_buy": "Spot buy", "air_freight": "Air freight", "buffer": "Buy buffer stock",
    "priority": "Allocation priority",
}

# Assumptions, shown in the app and in reports. Shares are of weekly demand; premiums of the component's value.
RULES = {
    "second_source": {"lead_weeks": 2, "max_share": 0.7, "fixed_eur": 10_000, "premium": 0.05},
    "new_supplier": {"lead_weeks_high": 12, "lead_weeks": 6, "max_share": 1.0, "fixed_eur_high": 80_000,
                     "fixed_eur": 30_000, "premium": 0.10},
    "spot_buy": {"lead_weeks": 1, "max_share": 0.5, "premium": 1.5},
    "air_freight": {"lead_weeks": 1, "premium": 0.6},
    "buffer": {"max_weeks": 8, "rate_per_week": 0.5, "premium": 0.03},
    "supplier_surge": 1.25,
    "stock_cap_weeks": 12.0,
}
SOURCES = ("primary", "second_source", "new_supplier", "spot_buy")
REFERENCES_VERSION = 2  # bump when the references change; cached ones of an older version are recomputed
MIN_GAIN_EUR = 1_000.0
# the optimiser's coordinate search: spot-buy shares and buffer sizes it tries, and its sweep limit
SPOT_SHARES = (0.1, 0.2, 0.3, 0.4, 0.5)
BUFFER_WEEKS = (2, 4, 6, 8)
SEARCH_SWEEPS = 6
SEARCH_SIMS = 15_000  # simulations per search start; keeps a very large disruption from stalling the app
JOINT_BUDGET = 1_500  # every combination per component when all components together have at most this many


class ActionError(ValueError):
    pass


def action_key(action: dict[str, Any]) -> str:
    return action["type"] if action["type"] == "priority" else f"{action['type']}:{action['component']}"


def describe_action(company: Company, action: dict[str, Any]) -> str:
    t = action["type"]
    if t == "priority":
        return f"Allocate scarce parts to assemblies in the order {', '.join(action['assemblies'])}"
    comp = company.components.get(action.get("component"))
    what = f"{action['component']} ({comp.description})" if comp else action.get("component", "?")
    start = f" from week {action['start_week']}" if action.get("start_week") else ""
    if t == "second_source":
        return f"Activate the approved second source for {what}{start}"
    if t == "new_supplier":
        sup = company.suppliers.get(action["supplier"])
        return f"Qualify {sup.name if sup else action['supplier']} ({action['supplier']}) for {what}{start}"
    if t == "spot_buy":
        return f"Spot buy {action['share']:.0%} of weekly demand of {what}{start}"
    if t == "air_freight":
        return f"Air freight {what} while the sea route is delayed{start}"
    if t == "buffer":
        return f"Buy {action['weeks']:g} extra weeks of stock of {what}{start}"
    return t


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """A clean action dict from model or form input; raises ActionError on shape problems."""
    if not isinstance(raw, dict) or raw.get("type") not in ACTION_TYPES:
        raise ActionError(f"action type must be one of {', '.join(ACTION_TYPES)}")
    t = raw["type"]
    out: dict[str, Any] = {"type": t}
    if t == "priority":
        assemblies = raw.get("assemblies")
        if not isinstance(assemblies, list) or not assemblies:
            raise ActionError("priority needs a non-empty list of assembly ids")
        out["assemblies"] = [str(a) for a in assemblies]
        return out
    if not raw.get("component"):
        raise ActionError(f"{t} needs a component id")
    out["component"] = str(raw["component"])
    try:
        out["start_week"] = int(raw.get("start_week") or 0)
        if t == "new_supplier":
            if not raw.get("supplier"):
                raise ActionError("new_supplier needs a supplier id")
            out["supplier"] = str(raw["supplier"])
        if t == "spot_buy":
            out["share"] = float(raw.get("share", RULES["spot_buy"]["max_share"]))
        if t == "buffer":
            out["weeks"] = float(raw.get("weeks", 4))
    except (TypeError, ValueError):
        raise ActionError(f"{t}: numbers expected for start_week, share or weeks") from None
    return out


def check(company: Company, d: Disruption, eff: Effects, action: dict[str, Any]) -> str | None:
    """Why an action is not allowed, or None."""
    t = action["type"]
    if t == "priority":
        unknown = [a for a in action["assemblies"] if a not in company.assemblies]
        return f"unknown assemblies: {', '.join(unknown)}" if unknown else None
    cid = action["component"]
    comp = company.components.get(cid)
    if comp is None:
        return f"{cid} is not a bought-in component of the graph"
    if cid not in eff.reasons:
        return f"{cid} is not affected by this disruption"
    if not 0 <= action.get("start_week", 0) < d.horizon_weeks:
        return f"start_week must be within 0..{d.horizon_weeks - 1}"
    if t == "second_source":
        if comp.single_source is not False:
            return f"{cid} has no approved second source (single-source: {comp.single_source})"
        if cid in eff.usable:
            return "our own plant is down; a second source cannot deliver into it"
    elif t == "new_supplier":
        sid = action["supplier"]
        if sid not in company.candidates(cid):
            return (f"{sid} does not supply the sub-commodity of {cid}; candidates: "
                    f"{', '.join(company.candidates(cid)[:8]) or 'none'}")
        r = RULES["new_supplier"]
        ready = action.get("start_week", 0) + (r["lead_weeks_high"] if comp.high_criticality else r["lead_weeks"])
        if supplier_affected(company, d, sid, ready):
            return f"{sid} is hit by the same disruption"
        country = company.suppliers[sid].country
        if blocked_origin(d, country, ready):
            return f"{sid} is in {country}, on the same affected lanes"
        if cid in eff.usable:
            return "our own plant is down; a new supplier cannot deliver into it"
    elif t == "spot_buy":
        if not 0 < action["share"] <= RULES["spot_buy"]["max_share"]:
            return f"spot buy share must be within 0..{RULES['spot_buy']['max_share']}"
        if cid in eff.usable:
            return "our own plant is down; spot buys cannot deliver into it"
    elif t == "air_freight":
        if cid not in eff.gap:
            return f"{cid} is not on a delayed route; air freight only bridges route delays"
    elif t == "buffer":
        if not 0 < action["weeks"] <= RULES["buffer"]["max_weeks"]:
            return f"buffer weeks must be within 0..{RULES['buffer']['max_weeks']}"
        if action.get("start_week", 0) >= eff.onset(cid):
            return "buffer stock must be bought before the disruption reaches the component (the supplier is already affected)"
    return None


@dataclass
class Outcome:
    total_cost: float
    lost_margin: float
    lost_revenue: float
    action_costs: dict[str, float]
    disruption_costs: float  # tariffs and freight surcharges paid on the usual supply
    weekly_lost_revenue: list[float]
    lost_revenue_by_assembly: dict[str, float]
    assembly_output: dict[str, list[float]]
    first_stop_week: int | None
    short_components: dict[str, int]  # component -> weeks with less than full supply to its assemblies
    refused: list[dict[str, Any]] = field(default_factory=list)
    component_costs: dict[str, float] = field(default_factory=dict)  # actions, tariffs and surcharges per component

    @property
    def action_cost(self) -> float:
        return sum(self.action_costs.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost": round(self.total_cost, 2), "lost_margin": round(self.lost_margin, 2),
            "lost_revenue": round(self.lost_revenue, 2), "action_cost": round(self.action_cost, 2),
            "action_costs": {k: round(v, 2) for k, v in self.action_costs.items()},
            "disruption_costs": round(self.disruption_costs, 2),
            "weekly_lost_revenue": [round(v, 2) for v in self.weekly_lost_revenue],
            "lost_revenue_by_assembly": {k: round(v, 2) for k, v in self.lost_revenue_by_assembly.items()},
            "assembly_output": {k: [round(x, 4) for x in v] for k, v in self.assembly_output.items()},
            "first_stop_week": self.first_stop_week, "short_components": self.short_components,
            "refused": self.refused,
            "component_costs": {k: round(v, 2) for k, v in self.component_costs.items()},
        }


class Simulator:
    """Simulates plans for one disruption; the effects and matrices are computed once."""

    def __init__(self, company: Company, d: Disruption, eff: Effects | None = None) -> None:
        self.company, self.d = company, d
        self.eff = eff if eff is not None else effects(company, d)
        self.h = d.horizon_weeks
        self.comps = self.eff.components
        self.index = {c: i for i, c in enumerate(self.comps)}
        self.assemblies = sorted({a for c in self.comps for a in company.components[c].assemblies})
        self.aindex = {a: j for j, a in enumerate(self.assemblies)}
        n, m, h = len(self.comps), len(self.assemblies), self.h
        self.w = np.zeros((n, m))
        for i, c in enumerate(self.comps):
            comp = company.components[c]
            for a in comp.assemblies:
                self.w[i, self.aindex[a]] = 1 / len(comp.assemblies)
        self.value = np.array([company.components[c].weekly_value for c in self.comps])
        self.capacity = np.ones((n, h))
        self.gap = np.zeros((n, h), dtype=bool)
        self.primary_cost = np.zeros((n, h))  # tariff + surcharge share
        self.usable = np.ones((n, h))
        for i, c in enumerate(self.comps):
            if c in self.eff.capacity:
                self.capacity[i] = self.eff.capacity[c]
            if c in self.eff.gap:
                self.gap[i] = self.eff.gap[c]
            if c in self.eff.surcharge:
                self.primary_cost[i] += self.eff.surcharge[c]
            if c in self.eff.tariff:
                self.primary_cost[i] += self.eff.tariff[c]
            if c in self.eff.usable:
                self.usable[i] = self.eff.usable[c]
        self.revenue = np.array([company.assemblies[a].weekly_revenue for a in self.assemblies])

    def run(self, actions: list[dict[str, Any]] | None = None, *, strict: bool = False) -> Outcome:
        """Simulate a plan. Refused actions are skipped and listed (or raise with `strict`)."""
        n, m, h = len(self.comps), len(self.assemblies), self.h
        rules = RULES
        plan: dict[str, dict[str, Any]] = {}
        refused = []
        for raw in actions or []:
            try:
                action = normalize(raw)
                reason = check(self.company, self.d, self.eff, action)
            except ActionError as e:
                action, reason = raw, str(e)
            if reason is None and action_key(action) in plan:
                reason = f"duplicate action {action_key(action)}"
            if reason:
                if strict:
                    raise ActionError(reason)
                refused.append({"action": raw, "reason": reason})
                continue
            plan[action_key(action)] = action

        # source capacity (n x h) and unit premium (n x h) per source
        cap = {s: np.zeros((n, h)) for s in SOURCES}
        premium = {s: np.zeros((n, h)) for s in SOURCES}
        # a supplier at full capacity can deliver a little extra to rebuild stock; a reduced one cannot
        surge = np.where(self.capacity >= 1.0, rules["supplier_surge"], self.capacity)
        cap["primary"] = surge.copy()
        cap["primary"][self.gap] = 0.0
        premium["primary"] = self.primary_cost.copy()
        air = np.zeros((n, h), dtype=bool)
        buffer_left = np.zeros(n)
        buffer_from = np.full(n, h)
        fixed: dict[str, float] = {}
        priority: list[int] | None = None
        weeks = np.arange(h)
        for key, a in plan.items():
            if a["type"] == "priority":
                order = [self.aindex[x] for x in a["assemblies"] if x in self.aindex]
                priority = order + [j for j in range(m) if j not in order]
                continue
            i, start = self.index[a["component"]], a.get("start_week", 0)
            comp = self.company.components[a["component"]]
            if a["type"] == "second_source":
                r = rules["second_source"]
                cap["second_source"][i] = np.where(weeks >= start + r["lead_weeks"], r["max_share"], 0.0)
                premium["second_source"][i] = r["premium"]
                fixed[key] = r["fixed_eur"]
            elif a["type"] == "new_supplier":
                r = rules["new_supplier"]
                lead = r["lead_weeks_high"] if comp.high_criticality else r["lead_weeks"]
                cap["new_supplier"][i] = np.where(weeks >= start + lead, r["max_share"], 0.0)
                premium["new_supplier"][i] = r["premium"]
                fixed[key] = r["fixed_eur_high"] if comp.high_criticality else r["fixed_eur"]
            elif a["type"] == "spot_buy":
                r = rules["spot_buy"]
                cap["spot_buy"][i] = np.where(weeks >= start + r["lead_weeks"], a["share"], 0.0)
                premium["spot_buy"][i] = r["premium"]
            elif a["type"] == "air_freight":
                r = rules["air_freight"]
                bridged = self.gap[i] & (weeks >= start + r["lead_weeks"])
                air[i] = bridged
                cap["primary"][i, bridged] = surge[i, bridged]
                premium["primary"][i, bridged] += r["premium"]
            elif a["type"] == "buffer":
                buffer_left[i] = a["weeks"]
                buffer_from[i] = start

        stock = np.full(n, float(self.d.stock_cover_weeks))
        target = float(self.d.stock_cover_weeks)
        # money per component, summed over the weeks: per source, air freight, buffer, tariffs and surcharges
        spend = np.zeros((len(SOURCES), n))
        used_source = np.zeros((len(SOURCES), n), dtype=bool)
        air_spend = np.zeros(n)
        buffer_spend = np.zeros(n)
        disruption_spend = np.zeros(n)
        output = np.ones((m, h))
        short = np.zeros(n, dtype=int)
        cols = np.arange(n)
        primary = SOURCES.index("primary")
        air_rate = rules["air_freight"]["premium"]
        prem_all = np.stack([premium[s] for s in SOURCES])  # sources x n x h
        cap_all = np.stack([cap[s] for s in SOURCES])
        for t in range(h):
            # buffer purchases from the usual supplier while it is unaffected
            buy = np.where((t >= buffer_from) & (self.capacity[:, t] >= 1.0) & ~self.gap[:, t],
                           np.minimum(buffer_left, rules["buffer"]["rate_per_week"]), 0.0)
            buffer_left -= buy
            buffer_spend += buy * self.value * rules["buffer"]["premium"]
            desired = 1.0 + np.clip(target - stock, 0.0, rules["supplier_surge"] - 1.0)
            remaining = desired.copy()
            arrivals = buy.copy()
            # cheapest sources first, per component
            prem = prem_all[:, :, t]
            caps = cap_all[:, :, t]
            order = np.argsort(prem, axis=0, kind="stable")
            for rank in range(len(SOURCES)):
                idx = order[rank]
                take = np.minimum(remaining, caps[idx, cols])
                remaining -= take
                arrivals += take
                unit = prem[idx, cols] * self.value * take
                is_primary = idx == primary
                extra = np.where(is_primary & air[:, t], air_rate * self.value * take, 0.0)
                air_spend += extra
                disruption_spend += np.where(is_primary, unit - extra, 0.0)
                other = ~is_primary & (take > 0)
                np.add.at(spend, (idx[other], cols[other]), unit[other])
                used_source[idx[other], cols[other]] = True
            avail = np.minimum(stock + arrivals, rules["stock_cap_weeks"] + 1.0)
            usable = avail * self.usable[:, t]
            # allocation to assemblies: share of each assembly's need it can make
            if m:
                frac = np.ones((n, m))
                if priority is None:
                    frac = np.where(self.w > 0, np.minimum(1.0, usable)[:, None], 1.0)
                else:
                    left = usable.copy()
                    for j in priority:
                        need = self.w[:, j]
                        give = np.minimum(need, left)
                        left -= give
                        frac[:, j] = np.where(need > 0, give / np.where(need > 0, need, 1.0), 1.0)
                out = frac.min(axis=0)
                out = np.where(out > 1 - 1e-9, 1.0, out)  # float dust from stock arithmetic
                output[:, t] = out
                used = (self.w * out[None, :]).sum(axis=1)
                short += ((frac < 1 - 1e-9) & (self.w > 0)).any(axis=1)
            else:
                used = np.minimum(1.0, usable)
            stock = np.minimum(avail - used, rules["stock_cap_weeks"])

        costs = dict(fixed)
        for s, source in enumerate(SOURCES):
            for i in np.nonzero(used_source[s])[0]:
                k = f"{source}:{self.comps[i]}"
                costs[k] = costs.get(k, 0.0) + spend[s, i]
        for name, values in (("air_freight", air_spend), ("buffer", buffer_spend)):
            for i in np.nonzero(values)[0]:
                costs[f"{name}:{self.comps[i]}"] = values[i]
        by_component = disruption_spend.copy()
        for k, v in costs.items():
            if k != "priority":
                by_component[self.index[k.split(":", 1)[1]]] += v
        disruption_costs = float(disruption_spend.sum())

        lost = (1 - output) * self.revenue[:, None] if m else np.zeros((0, h))
        weekly = lost.sum(axis=0) if m else np.zeros(h)
        lost_revenue = float(weekly.sum())
        lost_margin = lost_revenue * self.company.margin
        stopped = np.nonzero((output < 1 - 1e-6).any(axis=0))[0] if m else []
        return Outcome(
            total_cost=float(lost_margin + sum(costs.values()) + disruption_costs),
            lost_margin=float(lost_margin), lost_revenue=lost_revenue, action_costs={k: float(v) for k, v in costs.items()},
            disruption_costs=disruption_costs, weekly_lost_revenue=weekly.tolist(),
            component_costs={c: float(by_component[i]) for i, c in enumerate(self.comps) if by_component[i] > 0.5},
            lost_revenue_by_assembly={a: float(lost[j].sum()) for j, a in enumerate(self.assemblies)},
            assembly_output={a: output[j].tolist() for j, a in enumerate(self.assemblies)},
            first_stop_week=int(stopped[0]) if len(stopped) else None,
            short_components={c: int(short[i]) for i, c in enumerate(self.comps) if short[i]},
            refused=refused,
        )

    # --- references -------------------------------------------------------------------------------------------

    def playbook(self) -> list[dict[str, Any]]:
        """Second source where allowed, otherwise spot buy half the demand; air freight on delayed lanes."""
        plan = []
        for c in self.comps:
            if c in self.eff.gap:
                plan.append({"type": "air_freight", "component": c})
                continue
            if c in self.eff.usable:
                continue
            for a in ({"type": "second_source", "component": c},
                      {"type": "spot_buy", "component": c, "share": RULES["spot_buy"]["max_share"]}):
                if check(self.company, self.d, self.eff, normalize(a)) is None:
                    plan.append(a)
                    break
        return plan

    def candidates(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if len(self.assemblies) > 1:
            out.append({"type": "priority", "assemblies": sorted(
                self.assemblies, key=lambda a: -self.company.assemblies[a].revenue)})
        for c in self.comps:
            options = [{"type": "second_source", "component": c},
                       {"type": "spot_buy", "component": c, "share": 0.25},
                       {"type": "spot_buy", "component": c, "share": 0.5},
                       {"type": "air_freight", "component": c}]
            if self.d.start_week > 0:
                options += [{"type": "buffer", "component": c, "weeks": w} for w in (2, 4, 8)]
            allowed = [s for s in self.company.candidates(c)
                       if check(self.company, self.d, self.eff,
                                {"type": "new_supplier", "component": c, "supplier": s}) is None]
            if allowed:
                best = min(allowed, key=lambda s: (self.company.suppliers[s].risk_score or 50.0,
                                                   self.company.suppliers[s].tier or 9, s))
                options.append({"type": "new_supplier", "component": c, "supplier": best})
            out += [normalize(o) for o in options if check(self.company, self.d, self.eff, normalize(o)) is None]
        return out

    def optimise(self, max_steps: int = 40, sweeps: int = SEARCH_SWEEPS) -> list[dict[str, Any]]:
        """Greedy from an empty plan and from the playbook, then a coordinate search on the cheaper result."""
        pool = self.candidates()
        plans = [self._greedy({}, pool, max_steps),
                 self._greedy({action_key(normalize(a)): normalize(a) for a in self.playbook()}, pool, max_steps)]
        best = min(plans, key=lambda p: self.run(p).total_cost)
        if not sweeps:
            return best
        # a second start with every allowed action on every component: removing pieces from a fully covered plan
        # shows each one's worth where adding to a plan cannot (another part still stops the same assembly)
        full = [axis[-1] for c in self.comps for axis in self.axes(c) if axis[-1] is not None]
        searched = []
        for start in (best, full):
            plan = self._coordinate(start, sweeps)
            plan = self._coordinate(plan, sweeps, swaps=True)  # only accepts improvements, so never worse
            searched.append(self._timing(plan, sweeps))
        return min(searched, key=lambda p: self.run(p).total_cost)

    def _timing(self, plan: list[dict[str, Any]], sweeps: int) -> list[dict[str, Any]]:
        """Later start weeks for the plan's actions: using the stock on hand first can make an action cheaper."""
        cover = int(self.d.stock_cover_weeks)

        def starts(a: dict[str, Any]) -> list[int]:
            # several events (a compound): around the week the component is first hit, not the first event's
            compound = isinstance(self.d, Compound)
            base = self.eff.onset(a["component"]) if compound else self.d.start_week
            return sorted({0} | {w for k in range(-cover if compound else 0, cover + 2)
                                 if 0 <= (w := base + k) < self.h})

        plan = [normalize(a) for a in plan]
        best = self.run(plan).total_cost
        for _ in range(sweeps):
            changed = False
            for i, a in enumerate(plan):
                if a["type"] in ("priority", "buffer"):
                    continue
                for w in starts(a):
                    if w == a.get("start_week", 0):
                        continue
                    trial = plan[:i] + [{**a, "start_week": w}] + plan[i + 1:]
                    cost = self.run(trial).total_cost
                    if cost < best - MIN_GAIN_EUR / 10:
                        best, plan, a, changed = cost, trial, trial[i], True
            if not changed:
                break
        return plan

    def axes(self, cid: str) -> list[list[dict[str, Any] | None]]:
        """One component's allowed choices per action type, each list starting with "none"."""
        def allowed(a: dict[str, Any]) -> bool:
            return check(self.company, self.d, self.eff, normalize(a)) is None

        new = [s for s in self.company.candidates(cid) if allowed({"type": "new_supplier", "component": cid, "supplier": s})]
        new_choice = min(new, key=lambda s: (self.company.suppliers[s].risk_score or 50.0,
                                             self.company.suppliers[s].tier or 9, s)) if new else None
        raw = [[{"type": "second_source", "component": cid}],
               [{"type": "new_supplier", "component": cid, "supplier": new_choice}] if new_choice else [],
               [{"type": "spot_buy", "component": cid, "share": s} for s in SPOT_SHARES],
               [{"type": "air_freight", "component": cid}],
               [{"type": "buffer", "component": cid, "weeks": w} for w in BUFFER_WEEKS]]
        return [[None] + [normalize(a) for a in axis if allowed(a)] for axis in raw]

    def _coordinate(self, plan: list[dict[str, Any]], sweeps: int, swaps: bool = False) -> list[dict[str, Any]]:
        """Per component, the cheapest choice of its actions with the rest of the plan fixed (every combination when
        the case is small enough, otherwise one action type at a time, with `swaps` also one action alone); then
        the allocation priority. Repeats until a sweep changes nothing."""
        axes = {c: self.axes(c) for c in self.comps}
        slot = {t: k for k, t in enumerate(("second_source", "new_supplier", "spot_buy", "air_freight", "buffer"))}
        state: dict[str, list[dict[str, Any] | None]] = {c: [None] * len(axes[c]) for c in self.comps}
        priority: dict[str, Any] | None = None
        for a in map(normalize, plan):
            if a["type"] == "priority":
                priority = a
            else:
                state[a["component"]][slot[a["type"]]] = a

        def flat() -> list[dict[str, Any]]:
            return [a for c in self.comps for a in state[c] if a is not None] + ([priority] if priority else [])

        joint = sum(math.prod(len(axis) for axis in axes[c]) for c in self.comps) <= JOINT_BUDGET

        def moves(c: str) -> list[list[dict[str, Any] | None]]:
            if joint:
                return [list(combo) for combo in itertools.product(*axes[c])]
            # one action type changed at a time, or the component's actions replaced by a single one (a swap)
            single = [state[c][:k] + [choice] + state[c][k + 1:] for k, axis in enumerate(axes[c]) for choice in axis]
            if not swaps:
                return single
            alone = [[choice if j == k else None for j in range(len(axes[c]))]
                     for k, axis in enumerate(axes[c]) for choice in axis[1:]]
            return single + alone + [[None] * len(axes[c])]

        ranked = (None if len(self.assemblies) < 2 else
                  normalize({"type": "priority", "assemblies": sorted(
                      self.assemblies, key=lambda a: -self.company.assemblies[a].revenue)}))
        best = self.run(flat()).total_cost
        sims = 0
        for _ in range(sweeps):
            changed = False
            for c in self.comps:
                current = state[c]
                for choice in moves(c):
                    sims += 1
                    if sims > SEARCH_SIMS:
                        break
                    state[c] = choice
                    cost = self.run(flat()).total_cost
                    if cost < best - MIN_GAIN_EUR / 10:
                        best, current, changed = cost, choice, True
                    state[c] = current
            if ranked is not None:
                kept = priority
                for choice in (None, ranked):
                    priority = choice
                    cost = self.run(flat()).total_cost
                    if cost < best - MIN_GAIN_EUR / 10:
                        best, kept, changed = cost, choice, True
                priority = kept
            if not changed:
                break
        return flat()

    def _greedy(self, plan: dict[str, dict[str, Any]], pool: list[dict[str, Any]], max_steps: int) -> list[dict[str, Any]]:
        """Keep adding (or swapping in) the one action that lowers total cost most, then drop actions that do not pay."""
        plan = dict(plan)
        best = self.run(list(plan.values())).total_cost
        for _ in range(max_steps):
            choice, choice_cost = None, best - MIN_GAIN_EUR
            for cand in pool:
                key = action_key(cand)
                if plan.get(key) == cand:
                    continue
                trial = {**plan, key: cand}
                cost = self.run(list(trial.values())).total_cost
                if cost < choice_cost:
                    choice, choice_cost = cand, cost
            if choice is None:
                break
            plan[action_key(choice)] = choice
            best = choice_cost
        for key in list(plan):
            trial = {k: v for k, v in plan.items() if k != key}
            cost = self.run(list(trial.values())).total_cost
            if cost <= best:
                plan, best = trial, cost
        return list(plan.values())

    def references(self) -> dict[str, Any]:
        nothing = self.run([])
        playbook_plan = self.playbook()
        playbook = self.run(playbook_plan)
        optimiser_plan = self.optimise()
        optimiser = self.run(optimiser_plan)
        return {
            "version": REFERENCES_VERSION,
            "do_nothing": {"plan": [], "outcome": nothing.to_dict()},
            "playbook": {"plan": playbook_plan, "outcome": playbook.to_dict(),
                         "skill": skill(nothing.total_cost, optimiser.total_cost, playbook.total_cost)},
            "optimiser": {"plan": optimiser_plan, "outcome": optimiser.to_dict(), "skill": 1.0
                          if nothing.total_cost - optimiser.total_cost > MIN_GAIN_EUR else None},
        }


def skill(nothing: float, optimiser: float, plan: float) -> float | None:
    """Cost reduction against doing nothing, as a share of the optimiser's reduction (None if it saves nothing)."""
    gain = nothing - optimiser
    if gain <= MIN_GAIN_EUR:
        return None
    return round((nothing - plan) / gain, 4)
