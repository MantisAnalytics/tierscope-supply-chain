"""Profiles: a prompt template, a tool set and an end condition.

`planner` plans a disruption (events start it); `chat` answers people (any message starts it). The prompt version
is the start of the template's SHA-256, so it changes whenever the prompt does. Each turn's system prompt is the
template plus a generated context section (world, week, approval modes, spending limit, current plan with its
decisions): autonomy is stated, never a different prompt. In a disruption's thread the turn's first user message
starts with the disruption overview from the world's view.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from scgraph.agent.gate import KINDS, eur
from scgraph.agent.loop import MAX_TURNS, LoopResult, run_loop
from scgraph.agent.tools import Tool, Turn
from scgraph.agent.tools.graph import GraphTools
from scgraph.agent.tools.ops import OpsTools
from scgraph.agent.tools.write import WriteTools
from scgraph.llm import LLMClient
from scgraph.ops.simulate import RULES

PROMPTS = Path(__file__).parent / "prompts"
NUDGE = ("Call submit_plan now with the plan you recommend: the simulation_id of the cheapest plan you simulated "
         "submits it exactly as simulated.")


@cache
def template(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


@dataclass(frozen=True)
class Profile:
    name: str
    end: str  # submit | text
    nudge: str | None = None

    @property
    def template(self) -> str:
        return template(self.name)

    @property
    def version(self) -> str:
        return hashlib.sha256(self.template.encode()).hexdigest()[:12]

    def tools(self, turn: Turn) -> tuple[list[Tool], str | None]:
        """The turn's tools and the disruption overview (None outside a disruption)."""
        out: list[Tool] = []
        overview = None
        writes = WriteTools(turn)
        if turn.disruption_id is not None:
            ops = OpsTools(turn)
            overview = ops.overview()
            if not ops.sim.comps and self.end == "submit":
                raise NothingToPlan("the disruption affects no bought-in component of the graph")
            out += [t for t in ops.tools() if self.end == "text" or t.name != "disruption_overview"]
        if turn.world.graph() is not None:
            out += GraphTools(turn).tools()
        if self.end == "submit":
            out.append(writes.submit_plan_tool())
        else:
            if turn.disruption_id is not None:
                out.append(writes.submit_plan_tool())
            out.append(writes.draft_disruption_tool())
            if turn.world.graph() is not None:
                out.append(writes.propose_edit_tool())
        return out, overview

    def system(self, turn: Turn) -> str:
        return self.template.format(**self._values(turn)).rstrip() + "\n\n" + context(turn)

    def _values(self, turn: Turn) -> dict[str, Any]:
        co = turn.world.company()
        values: dict[str, Any] = {"assemblies": len(co.assemblies), "revenue": eur(co.total_revenue),
                                  "margin": co.margin, "r": RULES}
        if turn.disruption_id is not None:
            d = turn.world.view(turn.disruption_id)
            values |= {"horizon": d.horizon_weeks, "stock_cover": d.stock_cover_weeks}
        return values


class NothingToPlan(ValueError):
    pass


PLANNER = Profile("planner", "submit", NUDGE)
CHAT = Profile("chat", "text")
PROFILES = {p.name: p for p in (PLANNER, CHAT)}


def context(turn: Turn) -> str:
    world, gate = turn.world, turn.world.gate
    modes = ", ".join(f"{kind} {gate.mode(kind)}" for kind in KINDS)
    lines = ["# Context", f"World: {world.name}. Current week: {world.now()}.",
             f"Approval modes: {modes}. In human mode a write becomes a proposal that a person decides; in auto "
             "mode it is applied at once.",
             f"Spending limit for auto-applied plans: {eur(gate.spend_limit_eur)} of action costs per disruption."]
    if turn.disruption_id is None:
        lines.append("This is a general thread, not bound to a disruption.")
        return "\n".join(lines)
    plan = turn.plan
    if not plan:
        lines.append("Current plan: none submitted yet.")
        return "\n".join(lines)
    stale = " It is stale: the graph changed since and its cost no longer holds." if plan.get("stale") else ""
    lines.append(f"Current plan: version {plan['version']}.{stale} Summary: {plan.get('summary') or '-'}")
    for a in plan["actions"]:
        decision = a["status"] + (f' with the comment "{a["comment"]}"' if a.get("comment") else "")
        lines.append(f"- [{decision}] {a['text']}" + (f" (reason: {a['reason']})" if a.get("reason") else ""))
    if not plan["actions"]:
        lines.append("- no actions (do nothing)")
    return "\n".join(lines)


def run_turn(profile: Profile, turn: Turn, client: LLMClient, messages: list[dict[str, Any]],
             on_step: Callable[[dict[str, Any]], None] | None = None, max_turns: int = MAX_TURNS) -> LoopResult:
    """One agent turn: the profile's tools and system prompt over the world, then the recorded loop."""
    try:
        tools, overview = profile.tools(turn)
    except NothingToPlan as e:
        return LoopResult(error=str(e))
    messages = list(messages)
    if overview is not None:
        if messages and messages[0]["role"] == "user" and isinstance(messages[0]["content"], str):
            messages[0] = {"role": "user", "content": f"{overview}\n\n{messages[0]['content']}"}
        else:
            messages.insert(0, {"role": "user", "content": overview})
    return run_loop(client, system=profile.system(turn), messages=messages, tools=tools, end=profile.end,
                    nudge=profile.nudge, on_step=on_step, max_turns=max_turns)
