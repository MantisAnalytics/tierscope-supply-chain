"""Apply human edits: each edit compiles to facts with method `human` (or to deletion markers and aliases).

A batch is one transaction and one journal entry; any invalid edit leaves the graph unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scgraph.editing.edits import Edit, EditBatchError, EditError, parse_edit, pending_mention, validate
from scgraph.editing.evidence import EvidenceStore
from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.rows import LoadReport, now, source_json, to_json
from scgraph.graph.store import GraphStore
from scgraph.review.gate import Gate
from scgraph.review.service import _accept_resolution, _decide, supersede


@dataclass
class EditResult:
    journal_id: int
    summary: str
    edits: list[str] = field(default_factory=list)
    report: LoadReport | None = None  # what merges applied and proposed


def apply_edits(store: GraphStore, edits: list[dict[str, Any] | Edit], author: str, *,
                evidence: dict[str, Any] | None = None, evidence_store: EvidenceStore | None = None,
                origin: str | None = None) -> EditResult:
    """Validate and apply edits in order, all or none. `evidence` is the default evidence of every edit; `origin`
    is journaled with the entry (`thread:<id>` for edits applied from an agent thread)."""
    if not edits:
        raise EditBatchError([(0, "no edits")])
    parsed: list[Edit | None] = []
    errors: list[tuple[int, str]] = []
    for i, data in enumerate(edits):
        try:
            edit = data if isinstance(data, Edit) else parse_edit(data)
            edit.evidence = {**(evidence or {}), **edit.evidence}
            parsed.append(edit)
        except EditError as e:
            parsed.append(None)
            errors.append((i, str(e)))
    report = LoadReport(run_id="")
    with store.operation(author, "edit", origin=origin) as op:
        report.run_id = f"edit-{op.journal_id}"
        for i, edit in enumerate(parsed):
            if edit is None:
                continue
            try:
                validate(store, edit, evidence_store)
            except EditError as e:
                errors.append((i, str(e)))
                continue
            # Valid edits apply even after an invalid one, so later edits validate against them; all roll back.
            _apply(store, edit, author, op.journal_id, evidence_store, report)
        if errors:
            raise EditBatchError(sorted(errors))
        described = [e.describe() for e in parsed]
        op.summary = "edit: " + "; ".join(described[:3]) + (f"; +{len(described) - 3} more" if len(described) > 3
                                                              else "")
        return EditResult(op.journal_id, op.summary, described, report)


def _source(edit: Edit, author: str, evidence_store: EvidenceStore | None) -> SourceLocation:
    ev = edit.evidence
    name = evidence_store.name(ev["id"]) if ev.get("id") and evidence_store else None
    return SourceLocation(file=name or "manual", page=ev.get("page"), quote=ev.get("quote"), url=ev.get("url"),
                          note=ev.get("comment"), author=author, evidence_id=ev.get("id"))


def _apply(store: GraphStore, edit: Edit, author: str, journal_id: int, evidence_store: EvidenceStore | None,
           report: LoadReport) -> None:
    source = _source(edit, author, evidence_store)
    common = {"method": "human", "confidence": 1.0, "run_id": f"edit-{journal_id}", "origin": {"edit": journal_id}}
    if edit.op == "delete":
        _delete(store, edit, author, journal_id)
        return
    if edit.op == "merge":
        _merge(store, edit, author, journal_id, report)
        return
    facts: list[Fact] = []
    if edit.op == "create_node":
        _unmark(store, "node", edit.type, edit.node_id)
        facts.append(Fact("entity", edit.type, edit.key, source, **common))
    elif edit.op == "add_edge":
        _unmark(store, "edge", edit.type, to_json(list(edit.key)))
        facts.append(Fact("relation", edit.type, edit.key, source, **common))
    fields = dict(edit.fields)
    if edit.op == "set_field":
        fields[edit.field] = edit.value
    for name, value in fields.items():
        facts.append(Fact("field", edit.type, edit.key, source, field=name, value=value, **common))
    for fact in facts:
        write_human(store, fact)


def write_human(store: GraphStore, fact: Fact) -> None:
    """Write a person's fact as accepted; a field value becomes the locked active value, others superseded."""
    rowid, created = store.write_fact(fact, fact.run_id, locked=fact.kind == "field")
    table = {"entity": "node_sources", "relation": "edge_sources", "field": "field_values"}[fact.kind]
    if not created:
        store.update(table, rowid, {"review": "accepted", **({"locked": 1} if fact.kind == "field" else {})})
    if fact.kind == "field":
        owner = store.owner_of(fact)
        _unmark(store, "field", fact.type, owner[2], fact.field)
        supersede(store, owner + (fact.field,), [fact.value])
        store.select_active(*owner, fact.field)


