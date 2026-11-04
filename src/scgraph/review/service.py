"""Accept and reject proposals: whole proposals at a time, journaled, with spec updates for mapping columns."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scgraph.graph.facts import Fact
from scgraph.graph.rows import LoadReport, fingerprint, now, source_json, to_json
from scgraph.graph.store import GraphStore
from scgraph.review.gate import Gate, _node_refs, _replace_key, mention_id, same_value


class ReviewError(ValueError):
    pass


class DecidedError(ReviewError):
    """The proposal is not pending any more."""


@dataclass
class Decision:
    proposal_id: int
    action: str  # accept | reject
    journal_id: int
    summary: str
    warnings: list[str] = field(default_factory=list)
    report: LoadReport | None = None  # facts a resolution applied through the gate


def _pending(store: GraphStore, proposal_id: int) -> dict[str, Any]:
    proposal = store.proposal(proposal_id)
    if proposal is None:
        raise ReviewError(f"no proposal #{proposal_id}")
    if proposal["status"] != "pending":
        raise DecidedError(f"proposal #{proposal_id} is already {proposal['status']}"
                           f" (by {proposal['decided_by']} at {proposal['decided_at']})")
    return proposal


def accept(store: GraphStore, proposal_id: int, author: str, *, comment: str | None = None,
           target_id: str | None = None, new_node: str | bool | None = None) -> Decision:
    """Apply a proposal as a whole; its values become human-confirmed. A resolution needs a node to attach to."""
    proposal = _pending(store, proposal_id)
    kind = proposal["kind"]
    if kind == "resolution" and not (target_id or new_node):
        raise ReviewError("a resolution is accepted by choosing a node (target_id) or creating one (new_node)")
    decision = Decision(proposal_id, "accept", 0, "")
    with store.operation(author, "accept") as op:
        if kind == "resolution":
            target = _resolution_target(store, proposal, target_id, new_node)
            decision.report = _accept_resolution(store, proposal, target, op.journal_id)
            what = f"{proposal['group_key'][3]} mention {proposal['group_key'][2]!r} -> {target}"
        else:
            if kind == "restore":
                _remove_markers(store, proposal)
            _apply_rows(store, store.proposal_rows(proposal_id, review="proposed"))
            what = proposal["summary"]
        _decide(store, proposal_id, "accepted", author, comment)
        if kind == "mapping":
            decision.warnings += _spec_decision(store, op, proposal, accept=True, author=author, reason=comment)
        op.summary = f"accept #{proposal_id} ({kind}): {what}" + (f" -- {comment}" if comment else "")
        decision.journal_id, decision.summary = op.journal_id, op.summary
    return decision


def reject(store: GraphStore, proposal_id: int, author: str, *, comment: str | None = None) -> Decision:
    """Reject a proposal as a whole: its facts are stored as rejected and not proposed again from the same source."""
    proposal = _pending(store, proposal_id)
    decision = Decision(proposal_id, "reject", 0, "")
    with store.operation(author, "reject") as op:
        touched = set()
        for fact, meta in store.proposal_rows(proposal_id, review="proposed"):
            values = {"review": "rejected"}
            if meta["table"] == "field_values":
                values.update(status="rejected", active=0)
                touched.add(store.owner_of(fact) + (fact.field,))
            store.update(meta["table"], meta["rowid"], values)
            store.remember_rejection(_fingerprint(store, fact))
        for owner in touched:
            store.select_active(*owner)
        _decide(store, proposal_id, "rejected", author, comment)
        if proposal["kind"] == "mapping":
            decision.warnings += _spec_decision(store, op, proposal, accept=False, author=author, reason=comment)
        op.summary = f"reject #{proposal_id} ({proposal['kind']}): {proposal['summary']}" \
            + (f" -- {comment}" if comment else "")
        decision.journal_id, decision.summary = op.journal_id, op.summary
    return decision


def _fingerprint(store: GraphStore, fact: Fact) -> str:
    value = to_json(fact.value) if fact.kind == "field" else None
    return fingerprint(store.item_of(fact), value, source_json(fact.source))


def _decide(store: GraphStore, proposal_id: int, status: str, author: str, comment: str | None) -> None:
    store.update("proposals", proposal_id, {"status": status, "decided_by": author, "decided_at": now(),
                                            "comment": comment})


def _apply_rows(store: GraphStore, rows: list[tuple[Fact, dict[str, Any]]]) -> None:
    """Accept rows: field values become human-confirmed and replace differing accepted values (superseded)."""
    touched: dict[tuple, list[Any]] = {}
    for fact, meta in rows:
        values: dict[str, Any] = {"review": "accepted"}
        if meta["table"] == "field_values":
            values["locked"] = 1
            touched.setdefault(store.owner_of(fact) + (fact.field,), []).append(fact.value)
        store.update(meta["table"], meta["rowid"], values)
    for owner, accepted in touched.items():
        rel = store.schema.relations.get(owner[1])
        if not (rel and owner[3] in rel.occurrence_fields):
            supersede(store, owner, accepted)
        store.select_active(*owner)


def supersede(store: GraphStore, owner: tuple, keep: list[Any]) -> None:
    """Accepted values of a field that differ from `keep` become superseded, remembered as decided by a person."""
    for other, meta in store.rows_of_field(*owner, review="accepted"):
        if not any(same_value(other.value, v) for v in keep):
            store.update("field_values", meta["rowid"], {"review": "superseded", "status": "superseded", "active": 0})
            store.remember_rejection(_fingerprint(store, other))


def _remove_markers(store: GraphStore, proposal: dict[str, Any]) -> None:
    """A restore proposal's deletion marker; for a node, also the markers its deletion put on its edges."""
    _, item_kind, type_name, key, field_name = proposal["group_key"]
    marker = store.deletion(item_kind, type_name, key, field_name)
    if marker is None:
        return
    store.delete("deletions", marker["rowid"])
    if item_kind == "node" and marker["journal_id"] is not None:
        for other in store.deletions():
            if other["journal_id"] == marker["journal_id"] and other["item_kind"] == "edge":
                store.delete("deletions", other["rowid"])


