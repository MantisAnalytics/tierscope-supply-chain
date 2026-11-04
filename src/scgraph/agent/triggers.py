"""Triggers: events that start the agent without a message.

`disruption.created` and `disruption.updated` (with the changed fields) are recorded with their source, shown in
the disruption's thread as an event block and, when `SCGRAPH_AGENT_TRIGGERS` is on, queue a planner turn there.
Event-started turns bill to ledger runs `auto-*`, capped together by `SCGRAPH_BUDGET_AUTO_USD` (checked by the
thread service before each such turn).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scgraph.agent.threads import ThreadService

EVENT_TYPES = ("disruption.created", "disruption.updated")


def emit(threads: ThreadService, type_: str, did: int, source: str, **payload: Any) -> int | None:
    """Record the event in the disruption's thread and deliver it. A draft the agent wrote in a general thread
    has no thread until it is saved; its events are not recorded before that."""
    assert type_ in EVENT_TYPES, type_
    thread = threads.agent.thread_for(did)
    if thread is None:
        if threads.store.disruption(did)["status"] == "draft":
            return None
        thread = threads.agent.thread(threads.thread_of(did))
    eid = threads.agent.add_event(type_, {"disruption_id": did, **payload}, source, thread["id"])
    threads.deliver_event(eid, thread["id"])
    return eid


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Fields of a disruption that differ; parameters by name (`params.suppliers`)."""
    out = []
    for key in sorted(set(before) | set(after)):
        if key == "params":
            b, a = before.get(key) or {}, after.get(key) or {}
            out += [f"params.{k}" for k in sorted(set(b) | set(a)) if b.get(k) != a.get(k)]
        elif before.get(key) != after.get(key):
            out.append(key)
    return out
