"""Plan versions: each submitted plan is the next version of its disruption's plan, tied to the graph version.

A new version keeps the accepted actions (they are commitments with tasks), proposes only actions not yet
accepted, and supersedes the still-undecided proposals of earlier versions. Staleness is computed on read: a plan
made on another graph version is stale when re-simulating it on the current graph moves its total cost by more
than 1% or refuses one of its actions, so unrelated edits do not flag every plan.
"""

from __future__ import annotations

from typing import Any

from scgraph.ops.company import Company
from scgraph.ops.scenarios import Disruption
from scgraph.ops.simulate import Simulator, action_key, skill
from scgraph.ops.store import OpsStore

STALE_COST_SHARE = 0.01


def bare(action: dict[str, Any]) -> dict[str, Any]:
    """An action without the plan's annotations (reason, kept)."""
    return {k: v for k, v in action.items() if k not in ("reason", "kept")}


def merge_accepted(store: OpsStore, did: int, actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                                                        list[dict[str, Any]]]:
    """The version's actions and the new ones to propose. An action whose key is already accepted takes the accepted
    action's place (marked `kept`); accepted actions the submission leaves out are kept too."""
    accepted = {action_key(a["action"]): a for a in store.actions(did, ("accepted",))}
    plan, new, seen = [], [], set()
    for action in actions:
        key = action_key(action)
        seen.add(key)
        if key in accepted:
            plan.append({**accepted[key]["action"], "reason": accepted[key]["reason"] or action.get("reason", ""),
                         "kept": True})
        else:
            plan.append(action)
            new.append(action)
    plan += [{**a["action"], "reason": a["reason"], "kept": True} for key, a in accepted.items() if key not in seen]
    return plan, new


def store_plan(store: OpsStore, company: Company, d: Disruption, did: int, *, run_id: int | None,
               graph_version: int | None, actions: list[dict[str, Any]], summary: str,
               refs: dict[str, Any] | None) -> dict[str, Any]:
    """Store the next plan version and propose its new actions; returns the plan."""
    plan, new = merge_accepted(store, did, actions)
    outcome = Simulator(company, d).run([bare(a) for a in plan])
    score = None
    if refs is not None:
        score = skill(refs["do_nothing"]["outcome"]["total_cost"], refs["optimiser"]["outcome"]["total_cost"],
                      outcome.total_cost)
    stored = store.agent.add_plan(did, run_id, graph_version, plan, summary, outcome.to_dict(), score)
    store.propose(did, run_id, [(bare(a), a.get("reason", "")) for a in new], plan_id=stored["id"])
    return stored


def staleness(plan: dict[str, Any], company: Company, d: Disruption, graph_version: int) -> dict[str, Any] | None:
    """None when the plan is current; else why it is stale (cost moved or actions refused on the current graph)."""
    if plan["graph_version"] == graph_version:
        return None
    o = Simulator(company, d).run([bare(a) for a in plan["actions"]])
    before = (plan["outcome"] or {}).get("total_cost")
    moved = before is not None and abs(o.total_cost - before) > STALE_COST_SHARE * max(abs(before), 1.0)
    if not moved and not o.refused:
        return None
    return {"total_cost_before": before, "total_cost_now": round(o.total_cost, 2), "refused": o.refused}
