"""Disruption tools: the overview, component details, alternative suppliers and plan simulation.

Everything about the disruption comes from the world's view of it, and simulation runs on that view.
"""

from __future__ import annotations

import json
from typing import Any

from scgraph.agent.gate import eur
from scgraph.agent.tools import Tool, Turn, obj
from scgraph.ops.scenarios import TYPE_LABELS, supplier_affected
from scgraph.ops.simulate import ACTION_TYPES, RULES, check, normalize

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(ACTION_TYPES)},
        "component": {"type": "string", "description": "component id (all types except priority)"},
        "supplier": {"type": "string", "description": "supplier id (new_supplier)"},
        "share": {"type": "number", "description": "share of weekly demand (spot_buy)"},
        "weeks": {"type": "number", "description": "weeks of extra stock (buffer)"},
        "start_week": {"type": "integer", "description": "week the action starts, default 0"},
        "assemblies": {"type": "array", "items": {"type": "string"}, "description": "assembly ids in order (priority)"},
    },
    "required": ["type"],
}
COMPONENTS = obj({"component": {"type": "string", "description": "one component id"},
                  "components": {"type": "array", "items": {"type": "string"},
                                 "description": "several component ids (up to 20) in one call"}})
# Actions `fill_gaps` tries for a component that is still short, in this order; each only where the rules allow it.
FILL_ORDER = ("air_freight", "second_source", "spot_buy")


ACTION_EXAMPLE = '[{"type": "spot_buy", "component": "DMC-1234", "share": 0.5, "start_week": 3}]'
TEXT_FIELDS = ("type", "component", "supplier")


def actions_arg(args: dict[str, Any]) -> list[Any]:
    """The `actions` input as a list; models sometimes send it as a JSON string (with text after the list), a single
    object, or ids with stray whitespace."""
    value = args.get("actions")
    if value is None:
        return []
    if isinstance(value, str):
        value = _list_in(value)
    if isinstance(value, dict):
        value = value.get("actions", [value]) if "type" not in value else [value]
    if not isinstance(value, list):
        raise ValueError(f"actions must be a list of action objects, like {ACTION_EXAMPLE}")
    return [{k: v.strip() if k in TEXT_FIELDS and isinstance(v, str) else v for k, v in a.items()}
            if isinstance(a, dict) else a for a in value]


def _list_in(text: str) -> Any:
    """The JSON value at the start of `text`, ignoring anything after it (`[], "summary": ...` reads as [])."""
    try:
        return json.JSONDecoder().raw_decode(text.strip())[0]
    except json.JSONDecodeError as e:
        raise ValueError(f"actions must be a list of action objects, like {ACTION_EXAMPLE} (could not read the "
                         f"string: {e})") from None


def text_after_list(text: Any) -> str:
    """For `actions` sent as a string: the text after the list, which models use for the summary when they mix the
    two up."""
    if not isinstance(text, str):
        return ""
    try:
        _, end = json.JSONDecoder().raw_decode(text.strip())
    except json.JSONDecodeError:
        return ""
    rest = text.strip()[end:].lstrip(" ,\n")
    return rest.removeprefix('"summary"').lstrip(' :>"\n').rstrip(' "}\n')


def components_arg(args: dict[str, Any], limit: int = 20) -> list[str]:
    """`component` or `components` (a list, or a string of ids), stripped; at most `limit`."""
    raw = args.get("components") or args.get("component") or []
    if isinstance(raw, str):
        raw = _list_in(raw) if raw.strip().startswith("[") else raw.replace(",", " ").split()
    ids = [str(c).strip() for c in raw if str(c).strip()]
    if not ids:
        raise ValueError("give `component` (one id) or `components` (a list of ids)")
    return ids[:limit]


