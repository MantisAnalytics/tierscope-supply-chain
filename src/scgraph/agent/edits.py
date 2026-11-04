"""Graph edits the agent proposes in a thread: checked against the schema and graph without applying, with the
current values they would change and a preview of their effect on exposure. A person applies them through graph
editing; a value that changed in the meantime makes the block stale instead.
"""

from __future__ import annotations

import threading
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from scgraph.editing.edits import Edit, EditError, parse_edit, validate
from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.rows import to_json
from scgraph.graph.store import GraphStore

if TYPE_CHECKING:
    from scgraph.agent.store import AgentStore

PREVIEW_EFFECTS = 20


class EditBlockError(ValueError):
    pass


def check(store: GraphStore, raw: list[dict[str, Any]]) -> list[Edit]:
    """Parse and validate edits without applying them; raises EditError naming the edit."""
    if not raw:
        raise EditError("no edits")
    out = []
    for i, data in enumerate(raw):
        try:
            edit = parse_edit(data)
            validate(store, edit)
        except EditError as e:
            raise EditError(f"edit {i + 1}: {e}") from None
        out.append(edit)
    return out


def current(store: GraphStore, edit: Edit) -> dict[str, Any]:
    """What the edit would change, as it is now: a field's active value, or whether the item exists."""
    if edit.op == "merge":
        return {"exists": store.has_node(edit.type, edit.source or "")}
    if edit.field is not None and edit.op in ("set_field", "delete"):
        if edit.type in store.schema.entities:
            owner = ("node", edit.type, edit.node_id)
        else:
            owner = ("edge", edit.type, to_json(list(edit.key)))
        return {"value": store.active_fields().get(owner, {}).get(edit.field)}
    if edit.type in store.schema.entities:
        return {"exists": store.has_node(edit.type, edit.node_id)}
    return {"exists": store.has_edge(edit.type, edit.key)}


def facts_of(edit: Edit) -> list[Fact]:
    """The facts an edit would write; deletes and merges write none (no preview)."""
    source = SourceLocation(file="agent proposal")
    common = {"method": "human", "confidence": 1.0, "run_id": "preview"}
    facts = []
    if edit.op == "create_node":
        facts.append(Fact("entity", edit.type, edit.key, source, **common))
    elif edit.op == "add_edge":
        facts.append(Fact("relation", edit.type, edit.key, source, **common))
    elif edit.op != "set_field":
        return []
    fields = dict(edit.fields) | ({edit.field: edit.value} if edit.op == "set_field" else {})
    facts += [Fact("field", edit.type, edit.key, source, field=k, value=v, **common) for k, v in fields.items()]
    return facts


def preview(store: GraphStore, edits: list[Edit]) -> dict[str, Any]:
    from scgraph.review.preview import effects

    facts = [f for e in edits for f in facts_of(e)]
    found = effects(store, facts) if facts else []
    return {"no_preview": [e.describe() for e in edits if not facts_of(e)], "changed": len(found),
            "max_impact_eur": max((abs(e["impact_eur"]) for e in found), default=0.0),
            "max_exposure_pp": max((abs(e["exposure_pp"]) for e in found), default=0.0),
            "effects": found[:PREVIEW_EFFECTS]}


def proposal(store: GraphStore, raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything an edit block stores: the edits, their current values and the preview."""
    edits = check(store, raw)
    return {"edits": raw, "before": [current(store, e) for e in edits], "preview": preview(store, edits),
            "described": [e.describe() for e in edits]}


def apply_block(store: GraphStore, agent: AgentStore, block_id: int, author: str,
                lock: threading.Lock | None = None) -> dict[str, Any]:
    """Apply a proposed edit block as `author`, journaled with the thread as origin. Refused, and marked stale,
    when a value it changes is no longer what it was when proposed."""
    from scgraph.editing.service import apply_edits

    block = agent.edit_block(block_id)
    if block is None:
        raise EditBlockError(f"no edit block {block_id}")
    if block["status"] != "proposed":
        raise EditBlockError(f"edit block {block_id} is already {block['status']}")
    with lock or nullcontext():
        edits = [parse_edit(e) for e in block["edits"]]
        changed = [e.describe() for e, before in zip(edits, block["before"], strict=True) if current(store, e) != before]
        if changed:
            agent.decide_edit_block(block_id, "stale", author)
            raise EditBlockError(f"the graph changed since the agent proposed this ({'; '.join(changed)}); "
                                 "ask the agent to propose it again")
        result = apply_edits(store, block["edits"], author, origin=f"thread:{block['thread_id']}")
    agent.decide_edit_block(block_id, "applied", author, journal_id=result.journal_id)
    return {"journal_id": result.journal_id, "summary": result.summary}
