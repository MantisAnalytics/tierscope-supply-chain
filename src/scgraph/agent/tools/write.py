"""The agent's writes: a plan, graph edits and a draft disruption. Each goes to the world as a `Write` and passes
the approval gate there; the tool tells the model what the gate did."""

from __future__ import annotations

from typing import Any

from scgraph.agent.gate import Write, eur
from scgraph.agent.tools import Tool, ToolResult, Turn, obj
from scgraph.agent.tools.ops import ACTION_SCHEMA, actions_arg, text_after_list
from scgraph.ops.news import TOOL as DRAFT_TOOL
from scgraph.ops.news import to_draft
from scgraph.ops.simulate import ActionError, action_key, check, normalize

SUBMIT_SCHEMA = obj({
    "actions": {"type": "array", "items": {**ACTION_SCHEMA, "properties": {
        **ACTION_SCHEMA["properties"], "reason": {"type": "string", "description": "one sentence"}},
        "required": ["type", "reason"]}},
    "simulation_id": {"type": "integer", "description": "submit the actions of this simulation exactly as simulated "
                                                        "(then `actions` may be left out)"},
    "summary": {"type": "string", "description": "two sentences for the approving manager; for an empty plan, why "
                                                 "no action lowers the total cost"}}, ["summary"])
# A submitted plan this much dearer than the cheapest simulated one is sent back once (EUR, and share of its cost).
BEST_PLAN_MARGIN_EUR, BEST_PLAN_MARGIN = 50_000, 0.05
EDIT_SCHEMA = obj({
    "edits": {"type": "array", "items": {"type": "object"}, "description": "edits in the graph edit format"},
    "reason": {"type": "string", "description": "what the person said that the edits record"}}, ["edits", "reason"])


