"""SQLite graph store: nodes, edges and per-field candidate values with provenance and review state.

Every candidate row (`node_sources`, `edge_sources`, `field_values`) has a review state: `accepted`, `proposed`,
`rejected` or `superseded`. The visible graph is the projection of accepted rows minus deletion markers and
aliases. With `review=True` every load passes the review gate (`scgraph.review.gate`); with `review=False` all
valid facts are accepted, which is what extraction measurements use.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scgraph.config import DEFAULT_REVIEW_THRESHOLD
from scgraph.graph.facts import Fact, FactError, OracleValue, Parameter, SourceLocation
from scgraph.graph.rows import Edge, FieldValue, LoadReport, Operation, now, source_json, source_row, to_json
from scgraph.graph.schema import SCHEMA, Schema, key_id
from scgraph.graph.tables import ADDED_COLUMNS, FACT_TABLES, ROWID_ALIASED, SCHEMA_SQL, TABLES, UNRESOLVED_PREFIX


@dataclass
class _Visibility:
    nodes: dict[tuple[str, str], bool]  # (type, id) -> referenced only
    edges: list[Edge]
    deleted_fields: set[tuple[str, str, str]]  # (type, owner key, field)


class GraphStore:
    def __init__(self, path: str | Path, schema: Schema = SCHEMA, *, review: bool = True,
                 threshold: float = DEFAULT_REVIEW_THRESHOLD) -> None:
        self.path = Path(path)
        self.schema = schema
        self.review = review
        self.threshold = threshold
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # The API serves requests from a thread pool, one at a time (see scgraph.api.app).
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(SCHEMA_SQL)
        for table, columns in ADDED_COLUMNS.items():
            existing = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column.split()[0] not in existing:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        self._db.commit()
        self._op: Operation | None = None
        self._vis: _Visibility | None = None

    def close(self) -> None:
        self._db.close()

    def reset(self) -> None:
        with self._db:
            for table in TABLES:
                self._db.execute(f"DELETE FROM {table}")
        self._vis = None

    # -- tracked writes --------------------------------------------------------

    @contextmanager
    def operation(self, actor: str, action: str, summary: str = "", *,
                  track_inserts: bool = True, origin: str | None = None) -> Iterator[Operation]:
        """One SQLite transaction and one journal entry; rolls back on an exception. `origin` names where the
        change came from (`thread:<id>` for an edit applied from an agent thread)."""
        if self._op is not None:
            raise RuntimeError("operations do not nest")
        op = Operation(actor, action, summary, track_inserts)
        self._op = op
        try:
            with self._db:
                cur = self._db.execute(
                    "INSERT INTO journal (ts, actor, action, summary, items_json, inverse_json, origin) VALUES (?, ?,"
                    " ?, ?, '[]', '{}', ?)", (now(), actor, action, summary, origin))
                op.journal_id = cur.lastrowid
                yield op
                items = [[t, r] for t, r in op.before]
                inverse = {"rows": [[t, r, b] for (t, r), b in op.before.items()], "files": op.files, **op.extra}
                self._db.execute("UPDATE journal SET summary = ?, items_json = ?, inverse_json = ? WHERE id = ?",
                                 (op.summary, to_json(items), to_json(inverse), op.journal_id))
        finally:
            self._op = None
            self._vis = None

    def _snapshot(self, table: str, rowid: int) -> dict[str, Any] | None:
        cur = self._db.execute(f"SELECT rowid AS rowid_, * FROM {table} WHERE rowid = ?", (rowid,))
        row = cur.fetchone()
        if row is None:
            return None
        names = [d[0] for d in cur.description]
        data = dict(zip(names, row, strict=True))
        rowid_ = data.pop("rowid_")
        if table not in ROWID_ALIASED:
            data["rowid"] = rowid_
        return data

    def _remember(self, table: str, rowid: int, before: dict[str, Any] | None) -> None:
        if self._op is not None and (table, rowid) not in self._op.before:
            if before is not None or self._op.track_inserts:
                self._op.before[(table, rowid)] = before

    def insert(self, table: str, values: dict[str, Any], *, or_ignore: bool = False) -> int | None:
        """Insert a row; returns its rowid, or None when `or_ignore` hit an existing row."""
        cols = ", ".join(values)
        marks = ", ".join("?" * len(values))
        cur = self._db.execute(f"INSERT {'OR IGNORE ' if or_ignore else ''}INTO {table} ({cols}) VALUES ({marks})",
                               tuple(values.values()))
        if cur.rowcount == 0:
            return None
        self._vis = None
        self._remember(table, cur.lastrowid, None)
        return cur.lastrowid

    def update(self, table: str, rowid: int, values: dict[str, Any]) -> bool:
        before = self._snapshot(table, rowid)
        if before is None or all(before.get(k) == v for k, v in values.items()):
            return False
        sets = ", ".join(f"{k} = ?" for k in values)
        self._db.execute(f"UPDATE {table} SET {sets} WHERE rowid = ?", (*values.values(), rowid))
        self._vis = None
        self._remember(table, rowid, before)
        return True

    def delete(self, table: str, rowid: int) -> bool:
        before = self._snapshot(table, rowid)
        if before is None:
            return False
        self._db.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
        self._vis = None
        self._remember(table, rowid, before)
        return True

    def restore_row(self, table: str, rowid: int, before: dict[str, Any] | None) -> None:
        """Put a row back to a remembered state (None: the row did not exist)."""
        current = self._snapshot(table, rowid)
        self._remember(table, rowid, current)
        if before is None:
            self._db.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
        else:
            cols = ", ".join(before)
            self._db.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join('?' * len(before))})",
                             tuple(before.values()))
        self._vis = None

    # -- loading -------------------------------------------------------------

    def load(
        self,
        facts: Iterable[Fact],
        *,
        inputs: Iterable[str] = (),
        oracle: Iterable[OracleValue] = (),
        parameters: Iterable[Parameter] = (),
        run_id: str | None = None,
    ) -> LoadReport:
        """Load facts, oracle values and parameters as one run. Invalid facts are rejected, not fatal.

        With review on, the gate decides per fact: apply, propose, or drop.
        """
        run_id = run_id or uuid.uuid4().hex[:12]
        inputs = list(inputs)
        report = LoadReport(run_id=run_id)
        valid: list[Fact] = []
        for fact in facts:
            try:
                fact.validate(self.schema)
            except FactError as e:
                report.rejected.append((fact, str(e)))
                continue
            valid.append(fact if fact.run_id else _with_run(fact, run_id))
        report.loaded = len(valid)
        with self.operation("system", "ingest", track_inserts=False) as op:
            if self.review:
                from scgraph.review.gate import Gate

                touched = Gate(self, run_id).run(valid, report)
            else:
                touched = set()
                for fact in valid:
                    rowid, created = self.write_fact(fact, fact.run_id)
                    report.applied += created
                    report.unchanged += not created
                    if fact.kind == "field":
                        touched.add(self.owner_of(fact) + (fact.field,))
            for owner in sorted(touched):
                if self.select_active(*owner):
                    owner_kind, owner_type, owner_key, field_name = owner
                    report.flagged.append((owner_type, self._owner_label(owner_kind, owner_key), field_name))
            for ov in oracle:
                self._db.execute(
                    "INSERT OR REPLACE INTO oracle_values VALUES (?, ?, ?, ?, ?, ?)",
                    (ov.entity_type, ov.entity_id, ov.metric, float(ov.value), source_json(ov.source), ov.anomaly),
                )
                report.oracle_values += 1
            for p in parameters:
                self._db.execute(
                    "INSERT OR REPLACE INTO parameters VALUES (?, ?, ?)",
                    (p.name, to_json(p.value), source_json(p.source)),
                )
                report.parameters += 1
            self._vis = None
            report.referenced_only = self.referenced_only()
            self._db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (run_id, now(), to_json(inputs), to_json(report.counts())),
            )
            what = ", ".join(Path(i).name for i in inputs) or f"run {run_id}"
            op.summary = (f"ingest {what}: {report.applied} facts applied, {report.proposed} proposed"
                          f" ({len(report.proposals_created)} new proposals), {report.dropped} dropped")
            op.extra["counts"] = report.counts()
            report.journal_id = op.journal_id
        return report

    def owner_of(self, fact: Fact) -> tuple[str, str, str]:
        """(owner kind, type, owner key) of a field fact's node or edge."""
        if fact.type in self.schema.entities:
            return "node", fact.type, key_id(fact.key)
        return "edge", fact.type, to_json(list(fact.key))

    def item_of(self, fact: Fact) -> tuple:
        """What a fact is about: ("node", type, id), ("edge", type, key json) or ("field", kind, type, key, field)."""
        if fact.kind == "entity":
            return "node", fact.type, key_id(fact.key)
        if fact.kind == "relation":
            return "edge", fact.type, to_json(list(fact.key))
        return ("field", *self.owner_of(fact), fact.field)

    def find_row(self, fact: Fact) -> tuple[str, int, str] | None:
        """(table, rowid, review state) of the row holding exactly this fact (item, value, source), if any."""
        source = source_json(fact.source)
        if fact.kind == "entity":
            row = self._db.execute(
                "SELECT rowid, review FROM node_sources WHERE type = ? AND id = ? AND source_json = ?",
                (fact.type, key_id(fact.key), source)).fetchone()
            table = "node_sources"
        elif fact.kind == "relation":
            row = self._db.execute(
                "SELECT s.rowid, s.review FROM edge_sources s JOIN edges e ON e.id = s.edge_id"
                " WHERE e.type = ? AND e.key_json = ? AND s.source_json = ?",
                (fact.type, to_json(list(fact.key)), source)).fetchone()
            table = "edge_sources"
        else:
            owner_kind, owner_type, owner_key = self.owner_of(fact)
            row = self._db.execute(
                "SELECT id, review FROM field_values WHERE owner_kind = ? AND owner_type = ? AND owner_key = ?"
                " AND field = ? AND value_json = ? AND source_json = ?",
                (owner_kind, owner_type, owner_key, fact.field, to_json(fact.value), source)).fetchone()
            table = "field_values"
        return (table, row[0], row[1]) if row else None

    def write_fact(self, fact: Fact, run_id: str, *, review: str = "accepted", proposal_id: int | None = None,
                   locked: bool = False) -> tuple[int, bool]:
        """Write one fact's row; returns (rowid, created). An existing identical row is left as it is."""
        source = source_json(fact.source)
        common = {
            "source_json": source, "method": fact.method, "confidence": float(fact.confidence), "run_id": run_id,
            "resolution_json": to_json(fact.resolution) if fact.resolution is not None else None,
            "review": review, "proposal_id": proposal_id,
            "origin_json": to_json(fact.origin) if fact.origin is not None else None,
        }
        if fact.kind == "entity":
            node_id = key_id(fact.key)
            self._ensure_node(fact.type, node_id, described=True)
            rowid = self.insert("node_sources", {"type": fact.type, "id": node_id, **common}, or_ignore=True)
        elif fact.kind == "relation":
            edge_id = self._ensure_edge(fact.type, fact.key)
            rowid = self.insert("edge_sources", {"edge_id": edge_id, **common}, or_ignore=True)
        else:
            owner_kind, owner_type, owner_key = self.owner_of(fact)
            if owner_kind == "node":
                self._ensure_node(fact.type, owner_key, described=True)
            else:
                self._ensure_edge(fact.type, fact.key)
            status = "active" if review == "accepted" else review
            rowid = self.insert("field_values", {
                "owner_kind": owner_kind, "owner_type": owner_type, "owner_key": owner_key, "field": fact.field,
                "value_json": to_json(fact.value), "status": status, "locked": int(locked), **common,
            }, or_ignore=True)
        if rowid is not None:
            return rowid, True
        found = self.find_row(fact)
        return found[1], False

    def _ensure_node(self, type_name: str, node_id: str, *, described: bool) -> None:
        if described:
            self._db.execute(
                "INSERT INTO nodes VALUES (?, ?, 0) ON CONFLICT (type, id) DO UPDATE SET referenced_only = 0",
                (type_name, node_id),
            )
        else:
            self._db.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, 1)", (type_name, node_id))

    def _ensure_edge(self, type_name: str, key: tuple) -> int:
        rel = self.schema.relation(type_name)
        src_id, dst_id, *context = key
        self._ensure_node(rel.src, src_id, described=False)
        self._ensure_node(rel.dst, dst_id, described=False)
        for fdef, value in zip(rel.identity, context, strict=True):
            if fdef.ref and value is not None:
                self._ensure_node(fdef.ref, value, described=False)
        key_json = to_json(list(key))
        self._db.execute(
            "INSERT OR IGNORE INTO edges (type, key_json, src_type, src_id, dst_type, dst_id) VALUES (?, ?, ?, ?, ?, ?)",
            (type_name, key_json, rel.src, src_id, rel.dst, dst_id),
        )
        row = self._db.execute("SELECT id FROM edges WHERE type = ? AND key_json = ?", (type_name, key_json))
        return row.fetchone()[0]

    def select_active(self, owner_kind: str, owner_type: str, owner_key: str, field_name: str) -> bool:
        """Pick the active candidate of one field among accepted rows; return True if the field is flagged.

        A field is flagged when its accepted candidates disagree or a conflict proposal about it is pending.
        A locked (human-confirmed) candidate wins over others, the latest one among several; otherwise the highest
        confidence wins, then the first loaded.
        """
        rows = self._db.execute(
            "SELECT f.id, f.value_json, f.confidence, f.locked, f.review, f.status, f.active, p.kind, p.status"
            " FROM field_values f LEFT JOIN proposals p ON p.id = f.proposal_id"
            " WHERE f.owner_kind = ? AND f.owner_type = ? AND f.owner_key = ? AND f.field = ? ORDER BY f.id",
            (owner_kind, owner_type, owner_key, field_name),
        ).fetchall()
        accepted = [r for r in rows if r[4] == "accepted"]
        rel = self.schema.relations.get(owner_type)
        occurrence = bool(rel and field_name in rel.occurrence_fields)
        flagged = False
        best = None
        if accepted and not occurrence:
            locked = [r for r in accepted if r[3]]
            best = max(locked, key=lambda r: r[0])[0] if locked else max(accepted, key=lambda r: (r[2], -r[0]))[0]
            conflict = any(r[4] == "proposed" and r[7] == "conflict" and r[8] == "pending" for r in rows)
            flagged = len({r[1] for r in accepted}) > 1 or conflict
        for r in rows:
            if r[4] != "accepted":
                want = (0, r[4])
            elif occurrence:
                want = (1, "active")
            else:
                want = (int(r[0] == best), "flagged" if flagged else "active")
            if (r[6], r[5]) != want:
                self.update("field_values", r[0], {"active": want[0], "status": want[1]})
        return flagged

    def _owner_label(self, owner_kind: str, owner_key: str) -> str:
        return owner_key if owner_kind == "node" else " -> ".join(str(p) for p in json.loads(owner_key) if p)

    # -- review state --------------------------------------------------------

    def version(self) -> int:
        """Graph version: the latest journal entry id; every change to the graph adds one."""
        return self._db.execute("SELECT COALESCE(MAX(id), 0) FROM journal").fetchone()[0]

    def alias_of(self, type_name: str, node_id: str) -> str | None:
        row = self._db.execute("SELECT to_id FROM aliases WHERE type = ? AND from_id = ?", (type_name, node_id))
        found = row.fetchone()
        return found[0] if found else None

    def resolve_alias(self, type_name: str, node_id: str) -> str:
        """Follow aliases (merges, resolved mentions) to the node an id now means."""
        seen = {node_id}
        while (target := self.alias_of(type_name, node_id)) is not None and target not in seen:
            seen.add(target)
            node_id = target
        return node_id

    def deletion(self, item_kind: str, type_name: str, key: str, field_name: str | None = None) -> dict | None:
        row = self._db.execute(
            "SELECT rowid, author, ts, reason, sources_json, journal_id FROM deletions"
            " WHERE item_kind = ? AND type = ? AND key = ? AND field IS ?",
            (item_kind, type_name, key, field_name)).fetchone()
        if row is None:
            return None
        return {"rowid": row[0], "item_kind": item_kind, "type": type_name, "key": key, "field": field_name,
                "author": row[1], "ts": row[2], "reason": row[3], "sources": json.loads(row[4]), "journal_id": row[5]}

    def deletions(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT item_kind, type, key, field FROM deletions ORDER BY rowid").fetchall()
        return [self.deletion(*r) for r in rows]

    def is_rejected(self, fp: str) -> bool:
        return self._db.execute("SELECT 1 FROM rejections WHERE fingerprint = ?", (fp,)).fetchone() is not None

    def remember_rejection(self, fp: str) -> None:
        self.insert("rejections", {"fingerprint": fp}, or_ignore=True)

    def row_fact(self, table: str, rowid: int) -> tuple[Fact, dict[str, Any]]:
        """The fact a candidate row holds, and the row's review columns."""
        if table == "node_sources":
            row = self._db.execute(
                "SELECT type, id, source_json, method, confidence, run_id, resolution_json, origin_json, review,"
                " proposal_id FROM node_sources WHERE rowid = ?", (rowid,)).fetchone()
            type_name, node_id, *rest = row
            key = tuple(node_id.split("::")) if len(self.schema.entity(type_name).identity) > 1 else (node_id,)
            fact = Fact("entity", type_name, key, None)
            meta = {"locked": False}
        elif table == "edge_sources":
            row = self._db.execute(
                "SELECT e.type, e.key_json, s.source_json, s.method, s.confidence, s.run_id, s.resolution_json,"
                " s.origin_json, s.review, s.proposal_id FROM edge_sources s JOIN edges e ON e.id = s.edge_id"
                " WHERE s.rowid = ?", (rowid,)).fetchone()
            type_name, key_json, *rest = row
            fact = Fact("relation", type_name, tuple(json.loads(key_json)), None)
            meta = {"locked": False}
        else:
            row = self._db.execute(
                "SELECT owner_kind, owner_type, owner_key, field, value_json, locked, source_json, method, confidence,"
                " run_id, resolution_json, origin_json, review, proposal_id FROM field_values WHERE id = ?",
                (rowid,)).fetchone()
            owner_kind, type_name, owner_key, field_name, value_json, locked, *rest = row
            if owner_kind == "node":
                key = tuple(owner_key.split("::")) if len(self.schema.entity(type_name).identity) > 1 else (owner_key,)
            else:
                key = tuple(json.loads(owner_key))
            fact = Fact("field", type_name, key, None, field=field_name, value=json.loads(value_json))
            meta = {"locked": bool(locked)}
        source, method, confidence, run_id, resolution, origin, review, proposal_id = rest
        fact = Fact(fact.kind, fact.type, fact.key, SourceLocation.from_dict(json.loads(source)), field=fact.field,
                    value=fact.value, method=method, confidence=confidence, run_id=run_id,
                    resolution=json.loads(resolution) if resolution else None,
                    origin=json.loads(origin) if origin else None)
        meta.update(review=review, proposal_id=proposal_id, table=table, rowid=rowid)
        return fact, meta

    def proposal_rows(self, proposal_id: int, review: str | None = None) -> list[tuple[Fact, dict[str, Any]]]:
        """Facts of a proposal with their row metadata, in load order."""
        out = []
        cond, args = ("AND review = ?", (proposal_id, review)) if review else ("", (proposal_id,))
        for table in FACT_TABLES:
            for (rowid,) in self._db.execute(f"SELECT rowid FROM {table} WHERE proposal_id = ? {cond} ORDER BY rowid",
                                             args):
                out.append(self.row_fact(table, rowid))
        return out

    def rows_of_field(self, owner_kind: str, owner_type: str, owner_key: str, field_name: str,
                      review: str | None = None) -> list[tuple[Fact, dict[str, Any]]]:
        cond = "AND review = ?" if review else ""
        args = (owner_kind, owner_type, owner_key, field_name) + ((review,) if review else ())
        rows = self._db.execute(
            "SELECT id FROM field_values WHERE owner_kind = ? AND owner_type = ? AND owner_key = ? AND field = ?"
            f" {cond} ORDER BY id", args).fetchall()
        return [self.row_fact("field_values", r[0]) for r in rows]

    def accepted_rows_of(self, type_name: str, node_id: str) -> list[tuple[Fact, dict[str, Any]]]:
        """Accepted node sources and field values of a node, and accepted sources and fields of its edges."""
        out = []
        for (rowid,) in self._db.execute("SELECT rowid FROM node_sources WHERE type = ? AND id = ? AND review ="
                                         " 'accepted' ORDER BY rowid", (type_name, node_id)):
            out.append(self.row_fact("node_sources", rowid))
        for (rowid,) in self._db.execute("SELECT id FROM field_values WHERE owner_kind = 'node' AND owner_type = ?"
                                         " AND owner_key = ? AND review = 'accepted' ORDER BY id",
                                         (type_name, node_id)):
            out.append(self.row_fact("field_values", rowid))
        for edge in self.edges_of(type_name, node_id, include_hidden=True):
            out += self.accepted_rows_of_edge(edge)
        return out

    def accepted_rows_of_edge(self, edge: Edge) -> list[tuple[Fact, dict[str, Any]]]:
        out = []
        for (rowid,) in self._db.execute("SELECT rowid FROM edge_sources WHERE edge_id = ? AND review = 'accepted'"
                                         " ORDER BY rowid", (edge.id,)):
            out.append(self.row_fact("edge_sources", rowid))
        for (rowid,) in self._db.execute("SELECT id FROM field_values WHERE owner_kind = 'edge' AND owner_type = ?"
                                         " AND owner_key = ? AND review = 'accepted' ORDER BY id",
                                         (edge.type, to_json(list(edge.key)))):
            out.append(self.row_fact("field_values", rowid))
        return out

    def proposals(self, status: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._db.execute(f"SELECT id FROM proposals {clause} ORDER BY id", args).fetchall()
        return [self.proposal(r[0]) for r in rows]

    def proposal(self, proposal_id: int) -> dict[str, Any] | None:
        cur = self._db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        row = cur.fetchone()
        if row is None:
            return None
        data = dict(zip([d[0] for d in cur.description], row, strict=True))
        data["group_key"] = json.loads(data["group_key"])
        data["notes"] = json.loads(data.pop("notes_json"))
        data["impact"] = json.loads(data.pop("impact_json")) if data["impact_json"] else None
        counts, low, high = 0, [], []
        for table in FACT_TABLES:
            n, lo, hi = self._db.execute(f"SELECT COUNT(*), MIN(confidence), MAX(confidence) FROM {table}"
                                         " WHERE proposal_id = ?", (proposal_id,)).fetchone()
            counts += n
            low += [lo] if lo is not None else []
            high += [hi] if hi is not None else []
        data["facts"] = counts
        data["confidence"] = [min(low), max(high)] if low else [None, None]
        return data

    def pending_proposal(self, kind: str, group_key: list) -> int | None:
        row = self._db.execute("SELECT id FROM proposals WHERE kind = ? AND group_key = ? AND status = 'pending'",
                               (kind, to_json(group_key))).fetchone()
        return row[0] if row else None

    def journal(self, *, before: int | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """Journal entries, newest first."""
        where, args = ("WHERE id < ?", [before]) if before else ("", [])
        lim = f"LIMIT {int(limit)}" if limit else ""
        rows = self._db.execute(f"SELECT id, ts, actor, action, summary, items_json, undone_by, origin FROM journal"
                                f" {where} ORDER BY id DESC {lim}", args).fetchall()
        return [{"id": i, "ts": ts, "actor": a, "action": act, "summary": s, "items": len(json.loads(items)),
                 "undone_by": u, "origin": o} for i, ts, a, act, s, items, u, o in rows]

    def journal_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = self._db.execute("SELECT id, ts, actor, action, summary, items_json, inverse_json, undone_by, origin"
                               " FROM journal WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        i, ts, a, act, s, items, inverse, u, o = row
        return {"id": i, "ts": ts, "actor": a, "action": act, "summary": s,
                "items": [tuple(x) for x in json.loads(items)], "inverse": json.loads(inverse), "undone_by": u,
                "origin": o}

    # -- reading -------------------------------------------------------------

    def _visibility(self) -> _Visibility:
        """Accepted nodes and edges minus deletions and aliases; cached until the next write."""
        if self._vis is not None:
            return self._vis
        q = self._db.execute
        hidden = {(t, k) for t, k in q("SELECT type, key FROM deletions WHERE item_kind = 'node'")}
        hidden |= {(t, f) for t, f in q("SELECT type, from_id FROM aliases")}
        deleted_edges = {(t, k) for t, k in q("SELECT type, key FROM deletions WHERE item_kind = 'edge'")}
        deleted_fields = {(t, k, f) for t, k, f in
                          q("SELECT type, key, field FROM deletions WHERE item_kind = 'field'")}
        described = {(t, i) for t, i in q("SELECT DISTINCT type, id FROM node_sources WHERE review = 'accepted'")}
        described |= {(t, k) for t, k in q("SELECT DISTINCT owner_type, owner_key FROM field_values"
                                           " WHERE owner_kind = 'node' AND review = 'accepted'")}
        edges = []
        referenced: set[tuple[str, str]] = set()
        rows = q("SELECT id, type, key_json, src_type, src_id, dst_type, dst_id FROM edges e WHERE EXISTS"
                 " (SELECT 1 FROM edge_sources s WHERE s.edge_id = e.id AND s.review = 'accepted') ORDER BY id")
        for i, t, k, st, si, dt, di in rows:
            if (t, k) in deleted_edges:
                continue
            edge = Edge(i, t, tuple(json.loads(k)), st, si, dt, di)
            ends = {(st, si), (dt, di)}
            rel = self.schema.relation(t)
            ends |= {(f.ref, v) for f, v in zip(rel.identity, edge.key[2:], strict=True) if f.ref and v is not None}
            if ends & hidden:
                continue
            edges.append(edge)
            referenced |= ends
        nodes = {n: n not in described for n in (described | referenced) - hidden}
        self._vis = _Visibility(nodes, edges, deleted_fields)
        return self._vis

    def nodes(self, type_name: str | None = None) -> list[tuple[str, str, bool]]:
        return sorted((t, i, r) for (t, i), r in self._visibility().nodes.items() if type_name in (None, t))

    def proposed_node_ids(self) -> list[tuple[str, str]]:
        """(type, id) of nodes that only a proposal still under review would add or describe."""
        return sorted({(t, i) for t, i in self._db.execute(
            "SELECT DISTINCT type, id FROM node_sources WHERE review = 'proposed'")})

    def has_node(self, type_name: str, node_id: str) -> bool:
        return (type_name, node_id) in self._visibility().nodes

    def referenced_only(self) -> list[tuple[str, str]]:
        return sorted(n for n, r in self._visibility().nodes.items() if r)

    def edges(self, type_name: str | None = None) -> list[Edge]:
        return [e for e in self._visibility().edges if type_name in (None, e.type)]

    def has_edge(self, type_name: str, key: tuple) -> bool:
        return any(e.type == type_name and e.key == tuple(key) for e in self._visibility().edges)

    def edges_of(self, type_name: str, node_id: str, *, include_hidden: bool = False) -> list[Edge]:
        """Edges touching a node in either direction; with `include_hidden`, also edges not accepted or hidden."""
        if include_hidden:
            rows = self._db.execute(
                "SELECT id, type, key_json, src_type, src_id, dst_type, dst_id FROM edges WHERE (src_type = ? AND"
                " src_id = ?) OR (dst_type = ? AND dst_id = ?) ORDER BY id", (type_name, node_id, type_name, node_id))
            return [Edge(i, t, tuple(json.loads(k)), st, si, dt, di) for i, t, k, st, si, dt, di in rows]
        return [e for e in self._visibility().edges
                if (e.src_type, e.src_id) == (type_name, node_id) or (e.dst_type, e.dst_id) == (type_name, node_id)]

    def remove_edge(self, type_name: str, key: tuple) -> bool:
        key_json = to_json(list(key))
        row = self._db.execute("SELECT id FROM edges WHERE type = ? AND key_json = ?", (type_name, key_json)).fetchone()
        if not row:
            return False
        with self._db:
            self._db.execute("DELETE FROM edges WHERE id = ?", row)
            self._db.execute("DELETE FROM edge_sources WHERE edge_id = ?", row)
            self._db.execute(
                "DELETE FROM field_values WHERE owner_kind = 'edge' AND owner_type = ? AND owner_key = ?",
                (type_name, key_json),
            )
        self._vis = None
        return True

    def field_values(self, type_name: str, key: tuple | str) -> dict[str, list[FieldValue]]:
        """All candidate values per field of a node (key or id) or edge (key tuple), active first."""
        if type_name in self.schema.entities:
            owner_kind, owner_key = "node", key if isinstance(key, str) else key_id(key)
        else:
            owner_kind, owner_key = "edge", to_json(list(key))
        rows = self._db.execute(
            "SELECT field, value_json, confidence, method, status, source_json, active, locked, run_id,"
            " resolution_json, review, proposal_id, origin_json, id FROM field_values"
            " WHERE owner_kind = ? AND owner_type = ? AND owner_key = ? ORDER BY field, active DESC, id",
            (owner_kind, type_name, owner_key),
        )
        out: dict[str, list[FieldValue]] = {}
        for f, v, c, m, s, src, a, lk, r, res, rev, pid, origin, i in rows:
            out.setdefault(f, []).append(
                FieldValue(json.loads(v), c, m, s, SourceLocation.from_dict(json.loads(src)), bool(a), bool(lk), r,
                           json.loads(res) if res else None, rev, pid, json.loads(origin) if origin else None, i)
            )
        return out

    def node_sources(self, type_name: str, node_id: str, *, all_states: bool = False
                     ) -> list[tuple[SourceLocation, str, float, dict | None]]:
        """Sources of a node's existence (accepted ones unless `all_states`)."""
        cond = "" if all_states else "AND review = 'accepted'"
        rows = self._db.execute(
            "SELECT source_json, method, confidence, resolution_json FROM node_sources WHERE type = ? AND id = ?"
            f" {cond} ORDER BY rowid",
            (type_name, node_id),
        )
        return [(SourceLocation.from_dict(json.loads(s)), m, c, json.loads(r) if r else None) for s, m, c, r in rows]

    def edge_sources(self, edge_id: int) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT source_json, method, confidence, resolution_json, review, proposal_id FROM edge_sources"
            " WHERE edge_id = ? ORDER BY rowid", (edge_id,))
        return [{"source": SourceLocation.from_dict(json.loads(s)), "method": m, "confidence": c,
                 "resolution": json.loads(r) if r else None, "review": rev, "proposal_id": pid}
                for s, m, c, r, rev, pid in rows]

    def active_fields(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        """(owner_kind, owner_type, owner_key) -> {field: active value}, occurrence and deleted fields excluded."""
        out: dict[tuple[str, str, str], dict[str, Any]] = {}
        deleted = self._visibility().deleted_fields
        rows = self._db.execute(
            "SELECT owner_kind, owner_type, owner_key, field, value_json FROM field_values"
            " WHERE active = 1 AND review = 'accepted' ORDER BY id"
        )
        for kind, otype, okey, f, v in rows:
            rel = self.schema.relations.get(otype)
            if rel and f in rel.occurrence_fields or (otype, okey, f) in deleted:
                continue
            out.setdefault((kind, otype, okey), {})[f] = json.loads(v)
        return out

    def occurrences(self, type_name: str | None = None) -> dict[int, list[dict[str, Any]]]:
        """Edge id -> one entry per accepted source row with its occurrence field values and source."""
        out: dict[int, list[dict[str, Any]]] = {}
        where, args = ("AND e.type = ?", (type_name,)) if type_name else ("", ())
        sources = self._db.execute(
            f"SELECT e.id, e.type, e.key_json, s.source_json FROM edge_sources s JOIN edges e ON e.id = s.edge_id"
            f" WHERE s.review = 'accepted' {where} ORDER BY e.id, s.rowid",
            args,
        ).fetchall()
        values: dict[tuple[str, str, str], dict[str, Any]] = {}
        for otype, okey, f, v, src in self._db.execute(
            "SELECT owner_type, owner_key, field, value_json, source_json FROM field_values WHERE owner_kind = 'edge'"
            " AND review = 'accepted'"
        ):
            rel = self.schema.relations.get(otype)
            if rel and f in rel.occurrence_fields:
                values.setdefault((otype, okey, source_row(src)), {})[f] = json.loads(v)
        for edge_id, otype, okey, src in sources:
            if not self.schema.relation(otype).occurrence_fields:
                continue
            entry = dict(values.get((otype, okey, source_row(src)), {}))
            entry["source"] = SourceLocation.from_dict(json.loads(src))
            out.setdefault(edge_id, []).append(entry)
        return out

    def oracle_values(self, metric: str | None = None) -> list[OracleValue]:
        where, args = ("WHERE metric = ?", (metric,)) if metric else ("", ())
        rows = self._db.execute(
            f"SELECT entity_type, entity_id, metric, value, source_json, anomaly FROM oracle_values {where}"
            " ORDER BY metric, entity_type, entity_id",
            args,
        )
        return [OracleValue(t, i, m, v, SourceLocation.from_dict(json.loads(s)), a) for t, i, m, v, s, a in rows]

    def parameters(self) -> dict[str, Parameter]:
        rows = self._db.execute("SELECT name, value_json, source_json FROM parameters ORDER BY name")
        return {n: Parameter(n, json.loads(v), SourceLocation.from_dict(json.loads(s))) for n, v, s in rows}

    def runs(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT id, started_at, inputs_json, counts_json FROM runs ORDER BY rowid")
        return [{"id": i, "started_at": t, "inputs": json.loads(inp), "counts": json.loads(c)} for i, t, inp, c in rows]

    def stats(self) -> dict[str, Any]:
        q = self._db.execute
        vis = self._visibility()
        nodes: dict[str, int] = {}
        for t, _ in vis.nodes:
            nodes[t] = nodes.get(t, 0) + 1
        edges: dict[str, int] = {}
        for e in vis.edges:
            edges[e.type] = edges.get(e.type, 0) + 1
        visible = {e.id for e in vis.edges}
        occurrences: dict[str, int] = {}
        for edge_id, t in q("SELECT s.edge_id, e.type FROM edge_sources s JOIN edges e ON e.id = s.edge_id"
                            " WHERE s.review = 'accepted'"):
            if edge_id in visible and self.schema.relation(t).occurrence_fields:
                occurrences[t] = occurrences.get(t, 0) + 1
        flagged = q(
            "SELECT COUNT(*) FROM (SELECT DISTINCT owner_kind, owner_type, owner_key, field"
            " FROM field_values WHERE status = 'flagged')"
        ).fetchone()[0]
        return {
            "nodes": nodes,
            "edges": edges,
            "occurrences": occurrences,
            "field_values": q("SELECT COUNT(*) FROM field_values WHERE review = 'accepted'").fetchone()[0],
            "flagged_fields": flagged,
            "referenced_only": sum(vis.nodes.values()),
            "unresolved_mentions": sum(1 for _, i in vis.nodes if i.startswith(UNRESOLVED_PREFIX)),
            "oracle_values": q("SELECT COUNT(*) FROM oracle_values").fetchone()[0],
            "parameters": q("SELECT COUNT(*) FROM parameters").fetchone()[0],
            "runs": q("SELECT COUNT(*) FROM runs").fetchone()[0],
            "proposals": dict(q("SELECT kind, COUNT(*) FROM proposals WHERE status = 'pending' GROUP BY kind"
                                " ORDER BY kind").fetchall()),
        }


def _with_run(fact: Fact, run_id: str) -> Fact:
    return Fact(fact.kind, fact.type, fact.key, fact.source, field=fact.field, value=fact.value, method=fact.method,
                confidence=fact.confidence, run_id=run_id, resolution=fact.resolution, origin=fact.origin)
