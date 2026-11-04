"""The world the agent works in: the only way it reads and changes anything.

A world gives the current week, the company snapshot with its graph version, the agent's view of a disruption,
simulation of a plan on that view, read access to the graph for graph tools, and submission of writes through the
approval gate. `LiveWorld` is the app over its graph and operations stores; `SnapshotWorld` holds a fixed company
and disruptions in memory (the `ops plan` wrapper and tests).

Rule for every world: `simulate` runs on the view, never on hidden truth, or the agent sees the future through
the simulator. A world may withhold or replace fields of the view (an estimated duration range instead of the
true duration); tools describe a disruption only from the view.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Protocol

from scgraph.agent.gate import Gate, GateError, GateResult, Write, eur
from scgraph.ops.company import Company
from scgraph.ops.scenarios import Disruption
from scgraph.ops.simulate import Outcome, Simulator

if TYPE_CHECKING:
    from scgraph.graph.store import GraphStore
    from scgraph.ops.service import OpsService


@dataclass
class View(Disruption):
    """What the agent may know about a disruption. `duration_range` (low, high weeks) replaces the duration in
    everything shown to the agent when the true duration is not known yet; `duration_weeks` then holds the
    world's working estimate for simulation."""

    duration_range: tuple[int, int] | None = None

    @classmethod
    def of(cls, d: Disruption, **changes: Any) -> View:
        data = {k: v for k, v in asdict(d).items() if k in cls.__dataclass_fields__}
        return cls(**(data | changes))

    def duration_text(self) -> str:
        if self.duration_range:
            low, high = self.duration_range
            return f"an estimated {low} to {high} weeks (not known exactly yet)"
        return f"{self.duration_weeks} weeks"


class World(Protocol):
    name: str
    gate: Gate

    def now(self) -> int: ...
    def company(self) -> Company: ...
    def graph_version(self) -> int: ...
    def graph(self) -> GraphStore | None: ...
    def reading(self) -> AbstractContextManager: ...
    def view(self, did: int) -> View: ...
    def simulator(self, did: int) -> Simulator: ...
    def simulate(self, did: int, actions: list[dict[str, Any]]) -> Outcome: ...
    def submit(self, write: Write) -> GateResult: ...


class BaseWorld:
    """Defaults: week 0, no graph, simulation on the view."""

    name = "world"
    gate: Gate

    def now(self) -> int:
        return 0

    def graph_version(self) -> int:
        return 0

    def graph(self) -> GraphStore | None:
        return None

    def reading(self) -> AbstractContextManager:
        """Held while graph tools read the graph."""
        return nullcontext()

    def simulator(self, did: int) -> Simulator:
        return Simulator(self.company(), self.view(did))

    def simulate(self, did: int, actions: list[dict[str, Any]]) -> Outcome:
        return self.simulator(did).run(actions)


class SnapshotWorld(BaseWorld):
    """A fixed company and disruptions in memory; no graph or databases. Writes pass the gate and are kept in
    `submitted` (and `applied` in auto mode)."""

    name = "snapshot"

    def __init__(self, company: Company, disruptions: dict[int, Disruption], gate: Gate | None = None,
                 week: int = 0, version: int = 0) -> None:
        self._company, self.disruptions = company, disruptions
        self.gate = gate or Gate()
        self.week, self.version = week, version
        self.submitted: list[Write] = []
        self.applied: list[Write] = []

    def now(self) -> int:
        return self.week

    def company(self) -> Company:
        return self._company

    def graph_version(self) -> int:
        return self.version

    def view(self, did: int) -> View:
        d = self.disruptions[did]
        return d if isinstance(d, View) else View.of(d)

    def submit(self, write: Write) -> GateResult:
        return self.gate.submit(write, self)

    def store(self, write: Write) -> dict[str, Any]:
        self.submitted.append(write)
        return {"block": {"type": write.kind, "ref": len(self.submitted)}, "message": f"{write.kind} submitted."}

    def apply(self, write: Write, stored: dict[str, Any]) -> None:
        self.applied.append(write)


class LiveWorld(BaseWorld):
    """The app: the graph store under its lock, the operations service's snapshot and stores. Week 0 (the app has
    no clock yet); the view is the stored disruption."""

    name = "live"

    def __init__(self, ops: OpsService, gate: Gate | None = None) -> None:
        self.ops = ops
        self.gate = gate or Gate()

    def company(self) -> Company:
        return self.ops.snapshot()

    def graph_version(self) -> int:
        with self.ops.graph_lock:
            return self.ops.graph.version()

    def graph(self) -> GraphStore:
        return self.ops.graph

    def reading(self) -> AbstractContextManager:
        return self.ops.graph_lock

    def view(self, did: int) -> View:
        return View.of(self.ops.disruption(did))

    def submit(self, write: Write) -> GateResult:
        return self.gate.submit(write, _LiveSink(self))


class _LiveSink:
    def __init__(self, world: LiveWorld) -> None:
        self.world, self.ops = world, world.ops

    def store(self, write: Write) -> dict[str, Any]:
        from scgraph.agent.plans import store_plan

        store = self.ops.store
        if write.kind == "plan":
            did = write.disruption_id
            co, d = self.world.company(), self.world.view(did)
            plan = store_plan(store, co, d, did, run_id=write.run_id, graph_version=self.world.graph_version(),
                              actions=write.data["actions"], summary=write.data.get("summary", ""),
                              refs=self.ops.references(co, did))
            new = sum(not a.get("kept") for a in plan["actions"])
            kept = len(plan["actions"]) - new
            cost = (plan["outcome"] or {}).get("action_cost", 0.0)
            over = (f" Its action costs of {eur(cost)} are over the spending limit of "
                    f"{eur(self.world.gate.spend_limit_eur)}; the approver sees that." if
                    cost > self.world.gate.spend_limit_eur else "")
            message = (f"Plan version {plan['version']} stored: {new} action(s) proposed for approval"
                       + (f", {kept} accepted action(s) kept" if kept else "")
                       + f"; total cost {eur(plan['outcome']['total_cost'])}.{over}")
            return {"block": {"type": "plan", "ref": plan["id"]}, "plan": plan, "message": message}
        if write.kind == "edit":
            bid = store.agent.add_edit_block(write.thread_id, write.data["edits"], write.data["before"],
                                             write.data.get("preview"), write.data.get("reason", ""))
            return {"block": {"type": "edit", "ref": bid}, "edit_block": bid,
                    "message": f"Edit block {bid} shown to the person; the graph changes only when they apply it."}
        if write.kind == "draft":
            did = store.add_disruption(write.data["disruption"], write.author, status="draft", thread=False)
            return {"block": {"type": "draft", "ref": did}, "disruption_id": did,
                    "message": f"Draft disruption {did} shown to the person to save or discard."}
        raise GateError(f"{write.kind} writes are not supported in the live world yet")

    def apply(self, write: Write, stored: dict[str, Any]) -> None:
        if write.kind == "plan":
            co = self.world.company()
            for a in self.ops.store.actions(write.disruption_id, ("proposed",)):
                if a["plan_id"] == stored["plan"]["id"]:
                    self.ops.decide(co, a["id"], True, write.author)
        elif write.kind == "edit":
            from scgraph.agent.edits import apply_block

            apply_block(self.ops.graph, self.ops.store.agent, stored["edit_block"], write.author, self.ops.graph_lock)
        elif write.kind == "draft":
            did = stored["disruption_id"]
            self.ops.store.update_disruption(did, status="active")
            self.ops.store.agent.bind_disruption(did, write.thread_id, write.data["disruption"].get("name", ""))
