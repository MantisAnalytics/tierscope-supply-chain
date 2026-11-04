"""Human edits: one JSON shape for the command line and the API, validated against the schema and the graph.

```json
{"op": "create_node", "type": "Supplier", "id": "S900001", "fields": {"name": "...", "country": "DE"}}
{"op": "set_field", "type": "Site", "key": ["P01"], "field": "recovery_time_months", "value": 9}
{"op": "add_edge", "type": "SUPPLIES", "key": ["S900001", "DMC-2221", null], "fields": {}}
{"op": "delete", "type": "Supplier", "key": ["S866350"], "field": null, "reason": "..."}
{"op": "merge", "type": "Supplier", "from": "mention:Bosch GmbH", "into": "S123456"}
```
Every op may carry `evidence: {"id"?, "page"?, "quote"?, "url"?, "comment"?}`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from scgraph.graph.schema import SchemaError, check_value, key_id
from scgraph.graph.tables import UNRESOLVED_PREFIX

if TYPE_CHECKING:
    from scgraph.editing.evidence import EvidenceStore
    from scgraph.graph.store import GraphStore

OPS = ("create_node", "set_field", "add_edge", "delete", "merge")
EVIDENCE_KEYS = ("id", "page", "quote", "url", "comment")


class EditError(ValueError):
    pass


class EditBatchError(ValueError):
    """Some edits of a batch are invalid; none was applied. `errors` lists (edit index, message)."""

    def __init__(self, errors: list[tuple[int, str]]) -> None:
        super().__init__("; ".join(f"edit {i + 1}: {m}" for i, m in errors))
        self.errors = errors


@dataclass
class Edit:
    op: str
    type: str
    key: tuple = ()
    field: str | None = None
    value: Any = None
    fields: dict[str, Any] = dataclasses.field(default_factory=dict)
    reason: str | None = None
    source: str | None = None  # merge: the node or `mention:` merged away
    into: str | None = None  # merge: the node that stays
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def node_id(self) -> str:
        return key_id(self.key)

    def describe(self) -> str:
        if self.op == "create_node":
            return f"create {self.type} {self.node_id}"
        if self.op == "set_field":
            return f"set {self.type} {_label(self.key)}.{self.field} = {self.value!r}"
        if self.op == "add_edge":
            return f"add {self.type} {_label(self.key)}"
        if self.op == "delete":
            return f"delete {self.type} {_label(self.key)}" + (f".{self.field}" if self.field else "")
        return f"merge {self.type} {self.source} into {self.into}"


def _label(key: tuple) -> str:
    return " -> ".join(str(p) for p in key if p is not None)


def parse_edit(data: Any) -> Edit:
    """An edit from its JSON shape; raises EditError for a malformed one."""
    if not isinstance(data, dict):
        raise EditError(f"an edit is a JSON object, got {data!r}")
    op, type_name = data.get("op"), data.get("type")
    if op not in OPS:
        raise EditError(f"op must be one of {', '.join(OPS)}, got {op!r}")
    if not isinstance(type_name, str) or not type_name:
        raise EditError("type is required")
    evidence = data.get("evidence") or {}
    if not isinstance(evidence, dict) or set(evidence) - set(EVIDENCE_KEYS):
        raise EditError(f"evidence takes {', '.join(EVIDENCE_KEYS)}, got {evidence!r}")
    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        raise EditError("fields must be an object of field name -> value")
    edit = Edit(op, type_name, fields=fields, field=data.get("field"), value=data.get("value"),
                reason=data.get("reason"), evidence=dict(evidence))
    if op == "merge":
        edit.source, edit.into = data.get("from"), data.get("into")
        if not isinstance(edit.source, str) or not isinstance(edit.into, str) or not edit.source or not edit.into:
            raise EditError("merge needs 'from' and 'into' node ids")
        return edit
    key = data.get("key", data.get("id"))
    if key is None:
        raise EditError(f"{op} needs a key (or an id)")
    edit.key = tuple(key) if isinstance(key, list | tuple) else (key,)
    if op == "set_field" and (not edit.field or "value" not in data):
        raise EditError("set_field needs 'field' and 'value'")
    return edit


def validate(store: GraphStore, edit: Edit, evidence: EvidenceStore | None = None) -> None:
    """Raise EditError when the edit does not fit the schema or the current graph."""
    schema = store.schema
    try:
        kind = schema.kind_of(edit.type)
        if edit.op in ("create_node", "merge") and kind != "entity":
            raise EditError(f"{edit.op} works on entity types; {edit.type} is a relation")
        if edit.op == "add_edge" and kind != "relation":
            raise EditError(f"add_edge needs a relation type; {edit.type} is an entity")
        if edit.op != "merge":
            schema.validate_key(edit.type, edit.key)
        for name, value in edit.fields.items():
            _check_field(store, edit.type, name, value)
    except SchemaError as e:
        raise EditError(str(e)) from None
    if edit.evidence.get("id") and (evidence is None or evidence.path(edit.evidence["id"]) is None):
        raise EditError(f"no evidence file {edit.evidence['id']!r}; upload it first")
    if edit.op == "create_node":
        if store.has_node(edit.type, edit.node_id):
            raise EditError(f"{edit.type} {edit.node_id!r} exists already")
    elif edit.op == "set_field":
        _require(store, edit.type, edit.key)
        try:
            _check_field(store, edit.type, edit.field, edit.value)
        except SchemaError as e:
            raise EditError(str(e)) from None
    elif edit.op == "add_edge":
        rel = schema.relation(edit.type)
        ends = [(rel.src, edit.key[0]), (rel.dst, edit.key[1])]
        ends += [(f.ref, v) for f, v in zip(rel.identity, edit.key[2:], strict=True) if f.ref and v is not None]
        for type_name, node_id in ends:
            if not store.has_node(type_name, node_id):
                raise EditError(f"no {type_name} {node_id!r} in the graph")
        if store.has_edge(edit.type, edit.key):
            raise EditError(f"{edit.type} {_label(edit.key)} exists already")
    elif edit.op == "delete":
        _require(store, edit.type, edit.key)
        if edit.field is not None:
            try:
                schema.field_def(edit.type, edit.field)
            except SchemaError as e:
                raise EditError(str(e)) from None
            owner = ("node", edit.type, edit.node_id) if kind == "entity" else store.owner_of(_probe(edit))
            if edit.field not in store.active_fields().get(owner, {}):
                raise EditError(f"{edit.type} {_label(edit.key)} has no value for {edit.field!r}")
    else:
        if not store.has_node(edit.type, edit.into):
            raise EditError(f"no {edit.type} {edit.into!r} in the graph")
        if edit.source == edit.into:
            raise EditError("a node cannot be merged into itself")
        if not store.has_node(edit.type, edit.source) and not pending_mention(store, edit.type, edit.source):
            raise EditError(f"no {edit.type} {edit.source!r} in the graph, and no pending mention of that name")


def _check_field(store: GraphStore, type_name: str, name: str, value: Any) -> None:
    fdef = store.schema.field_def(type_name, name)
    rel = store.schema.relations.get(type_name)
    if rel and name in rel.occurrence_fields:
        raise SchemaError(f"{type_name}.{name} is recorded per source row; it cannot be set by hand")
    check_value(fdef, value)


def _probe(edit: Edit):
    from scgraph.graph.facts import Fact

    return Fact("relation", edit.type, edit.key, None)


def _require(store: GraphStore, type_name: str, key: tuple) -> None:
    if type_name in store.schema.entities:
        if not store.has_node(type_name, key_id(key)):
            raise EditError(f"no {type_name} {key_id(key)!r} in the graph")
    elif not store.has_edge(type_name, key):
        raise EditError(f"no {type_name} {_label(key)} in the graph")


def pending_mention(store: GraphStore, type_name: str, node_id: str) -> list[int]:
    """Pending resolution proposals for a `mention:` id."""
    if not node_id.startswith(UNRESOLVED_PREFIX):
        return []
    mention = " ".join(node_id[len(UNRESOLVED_PREFIX):].split())
    return [p["id"] for p in store.proposals(status="pending", kind="resolution")
            if p["group_key"][3] == type_name and " ".join(p["group_key"][2].split()) == mention]