class WriteTools:
    def __init__(self, turn: Turn) -> None:
        self.turn = turn
        self.world = turn.world

    def _write(self, kind: str, data: dict[str, Any]) -> Write:
        t = self.turn
        return Write(kind, data, t.author, thread_id=t.thread_id, disruption_id=t.disruption_id, run_id=t.run_id)

    def submit_plan_tool(self) -> Tool:
        return Tool("submit_plan", "Submit the plan you recommend for approval, as `actions` or as the simulation_id of "
                    "a plan you simulated; an empty plan with the reason in the summary when no action lowers the "
                    "total cost. Every action must pass simulate_plan's rules; refused plans come back with the "
                    "reasons.", SUBMIT_SCHEMA, self.submit_plan)

    def propose_edit_tool(self) -> Tool:
        return Tool("propose_edit", "Propose corrections to the graph data as a block the person applies or "
                    "dismisses; nothing changes before they apply it. Edit format: "
                    '{"op": "set_field", "type": "Site", "key": ["P01"], "field": "recovery_time_months", '
                    '"value": 9}; also create_node {"op", "type", "id", "fields"}, add_edge {"op", "type", "key": '
                    '[from, to, identity...], "fields"}, delete {"op", "type", "key", "field"?, "reason"}.',
                    EDIT_SCHEMA, self.propose_edit)

    def draft_disruption_tool(self) -> Tool:
        return Tool("draft_disruption", "Turn an event the person describes into a draft disruption for them to "
                    "save or discard. " + DRAFT_TOOL["description"], DRAFT_TOOL["input_schema"],
                    self.draft_disruption)

    def submit_plan(self, args: dict[str, Any]) -> ToolResult:
        did = self.turn.disruption_id
        if did is None:
            raise ValueError("this thread has no disruption to plan")
        items = self._submitted(args)
        sim = self.world.simulator(did)
        plan, refused, seen = [], [], set()
        for item in items:
            try:
                action = normalize(item)
            except ActionError as e:
                refused.append({"action": item, "reason": str(e)})
                continue
            reason = check(sim.company, sim.d, sim.eff, action)
            if reason is None and action_key(action) in seen:
                reason = f"duplicate action {action_key(action)}"
            if reason:
                refused.append({"action": item, "reason": reason})
                continue
            seen.add(action_key(action))
            plan.append({**action, "reason": str(item.get("reason", "")).strip()})
        summary = str(args.get("summary", "")).strip() or text_after_list(args.get("actions"))
        if refused:
            return ToolResult({"accepted": False, "refused": refused,
                               "message": "Fix or drop the refused actions and submit again."})
        if not plan and not summary:
            return ToolResult({"accepted": False, "message": "An empty plan needs its reason in the summary."})
        outcome = sim.run([{k: v for k, v in a.items() if k != "reason"} for a in plan])
        if (warning := self._cheaper_simulation(outcome.total_cost)) is not None:
            return ToolResult({"accepted": False, "message": warning})
        result = self.world.submit(self._write("plan", {"actions": plan, "summary": summary,
                                                        "action_cost": outcome.action_cost,
                                                        "total_cost": outcome.total_cost}))
        if result.status == "refused":
            raise ValueError(result.message)
        return ToolResult({"accepted": True, "status": result.status, "message": result.message,
                           "total_cost": eur(outcome.total_cost)}, ends=True, block=result.block)

    def _submitted(self, args: dict[str, Any]) -> list[Any]:
        """The submitted actions: `actions`, or the actions of simulation `simulation_id` (with any `actions` given
        too, for their reasons)."""
        sid = args.get("simulation_id")
        if sid is None:
            if "actions" not in args:
                raise ValueError("submit_plan needs `actions` (an empty list if you recommend doing nothing) or a "
                                 "`simulation_id`, and `summary`")
            return actions_arg(args)
        found = next((s for s in self.turn.simulations if str(s["id"]) == str(sid).strip()), None)
        if found is None:
            raise ValueError(f"no simulation {sid!r} in this turn; simulate the plan first")
        reasons = {(a.get("type"), a.get("component")): a.get("reason", "") for a in actions_arg(args)
                   if isinstance(a, dict)} if "actions" in args else {}
        return [{**a, "reason": a.get("reason") or reasons.get((a.get("type"), a.get("component"))) or
                 f"as simulated (simulation {found['id']})"} for a in found["actions"]]

    def _cheaper_simulation(self, cost: float) -> str | None:
        """Once per turn: a message pointing to a simulated plan clearly cheaper than the one submitted."""
        t = self.turn
        done = [s for s in t.simulations if not s["refused"]]
        if t.best_plan_warned or not done:
            return None
        best = min(done, key=lambda s: s["total_cost"])
        if cost - best["total_cost"] <= max(BEST_PLAN_MARGIN_EUR, BEST_PLAN_MARGIN * cost):
            return None
        t.best_plan_warned = True
        return (f"Not submitted: simulation {best['id']} cost {eur(best['total_cost'])}, {eur(cost - best['total_cost'])} "
                f"less than this plan ({eur(cost)}). Submit it with simulation_id {best['id']}, or submit this plan "
                "again if you prefer it and say why in the summary.")

    def propose_edit(self, args: dict[str, Any]) -> ToolResult:
        from scgraph.agent import edits

        store = self.world.graph()
        if store is None:
            raise ValueError("this world has no graph to edit")
        raw = args.get("edits")
        if not isinstance(raw, list):
            raise ValueError("edits must be a list of edits")
        with self.world.reading():
            proposal = edits.proposal(store, raw)
        result = self.world.submit(self._write("edit", {"edits": raw, "before": proposal["before"],
                                                        "preview": proposal["preview"],
                                                        "reason": str(args.get("reason", ""))}))
        p = proposal["preview"]
        return ToolResult({"status": result.status, "message": result.message, "edits": proposal["described"],
                           "current_values": proposal["before"],
                           "preview": {"entities_changed": p["changed"], "max_exposure_change_pp":
                                       round(p["max_exposure_pp"], 2), "max_impact_change": eur(p["max_impact_eur"]),
                                       "no_preview": p["no_preview"]}}, block=result.block)

    def draft_disruption(self, args: dict[str, Any]) -> ToolResult:
        draft = to_draft(self.world.company(), args)
        draft.disruption.source = "chat"
        data = {**draft.disruption.to_dict(), "warnings": draft.warnings}
        result = self.world.submit(self._write("draft", {"disruption": data, "warnings": draft.warnings}))
        return ToolResult({"status": result.status, "message": result.message, "draft": draft.disruption.to_dict(),
                           "warnings": draft.warnings}, block=result.block)