class OpsTools:
    def __init__(self, turn: Turn) -> None:
        assert turn.disruption_id is not None, "ops tools need a disruption"
        self.turn, self.world, self.did = turn, turn.world, turn.disruption_id
        self.company = self.world.company()
        self.d = self.world.view(self.did)
        self.sim = self.world.simulator(self.did)
        self.nothing = self.sim.run([])

    def tools(self) -> list[Tool]:
        return [
            Tool("disruption_overview", "The disruption as currently known: affected components, assemblies "
                 "reached and the cost if nothing is done.", obj({}), lambda _: self.overview()),
            Tool("component_details", "Details of bought-in components: supplier, sites, assemblies, value, "
                 "criticality, single-source flag, Business Exposure and how the disruption affects them. Pass "
                 "`components` to get several in one call.", COMPONENTS, self.component_details),
            Tool("alternative_suppliers", "Suppliers of the same sub-commodity that could be qualified for a "
                 "component, with country, tier, risk score and whether the disruption hits them. Pass "
                 "`components` to get several in one call.", COMPONENTS, self.alternative_suppliers),
            Tool("simulate_plan", "Simulate a candidate plan over the horizon. Returns total cost, the change "
                 "against doing nothing, lost revenue, action costs, the first week production stops, components "
                 "still short, the assemblies they block, and any refused actions with the reason, under a "
                 "simulation_id that submit_plan accepts. With fill_gaps true it first adds, for every component the plan leaves short, the actions the rules "
                 "allow (air freight on delayed routes, else a second source and a spot buy at the maximum share) "
                 "and returns them as `added_actions`.",
                 obj({"actions": {"type": "array", "items": ACTION_SCHEMA},
                      "fill_gaps": {"type": "boolean", "description": "add actions for every component left short"}},
                     ["actions"]),
                 lambda args: self.simulate(actions_arg(args), fill_gaps=args.get("fill_gaps") is True)),
        ]

    def overview(self) -> str:
        c, d, n = self.company, self.d, self.nothing
        lines = [f"# Disruption: {d.name}", f"Type: {TYPE_LABELS[d.type]}. Starts week {d.start_week}, lasts "
                 f"{d.duration_text()}; horizon {d.horizon_weeks} weeks.", d.description, "",
                 "## Affected bought-in components"]
        for cid in self.sim.comps:
            comp = c.components[cid]
            sup = c.suppliers[comp.supplier]
            lines.append(
                f"- id {cid}: {comp.description} | {comp.commodity or '-'} / {comp.sub_commodity or '-'} | criticality "
                f"{comp.criticality or '-'} | single-source {comp.single_source} | supplier {sup.id} {sup.name} "
                f"({sup.country}) | value {eur(comp.weekly_value)}/week | in assemblies {', '.join(comp.assemblies) or '-'}"
                f" | {self.sim.eff.reasons[cid]} | weeks short if nothing is done: {n.short_components.get(cid, 0)}"
                f" | allowed: {self.allowed(cid)}")
        lines += ["", "## Assemblies reached"]
        for a in self.sim.assemblies:
            asm = c.assemblies[a]
            lines.append(f"- {a} ({asm.business_unit or '-'}): revenue {eur(asm.weekly_revenue)}/week; lost revenue if "
                         f"nothing is done {eur(n.lost_revenue_by_assembly.get(a, 0))}")
        lines += ["", "## If nothing is done", f"Total cost {eur(n.total_cost)} (lost revenue {eur(n.lost_revenue)}, "
                  f"lost margin {eur(n.lost_margin)}, tariffs and surcharges {eur(n.disruption_costs)}). First week "
                  f"with lost output: {n.first_stop_week if n.first_stop_week is not None else 'none'}."]
        if n.component_costs:
            top = sorted(n.component_costs.items(), key=lambda kv: -kv[1])[:15]
            lines.append("Tariffs and surcharges by component: " + ", ".join(f"{k} {eur(v)}" for k, v in top) + ".")
        return "\n".join(lines)

    def allowed(self, cid: str) -> str:
        """The action types this component allows, as one short phrase for the overview."""
        def ok(action: dict[str, Any]) -> bool:
            return check(self.company, self.d, self.sim.eff, normalize(action)) is None

        out = []
        if ok({"type": "second_source", "component": cid}):
            out.append("second_source")
        usable = [s for s in self.company.candidates(cid)
                  if ok({"type": "new_supplier", "component": cid, "supplier": s})]
        if usable:
            out.append(f"new_supplier ({len(usable)} candidate{'s' if len(usable) > 1 else ''})")
        if ok({"type": "spot_buy", "component": cid}):
            out.append("spot_buy")
        if ok({"type": "air_freight", "component": cid}):
            out.append("air_freight")
        if ok({"type": "buffer", "component": cid}):
            out.append(f"buffer (start before week {self.d.start_week})")
        return ", ".join(out) or "none (only the allocation priority)"

    def component_details(self, args: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        ids = components_arg(args)
        out = [self._component(cid) for cid in ids]
        return out[0] if len(out) == 1 and "components" not in args else out

    def _component(self, cid: str) -> dict[str, Any]:
        comp = self.company.components.get(cid)
        if comp is None:
            raise ValueError(f"{cid!r} is not a bought-in component")
        sup = self.company.suppliers[comp.supplier]
        return {
            "id": cid, "description": comp.description, "commodity": comp.commodity,
            "sub_commodity": comp.sub_commodity, "criticality": comp.criticality, "single_source": comp.single_source,
            "weekly_value_eur": round(comp.weekly_value), "value_estimated": comp.value_estimated,
            "supplier": {"id": sup.id, "name": sup.name, "country": sup.country, "tier": sup.tier,
                         "risk_score": sup.risk_score, "risk_flags": sup.flags},
            "sites": [{"id": s, "country": self.company.sites[s].country} for s in comp.sites if s in self.company.sites],
            "assemblies": comp.assemblies,
            "business_exposure_pct": round(self.company.exposure_pct.get(cid, 0.0), 2),
            "affected": self.sim.eff.reasons.get(cid, "not affected"),
            "weeks_short_if_nothing_done": self.nothing.short_components.get(cid, 0),
            "allowed": self.allowed(cid),
        }

    def alternative_suppliers(self, args: dict[str, Any]) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        ids = components_arg(args)
        out = {cid: self._alternatives(cid) for cid in ids}
        return out[ids[0]] if len(ids) == 1 and "components" not in args else out

    def _alternatives(self, cid: str) -> list[dict[str, Any]]:
        if cid not in self.company.components:
            raise ValueError(f"{cid!r} is not a bought-in component")
        out = []
        for sid in self.company.candidates(cid):
            sup = self.company.suppliers[sid]
            refusal = check(self.company, self.d, self.sim.eff, {"type": "new_supplier", "component": cid,
                                                                 "supplier": sid, "start_week": 0})
            out.append({"id": sid, "name": sup.name, "country": sup.country, "tier": sup.tier,
                        "risk_score": sup.risk_score, "risk_flags": sup.flags,
                        "usable": refusal is None and not supplier_affected(self.company, self.d, sid),
                        **({"why_not": refusal} if refusal else {})})
        return sorted(out, key=lambda s: (not s["usable"], s["risk_score"] or 50, s["id"]))[:15]

    def simulate(self, actions: list[dict[str, Any]], fill_gaps: bool = False) -> dict[str, Any]:
        added: list[dict[str, Any]] = []
        if fill_gaps:
            added = self.gap_actions(actions, self.sim.run(actions).short_components)
            actions = list(actions) + added
        o = self.sim.run(actions)
        sid = len(self.turn.simulations) + 1
        self.turn.simulations.append({"id": sid, "actions": [a for a in actions if isinstance(a, dict)],
                                      "total_cost": o.total_cost, "refused": bool(o.refused)})
        out = {
            "simulation_id": sid,
            "total_cost": eur(o.total_cost), "change_vs_doing_nothing": eur(o.total_cost - self.nothing.total_cost),
            "lost_revenue": eur(o.lost_revenue), "lost_margin": eur(o.lost_margin),
            "action_costs": {k: eur(v) for k, v in o.action_costs.items()},
            "tariffs_and_surcharges": eur(o.disruption_costs),
            "first_week_with_lost_output": o.first_stop_week,
            "components_still_short": {k: f"{v} weeks" for k, v in o.short_components.items()},
            "blocked_assemblies": self.blocked(o),
            "money_by_component": {k: eur(v) for k, v in sorted(o.component_costs.items(), key=lambda kv: -kv[1])},
            "refused": o.refused,
        }
        if fill_gaps:
            out["added_actions"] = added
        if any(len(b["short_components"]) > 1 for b in out["blocked_assemblies"]):
            out["note"] = ("An assembly makes only as much as its scarcest part: covering some of an assembly's short "
                           "components shows no gain until all of them are covered. Cover them together in one "
                           "simulation (fill_gaps does this), then remove actions one at a time.")
        return out

    def blocked(self, o: Any, limit: int = 8) -> list[dict[str, Any]]:
        """The assemblies losing the most revenue, each with the short components it contains."""
        out = []
        for a, lost in sorted(o.lost_revenue_by_assembly.items(), key=lambda kv: -kv[1]):
            if lost <= 0 or len(out) >= limit:
                break
            short = [c for c in o.short_components if a in self.company.components[c].assemblies]
            out.append({"assembly": a, "lost_revenue": eur(lost), "short_components": short})
        return out

    def gap_actions(self, actions: list[dict[str, Any]], short: dict[str, int]) -> list[dict[str, Any]]:
        """The actions `fill_gaps` adds: for each short component, every type in FILL_ORDER the rules allow that the
        plan does not have yet (air freight alone where the route is delayed)."""
        have = {(a.get("type"), a.get("component")) for a in actions if isinstance(a, dict)}
        start = self.world.now()
        out = []
        for cid in short:
            for kind in FILL_ORDER:
                a = {"type": kind, "component": cid, "start_week": start}
                if kind == "spot_buy":
                    a["share"] = RULES["spot_buy"]["max_share"]
                if (kind, cid) in have or check(self.company, self.d, self.sim.eff, normalize(a)) is not None:
                    continue
                out.append(a)
                if kind == "air_freight":
                    break
        return out
