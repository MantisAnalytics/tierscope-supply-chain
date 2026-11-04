"""The review gate: decides per fact whether it is applied, proposed, or dropped. Deterministic, no LLM calls.

```
fact
 |- item is an alias ---> re-key to the alias target, continue
 |- same (item, value, source) stored already, rejected before, or superseded by a person ---> unchanged / drop
 |- item deleted: source in marker's sources ---> drop ; else ---> restore proposal
 |- unresolved mention, or resolution below threshold ---> resolution proposal
 |- field with accepted value:  equal ---> accept as extra source
 |                              differs ---> conflict proposal (also when locked, any confidence)
 |- confidence >= threshold ---> accept
 '- else ---> mapping / statement proposal (by origin)
```
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scgraph.graph.facts import Fact
from scgraph.graph.rows import LoadReport, fingerprint, now, source_json, to_json
from scgraph.graph.schema import key_id
from scgraph.graph.tables import UNRESOLVED_PREFIX

if TYPE_CHECKING:
    from scgraph.graph.store import GraphStore

KINDS = ("mapping", "statement", "resolution", "conflict", "restore")
# Resolution methods that need no review whatever their confidence.
SURE_RESOLUTIONS = ("id", "human")


def same_value(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def mention_id(mention: str) -> str:
    """The provisional node id of an unresolved mention."""
    return f"{UNRESOLVED_PREFIX}{' '.join(mention.split())}"


def _short(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def origin_group(fact: Fact) -> tuple[str, list]:
    """(kind, group key) of the proposal a fact below the threshold joins: its spec column or document passage."""
    origin = fact.origin or {}
    if "spec" in origin:
        return "mapping", ["spec", origin["spec"], origin.get("region"), origin.get("column")]
    if "document" in origin:
        return "statement", ["document", origin["document"], origin.get("quote")]
    source = fact.source
    if source.quote:
        return "statement", ["document", source.file, source.quote]
    return "mapping", ["file", source.file, source.sheet, source.column if fact.kind == "field" else None]


def group_summary(kind: str, key: list, fact: Fact) -> str:
    target = f"{fact.type}.{fact.field}" if fact.kind == "field" else fact.type
    if kind == "mapping":
        by, where, region, column = key
        label = f"column {column!r}" if column else "row keys"
        place = f" of {'region' if by == 'spec' else 'sheet'} {region}" if region else ""
        return f"{label}{place} -> {target} ({Path(str(where)).name})"
    if kind == "statement":
        return f'statement "{_short(str(key[2] or ""))}" in {Path(str(key[1])).name}'
    if kind == "resolution":
        return f"resolve {key[3]} mention {key[2]!r} in {Path(str(key[1])).name}"
    if kind == "conflict":
        if key[0] == "spec":
            return f"column {key[3]!r} of region {key[2]} disagrees with accepted values ({Path(str(key[1])).name})"
        owner = key[3] if key[1] == "node" else " -> ".join(str(p) for p in json.loads(key[3]) if p)
        return f"{key[2]} {owner}.{key[4]}: value disagrees with the accepted one"
    item_kind, type_name, item_key, field_name = key[1:]
    label = item_key if item_kind != "edge" else " -> ".join(str(p) for p in json.loads(item_key) if p)
    return f"restore deleted {type_name} {label}" + (f".{field_name}" if field_name else "") \
        + f" (new evidence from {fact.source.file})"


class Gate:
    """One load's decisions. `trusted` applies facts a person already decided on (merges, resolved mentions):
    no threshold, by default no resolution check, but conflicts with accepted values still become proposals.
    `lock` marks the field values it applies as human-confirmed."""

    def __init__(self, store: GraphStore, run_id: str, *, trusted: bool = False, lock: bool = False,
                 check_resolution: bool | None = None) -> None:
        self.store = store
        self.run_id = run_id
        self.trusted = trusted
        self.lock = lock
        self.check_resolution = not trusted if check_resolution is None else check_resolution
        self.threshold = store.threshold
        self.report: LoadReport | None = None
        self.touched: set[tuple[str, str, str, str]] = set()

    def run(self, facts: list[Fact], report: LoadReport) -> set[tuple[str, str, str, str]]:
        """Decide every fact; returns the fields whose active value must be chosen again."""
        self.report = report
        fields: dict[tuple[str, str, str, str], list[Fact]] = {}
        for fact in facts:
            fact, rekeyed = self._rekey(fact)
            if self._known(fact) or self._deleted(fact):
                continue
            if self.check_resolution:
                fact, weak = self._resolve(fact, rekeyed)
                if weak is not None:
                    self._propose(fact, "resolution", ["mention", fact.source.file, weak[1], weak[0]])
                    continue
            if fact.kind == "field":
                fields.setdefault(self.store.owner_of(fact) + (fact.field,), []).append(fact)
            elif self._exists(fact) or self._passes(fact):
                self._accept(fact)
            else:
                self._propose(fact, *origin_group(fact))
        for owner, group in fields.items():
            self._fields(owner, group)
        self._name_columns(report.proposals_created)
        return self.touched

    def _name_columns(self, proposal_ids: list[int]) -> None:
        """A new column proposal is named after the fields it fills, its own column's target first."""
        for proposal_id in proposal_ids:
            proposal = self.store.proposal(proposal_id)
            if proposal["kind"] != "mapping" or not proposal["group_key"][3]:
                continue
            targets = [f"{t}.{f}" for t, f in self.store._db.execute(
                "SELECT owner_type, field FROM field_values WHERE proposal_id = ? GROUP BY owner_type, field"
                " ORDER BY MIN(id)", (proposal_id,))]
            if targets:
                more = f" (+{len(targets) - 1} more)" if len(targets) > 1 else ""
                by, where, region, column = proposal["group_key"]
                summary = f"column {column!r} of region {region} -> {targets[0]}{more} ({Path(str(where)).name})"
                self.store.update("proposals", proposal_id, {"summary": summary})

    # -- steps -----------------------------------------------------------------

    def _rekey(self, fact: Fact) -> tuple[Fact, set[int]]:
        """Replace node ids that are aliases (merged nodes, resolved mentions) by their targets."""
        store = self.store
        parts = list(fact.key)
        rekeyed: set[int] = set()
        for index, type_name in _node_refs(store, fact):
            target = store.resolve_alias(type_name, parts[index])
            if target != parts[index]:
                parts[index] = target
                rekeyed.add(index)
        return (_replace_key(fact, tuple(parts)) if rekeyed else fact), rekeyed

    def _known(self, fact: Fact) -> bool:
        """True when the fact needs no decision: the store has this candidate, or a person rejected it."""
        found = self.store.find_row(fact)
        if found is not None:
            if found[2] in ("rejected", "superseded"):
                self.report.dropped += 1
            else:
                self.report.unchanged += 1
            return True
        if self.store.is_rejected(self._fingerprint(fact)):
            self.report.dropped += 1
            return True
        return False

    def _fingerprint(self, fact: Fact) -> str:
        value = to_json(fact.value) if fact.kind == "field" else None
        return fingerprint(self.store.item_of(fact), value, source_json(fact.source))

    def _deleted(self, fact: Fact) -> bool:
        """True when the fact is about a deleted item: dropped if its source supported the item, else proposed."""
        marker = self._marker(fact)
        if marker is None:
            return False
        if source_json(fact.source) in marker["sources"]:
            self.report.dropped += 1
            return True
        key = ["deleted", marker["item_kind"], marker["type"], marker["key"], marker["field"]]
        self._propose(fact, "restore", key)
        return True

    def _marker(self, fact: Fact) -> dict | None:
        store = self.store
        candidates: list[tuple[str, str, str, str | None]] = []
        if fact.kind == "entity":
            candidates.append(("node", fact.type, key_id(fact.key), None))
        elif fact.kind == "relation":
            candidates.append(("edge", fact.type, to_json(list(fact.key)), None))
        else:
            owner_kind, owner_type, owner_key = store.owner_of(fact)
            candidates.append(("field", owner_type, owner_key, fact.field))
            candidates.append((owner_kind, owner_type, owner_key, None))
        if fact.type in store.schema.relations:
            candidates += [("node", t, fact.key[i], None) for i, t in _node_refs(store, fact)]
        for item_kind, type_name, key, field_name in candidates:
            if (marker := store.deletion(item_kind, type_name, key, field_name)) is not None:
                return marker
        return None

    def _resolve(self, fact: Fact, rekeyed: set[int]) -> tuple[Fact, tuple[str, str] | None]:
        """Apply resolved mentions; returns the fact and its first (type, mention) still needing a person."""
        if not fact.resolution:
            return fact, None
        mentions = fact.resolution.get("mentions") or [fact.resolution]
        parts = list(fact.key)
        weak = None
        for record, (index, type_name) in zip(mentions, _node_refs(self.store, fact), strict=False):
            method, confidence = record.get("method"), record.get("confidence", 0.0)
            if method in SURE_RESOLUTIONS or method != "unresolved" and confidence >= self.threshold:
                continue
            target = self.store.resolve_alias(type_name, mention_id(str(record.get("mention", ""))))
            if not target.startswith(UNRESOLVED_PREFIX):
                parts[index] = target
            elif index not in rekeyed and weak is None:
                weak = (type_name, str(record.get("mention", "")))
        if tuple(parts) != fact.key:
            fact = _replace_key(fact, tuple(parts))
        return fact, weak

    def _exists(self, fact: Fact) -> bool:
        """An entity or relation fact for an item that has an accepted source already: one more source."""
        q = self.store._db.execute
        if fact.kind == "entity":
            row = q("SELECT 1 FROM node_sources WHERE type = ? AND id = ? AND review = 'accepted'",
                    (fact.type, key_id(fact.key)))
        else:
            row = q("SELECT 1 FROM edge_sources s JOIN edges e ON e.id = s.edge_id WHERE e.type = ? AND e.key_json = ?"
                    " AND s.review = 'accepted'", (fact.type, to_json(list(fact.key))))
        return row.fetchone() is not None

    def _passes(self, fact: Fact) -> bool:
        return self.trusted or fact.confidence >= self.threshold

    def _fields(self, owner: tuple[str, str, str, str], group: list[Fact]) -> None:
        owner_kind, owner_type, owner_key, field_name = owner
        rel = self.store.schema.relations.get(owner_type)
        if rel and field_name in rel.occurrence_fields:  # per source row, nothing to disagree with
            for fact in group:
                self._accept(fact) if self._passes(fact) else self._propose(fact, *origin_group(fact))
            return
        accepted = [f.value for f, _ in self.store.rows_of_field(*owner, review="accepted")]
        if not accepted:
            best = min(range(len(group)), key=lambda i: (-group[i].confidence, i))
            if not self._passes(group[best]):
                for fact in group:
                    self._propose(fact, *origin_group(fact))
                return
            accepted = [group[best].value]
        for fact in group:
            if any(same_value(fact.value, v) for v in accepted):
                self._accept(fact)
            else:
                self._propose(fact, "conflict", self._conflict_key(fact, owner))
                self.touched.add(owner)

    def _conflict_key(self, fact: Fact, owner: tuple[str, str, str, str]) -> list:
        origin = fact.origin or {}
        if "spec" in origin and origin.get("column"):
            return ["spec", origin["spec"], origin.get("region"), origin["column"]]
        return ["field", *owner]

    # -- writes ----------------------------------------------------------------

    def _accept(self, fact: Fact) -> None:
        _, created = self.store.write_fact(fact, fact.run_id or self.run_id, locked=self.lock and fact.kind == "field")
        if created:
            self.report.applied += 1
            if fact.kind == "field":
                self.touched.add(self.store.owner_of(fact) + (fact.field,))
        else:
            self.report.unchanged += 1

    def _propose(self, fact: Fact, kind: str, group_key: list) -> None:
        store, report = self.store, self.report
        proposal_id = store.pending_proposal(kind, group_key)
        new = proposal_id is None
        if new:
            proposal_id = store.insert("proposals", {
                "kind": kind, "group_key": to_json(group_key), "status": "pending",
                "summary": group_summary(kind, group_key, fact), "created_run": self.run_id, "created_at": now(),
            })
            report.proposals_created.append(proposal_id)
        _, created = store.write_fact(fact, fact.run_id or self.run_id, review="proposed", proposal_id=proposal_id)
        if not created:
            report.unchanged += 1
            return
        report.proposed += 1
        if not new and proposal_id not in report.proposals_updated and proposal_id not in report.proposals_created:
            report.proposals_updated.append(proposal_id)
        if note := self._rejected_before(fact):
            self.add_note(proposal_id, note)

    def _rejected_before(self, fact: Fact) -> str | None:
        """A note when the same value for the same item was rejected before from another source."""
        store = self.store
        if fact.kind == "field":
            owner = store.owner_of(fact)
            for other, meta in store.rows_of_field(*owner, fact.field):
                if meta["review"] in ("rejected", "superseded") and same_value(other.value, fact.value):
                    by = f" (proposal #{meta['proposal_id']})" if meta["proposal_id"] else ""
                    return (f"{fact.type} {owner[2]}.{fact.field} = {fact.value!r} was "
                            f"{meta['review']} before{by}, from {other.source.label()}")
        return None

    def add_note(self, proposal_id: int, note: str) -> None:
        notes = self.store.proposal(proposal_id)["notes"]
        if note not in notes:
            self.store.update("proposals", proposal_id, {"notes_json": to_json([*notes, note])})


def _node_refs(store: GraphStore, fact: Fact) -> list[tuple[int, str]]:
    """(key index, node type) of every node a fact's key names; mention order of document facts."""
    if fact.type in store.schema.entities:
        return [(0, fact.type)] if len(fact.key) == 1 else []
    rel = store.schema.relation(fact.type)
    refs = [(0, rel.src), (1, rel.dst)]
    refs += [(2 + i, f.ref) for i, f in enumerate(rel.identity) if f.ref and fact.key[2 + i] is not None]
    return refs


def _replace_key(fact: Fact, key: tuple) -> Fact:
    return Fact(fact.kind, fact.type, key, fact.source, field=fact.field, value=fact.value, method=fact.method,
                confidence=fact.confidence, run_id=fact.run_id, resolution=fact.resolution, origin=fact.origin)
