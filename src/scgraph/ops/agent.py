"""The response agent on one disruption, outside any thread: the `planner` profile of `scgraph.agent` over a
one-disruption world in memory. Kept for the command line (`ops plan`) and its callers; the app runs the planner
as a turn of the disruption's thread (`scgraph.agent.threads`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scgraph.agent.gate import eur
from scgraph.agent.loop import MAX_TURNS
from scgraph.agent.profiles import PLANNER, run_turn
from scgraph.agent.tools import Turn
from scgraph.agent.world import SnapshotWorld
from scgraph.llm import LLMClient
from scgraph.ops.company import Company
from scgraph.ops.scenarios import Disruption

# The planner as submitted: the `planner` profile with the tools shaped for Muse Glimmer (blocked assemblies and
# fill_gaps in simulate_plan, submit by simulation_id, several components per lookup, tolerant input). Earlier
# versions and their result sets are in the git history.
AGENT_VERSION = "v4"
VARIANTS = {"full": PLANNER}  # the variant is recorded with every run


@dataclass
class AgentResult:
    status: str  # done | failed
    plan: list[dict[str, Any]] = field(default_factory=list)  # normalised actions with "reason"
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


def run_agent(company: Company, d: Disruption, client: LLMClient,
              on_step: Callable[[dict[str, Any]], None] | None = None, max_turns: int = MAX_TURNS,
              variant: str = "full") -> AgentResult:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {', '.join(VARIANTS)}")
    world = SnapshotWorld(company, {0: d})
    turn = Turn(world, author=f"agent:{client.default_model}", disruption_id=0)
    loop = run_turn(VARIANTS[variant], turn, client, [], on_step=on_step, max_turns=max_turns)
    result = AgentResult(loop.status, steps=loop.steps, input_tokens=loop.input_tokens,
                         output_tokens=loop.output_tokens, cost_usd=loop.cost_usd, error=loop.error)
    if loop.status == "done":
        submitted = world.submitted[-1].data
        result.plan, result.summary = submitted["actions"], submitted["summary"]
    return result


__all__ = ["AGENT_VERSION", "VARIANTS", "AgentResult", "eur", "run_agent"]