def _resolution_target(store: GraphStore, proposal: dict[str, Any], target_id: str | None,
                       new_node: str | bool | None) -> str:
    type_name, mention = proposal["group_key"][3], proposal["group_key"][2]
    if target_id:
        if not store.has_node(type_name, target_id):
            raise ReviewError(f"no {type_name} {target_id!r} in the graph")
        return target_id
    node_id = new_node if isinstance(new_node, str) else " ".join(mention.split())
    if store.has_node(type_name, node_id):
        raise ReviewError(f"{type_name} {node_id!r} exists already; choose it as target_id")
    return node_id


def _accept_resolution(store: GraphStore, proposal: dict[str, Any], target: str, journal_id: int) -> LoadReport:
    """Re-key the proposal's facts from the mention to the chosen node and apply them through the gate.

    The mention text becomes an alias of the node, so later mentions with the same text resolve to it.
    """
    type_name, mention = proposal["group_key"][3], proposal["group_key"][2]
    provisional = mention_id(mention)
    facts = []
    for fact, meta in store.proposal_rows(proposal["id"], review="proposed"):
        store.delete(meta["table"], meta["rowid"])
        facts.append(_resolved(store, fact, type_name, mention, provisional, target))
    if store.alias_of(type_name, provisional) is None:
        store.insert("aliases", {"type": type_name, "from_id": provisional, "to_id": target, "journal_id": journal_id})
    report = LoadReport(run_id=f"accept-{proposal['id']}")
    touched = Gate(store, report.run_id, trusted=True, lock=True, check_resolution=True).run(facts, report)
    for owner in touched:
        store.select_active(*owner)
    return report


def _resolved(store: GraphStore, fact: Fact, type_name: str, mention: str, provisional: str, target: str) -> Fact:
    """The fact with the mention's node replaced by `target` and its resolution record marked as a person's."""
    record = fact.resolution or {}
    records = record.get("mentions") or [record]
    chosen = {provisional} | {r.get("chosen") for r in records if r.get("mention") == mention}
    parts = list(fact.key)
    for index, ref_type in _node_refs(store, fact):
        if ref_type == type_name and parts[index] in chosen:
            parts[index] = target
    human = {"method": "human", "mention": mention, "chosen": target, "confidence": 1.0}
    if "mentions" in record:
        records = [human if r.get("mention") == mention else r for r in records]
        weakest = min(records, key=lambda r: (r.get("method") in ("id", "human"), r.get("confidence", 0.0)))
        resolution = {"method": weakest.get("method"), "confidence": weakest.get("confidence"), "mentions": records}
    else:
        resolution = human
    resolved = _replace_key(fact, tuple(parts))
    return Fact(resolved.kind, resolved.type, resolved.key, resolved.source, field=resolved.field,
                value=resolved.value, method=resolved.method, confidence=resolved.confidence, run_id=resolved.run_id,
                resolution=resolution, origin=resolved.origin)


