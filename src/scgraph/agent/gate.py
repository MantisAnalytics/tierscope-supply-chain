"""The approval gate: every write the agent makes passes it, in `human` or `auto` mode per write kind.

`human` stores the write as a proposal and returns a block for a person to decide; `auto` stores it, applies it at
once with the agent as author and returns a block saying so. The gate also refuses to auto-apply a plan whose
action costs exceed the spending limit per disruption; in `human` mode the limit is only shown.

Storing and applying belong to the world (its `Sink`), so the same gate works over the app's stores and over a
simulated world.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

KINDS = ("plan", "edit", "draft", "task")
MODES = ("human", "auto")
DEFAULT_SPEND_LIMIT_EUR = 250_000.0


class GateError(ValueError):
    pass


@dataclass
class Write:
    kind: str  # plan | edit | draft | task
    data: dict[str, Any]
    author: str  # agent:<model>
    thread_id: int | None = None
    disruption_id: int | None = None
    run_id: int | None = None


@dataclass
class GateResult:
    status: str  # proposed | applied | refused
    message: str  # what the model is told
    block: dict[str, Any] | None = None  # {type, ref} posted in the thread
    stored: dict[str, Any] = field(default_factory=dict)


class Sink(Protocol):
    def store(self, write: Write) -> dict[str, Any]:
        """Store the write as a proposal; returns at least {"block": {type, ref}, "message": str}."""

    def apply(self, write: Write, stored: dict[str, Any]) -> None:
        """Apply a stored write with `write.author` as author."""


class Gate:
    def __init__(self, modes: Mapping[str, str] | None = None,
                 spend_limit_eur: float = DEFAULT_SPEND_LIMIT_EUR) -> None:
        self.modes = {kind: "human" for kind in KINDS} | dict(modes or {})
        for kind, mode in self.modes.items():
            if kind not in KINDS or mode not in MODES:
                raise GateError(f"gate mode {kind}={mode!r}: kinds are {', '.join(KINDS)}, modes {', '.join(MODES)}")
        self.spend_limit_eur = spend_limit_eur

    def mode(self, kind: str) -> str:
        return self.modes[kind]

    def submit(self, write: Write, sink: Sink) -> GateResult:
        if write.kind not in KINDS:
            raise GateError(f"unknown write kind {write.kind!r}")
        auto = self.mode(write.kind) == "auto"
        if write.kind == "plan" and auto:
            cost = float(write.data.get("action_cost") or 0.0)
            if cost > self.spend_limit_eur:
                return GateResult("refused", f"Plan not applied: its action costs of {eur(cost)} exceed the spending "
                                             f"limit of {eur(self.spend_limit_eur)} per disruption. Submit a cheaper "
                                             "plan.")
        stored = sink.store(write)
        block = dict(stored["block"])
        if not auto:
            return GateResult("proposed", stored.get("message") or "Submitted for a person to decide.", block, stored)
        sink.apply(write, stored)
        block["applied"] = True
        return GateResult("applied", (stored.get("message") or "Stored.") + " Applied at once (auto mode).", block,
                          stored)


def eur(value: float) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e6:
        return f"{sign}EUR {v / 1e6:,.2f}M"
    if v >= 1e3:
        return f"{sign}EUR {v / 1e3:,.0f}k"
    return f"{sign}EUR {v:,.0f}"