def _unmark(store: GraphStore, item_kind: str, type_name: str, key: str, field_name: str | None = None) -> None:
    """A person re-creating a deleted item removes its deletion marker."""
    if (marker := store.deletion(item_kind, type_name, key, field_name)) is not None:
        store.delete("deletions", marker["rowid"])


def _mark(store: GraphStore, item_kind: str, type_name: str, key: str, field_name: str | None, sources: set[str],
          author: str, reason: str | None, journal_id: int) -> None:
    if store.deletion(item_kind, type_name, key, field_name) is None:
        store.insert("deletions", {"item_kind": item_kind, "type": type_name, "key": key, "field": field_name,
                                   "author": author, "ts": now(), "reason": reason,
                                   "sources_json": to_json(sorted(sources)), "journal_id": journal_id})


def _sources(rows: list[tuple[Fact, dict[str, Any]]]) -> set[str]:
    return {source_json(f.source) for f, _ in rows}


def _delete(store: GraphStore, edit: Edit, author: str, journal_id: int) -> None:
    """A deletion marker with the sources that supported the item; a node's edges are marked with it."""
    if edit.field is not None:
        if edit.type in store.schema.entities:
            owner = ("node", edit.type, edit.node_id)
        else:
            owner = ("edge", edit.type, to_json(list(edit.key)))
        rows = store.rows_of_field(*owner, edit.field, review="accepted")
        _mark(store, "field", edit.type, owner[2], edit.field, _sources(rows), author, edit.reason, journal_id)
        return
    if edit.type in store.schema.relations:
        [edge] = [e for e in store.edges(edit.type) if e.key == tuple(edit.key)]
        _mark(store, "edge", edit.type, to_json(list(edit.key)), None, _sources(store.accepted_rows_of_edge(edge)),
              author, edit.reason, journal_id)
        return
    node_id = edit.node_id
    node_rows = [r for r in store.accepted_rows_of(edit.type, node_id) if r[0].type == edit.type]
    for edge in store.edges():
        rel = store.schema.relation(edge.type)
        refs = {(edge.src_type, edge.src_id), (edge.dst_type, edge.dst_id)}
        refs |= {(f.ref, v) for f, v in zip(rel.identity, edge.key[2:], strict=True) if f.ref}
        if (edit.type, node_id) in refs:
            _mark(store, "edge", edge.type, to_json(list(edge.key)), None, _sources(store.accepted_rows_of_edge(edge)),
                  author, edit.reason or f"{edit.type} {node_id} deleted", journal_id)
    _mark(store, "node", edit.type, node_id, None, _sources(node_rows), author, edit.reason, journal_id)


def _merge(store: GraphStore, edit: Edit, author: str, journal_id: int, report: LoadReport) -> None:
    """Node (or mention) A into B: A becomes an alias of B, its accepted content moves to B through the gate."""
    for proposal_id in pending_mention(store, edit.type, edit.source):
        proposal = store.proposal(proposal_id)
        sub = _accept_resolution(store, proposal, edit.into, journal_id)
        report.applied += sub.applied
        report.proposed += sub.proposed
        report.proposals_created += sub.proposals_created
        _decide(store, proposal_id, "accepted", author, f"merged into {edit.into}")
    if store.alias_of(edit.type, edit.source) is None:
        store.insert("aliases", {"type": edit.type, "from_id": edit.source, "to_id": edit.into,
                                 "journal_id": journal_id})
    rows = store.accepted_rows_of(edit.type, edit.source)
    touched: set = set()
    for locked in (True, False):
        facts = [f for f, meta in rows if meta["locked"] == locked]
        touched |= Gate(store, report.run_id, trusted=True, lock=locked).run(facts, report)
    for owner in touched:
        store.select_active(*owner)