def _spec_decision(store: GraphStore, op: Any, proposal: dict[str, Any], *, accept: bool, author: str,
                   reason: str | None) -> list[str]:
    """Approve or turn off the proposal's column in its stored spec; warns when there is no spec file."""
    from scgraph.ingest.spec_review import decide_mapping

    by, where, region, column = proposal["group_key"]
    if by != "spec":
        return []
    path = Path(where)
    if not path.is_file():
        return [f"spec {where} not found; the decision applies to the graph only"]
    op.files.setdefault(str(path), path.read_text())
    try:
        decide_mapping(path, region, column, accept=accept, author=author, reason=reason, threshold=store.threshold)
    except ValueError as e:
        return [f"spec {path.name} not updated: {e}"]
    return []


def describe(store: GraphStore, proposal_id: int, *, samples: int = 5) -> dict[str, Any]:
    """A proposal with its facts (or `samples` of them), the values it would replace, and spec context."""
    proposal = store.proposal(proposal_id)
    if proposal is None:
        raise ReviewError(f"no proposal #{proposal_id}")
    rows = store.proposal_rows(proposal_id)
    facts = [_fact_view(store, fact, meta) for fact, meta in rows]
    out = {**proposal, "count": len(facts), "facts": facts if len(facts) <= samples else facts[:samples],
           "confidence": [min((f["confidence"] for f in facts), default=None),
                          max((f["confidence"] for f in facts), default=None)],
           "targets": sorted({f["target"] for f in facts})}
    if proposal["kind"] == "conflict":
        out["changes"] = [{"item": f["item"], "field": f["field"], "current": f["current"], "proposed": f["value"],
                           "source": f["source"], "quote": f["quote"]} for f in facts]
    if proposal["kind"] == "mapping" and proposal["group_key"][0] == "spec":
        out["spec"] = _spec_context(proposal["group_key"])
    if proposal["kind"] == "resolution":
        out["mention"] = proposal["group_key"][2]
        out["candidates"] = sorted({c for f in facts for c in _candidates(f["resolution"])})
    if proposal["kind"] == "restore":
        _, item_kind, type_name, key, field_name = proposal["group_key"]
        out["deletion"] = store.deletion(item_kind, type_name, key, field_name)
    return out


def _candidates(resolution: dict | None) -> list[str]:
    if not resolution:
        return []
    return [c for r in resolution.get("mentions") or [resolution] for c in r.get("candidates", [])]


def _fact_view(store: GraphStore, fact: Fact, meta: dict[str, Any]) -> dict[str, Any]:
    item = store.item_of(fact)
    view = {"kind": fact.kind, "type": fact.type, "key": list(fact.key), "item": _item_label(item),
            "field": fact.field, "value": fact.value, "confidence": fact.confidence, "method": fact.method,
            "source": fact.source.label(), "location": fact.source.to_dict(), "quote": fact.source.quote,
            "review": meta["review"], "resolution": fact.resolution,
            "target": f"{fact.type}.{fact.field}" if fact.kind == "field" else fact.type, "current": None}
    if fact.kind == "field":
        accepted = store.rows_of_field(*store.owner_of(fact), fact.field, review="accepted")
        active = [f.value for f, _ in accepted]
        view["current"] = active[0] if len(set(map(to_json, active))) == 1 else active or None
    return view


def _item_label(item: tuple) -> str:
    if item[0] == "node":
        return f"{item[1]} {item[2]}"
    if item[0] == "edge":
        return f"{item[1]} " + " -> ".join(str(p) for p in json.loads(item[2]) if p)
    owner = item[3] if item[1] == "node" else " -> ".join(str(p) for p in json.loads(item[3]) if p)
    return f"{item[2]} {owner}"


def _spec_context(group_key: list) -> dict[str, Any]:
    _, where, region_id, column = group_key
    path = Path(where)
    out: dict[str, Any] = {"path": where, "region": region_id, "column": column}
    if not path.is_file():
        out["missing"] = True
        return out
    spec = json.loads(path.read_text())
    region = next((r for r in spec.get("regions", []) if r.get("id") == region_id), {})
    if column is None:
        out.update(confidence=region.get("confidence"), keys=list(region.get("key_columns", {})))
    else:
        c = region.get("columns", {}).get(column, {})
        out.update(target=c.get("target"), confidence=c.get("confidence"), rationale=c.get("rationale"),
                   role=c.get("role"))
    return out
