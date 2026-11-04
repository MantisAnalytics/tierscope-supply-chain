"""The agent's tool set, shared by every profile: graph questions, disruption assessment and simulation, and the
writes that go through the approval gate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scgraph.agent.world import World


@dataclass
class Turn:
    """What a turn's tools work on: the world, the thread and disruption it serves, and who writes."""

    world: World
    author: str  # agent:<model>
    disruption_id: int | None = None
    thread_id: int | None = None
    run_id: int | None = None
    # The disruption's current plan version with each action's decision, for the context section:
    # {"version", "summary", "stale", "actions": [{"text", "status", "reason", "comment"}]}
    plan: dict[str, Any] | None = None
    # This turn's simulations, so a plan can be submitted by its simulation id: [{"id", "actions", "total_cost"}]
    simulations: list[dict[str, Any]] = field(default_factory=list)
    best_plan_warned: bool = False  # submit_plan has pointed to a cheaper simulation once


@dataclass
class ToolResult:
    value: Any
    ends: bool = False  # the planner's end condition: an accepted submission
    block: dict[str, Any] | None = None  # posted in the thread with the agent's answer


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], Any] = field(repr=False)

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}
