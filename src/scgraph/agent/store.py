"""The agent's tables in ops.sqlite: threads, messages, plan versions, events and edit blocks.

They share the operations database (one connection and lock with `OpsStore`) because plans, runs and actions
reference each other. `migrate` brings a database of any earlier version up to date on open; it is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disruption_id INTEGER UNIQUE,                  -- NULL for a general thread
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',         -- active | archived
    needs TEXT,                                    -- why a person is needed: plan ready | agent failed | budget
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    author TEXT NOT NULL,                          -- a person's name, agent:<model>, or system
    role TEXT NOT NULL,                            -- person | agent | system
    blocks_json TEXT NOT NULL,                     -- [{type, text} | {type, ref}]
    status TEXT NOT NULL DEFAULT 'done',           -- queued | done (inputs wait as queued until their turn)
    profile TEXT,                                  -- the profile a queued input runs: chat | planner
    run_id INTEGER,                                -- the turn that answered (inputs) or wrote it (agent)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disruption_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    run_id INTEGER,
    graph_version INTEGER,                         -- NULL for plans migrated from before versions existed
    actions_json TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    outcome_json TEXT,
    skill REAL,
    created_at TEXT NOT NULL,
    UNIQUE (disruption_id, version)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    thread_id INTEGER,
    run_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edit_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    edits_json TEXT NOT NULL,
    before_json TEXT NOT NULL,                     -- current values of the touched fields when proposed
    preview_json TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',       -- proposed | applied | dismissed | stale
    decided_by TEXT,
    decided_at TEXT,
    journal_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages (thread_id, id);
CREATE INDEX IF NOT EXISTS plans_disruption ON plans (disruption_id, version);
CREATE INDEX IF NOT EXISTS events_thread ON events (thread_id);
"""
# Columns added to operations tables; ALTER TABLE ADD COLUMN when missing.
ADDED_COLUMNS = {
    "runs": ("thread_id INTEGER", "profile TEXT", "prompt_version TEXT", "world TEXT", "modes_json TEXT",
             "seed INTEGER", "plan_id INTEGER", "trigger_event_id INTEGER"),
    "actions": ("plan_id INTEGER",),
}
BLOCK_TYPES = ("text", "plan", "simulation", "edit", "draft", "decision", "event")
RUN_META = ("thread_id", "profile", "prompt_version", "world", "modes", "seed", "plan_id", "trigger_event_id")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def migrate(db: sqlite3.Connection) -> None:
    """Create the agent tables and columns, let runs exist without a disruption, give every disruption its thread
    and turn every done run with a plan into the next plan version of its disruption."""
    with db:
        db.executescript(SCHEMA)
        for table, columns in ADDED_COLUMNS.items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column.split()[0] not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        _nullable_run_disruption(db)
        for did, data, status, created in db.execute(
                "SELECT id, data_json, status, created_at FROM disruptions d WHERE NOT EXISTS "
                "(SELECT 1 FROM threads t WHERE t.disruption_id = d.id) ORDER BY id").fetchall():
            db.execute("INSERT INTO threads (disruption_id, title, status, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?)", (did, json.loads(data).get("name") or f"Disruption {did}",
                                                  "archived" if status == "archived" else "active", created, created))
        runs = db.execute("SELECT id, disruption_id, plan_json, summary, outcome_json, skill, finished_at, model "
                          "FROM runs WHERE status = 'done' AND plan_json IS NOT NULL AND plan_id IS NULL "
                          "AND disruption_id IS NOT NULL ORDER BY id").fetchall()
        for rid, did, plan, summary, outcome, score, finished, model in runs:
            [version] = db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM plans WHERE disruption_id = ?",
                                   (did,)).fetchone()
            pid = db.execute("INSERT INTO plans (disruption_id, version, run_id, graph_version, actions_json, summary,"
                             " outcome_json, skill, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                             (did, version, rid, plan, summary or "", outcome, score, finished or now())).lastrowid
            row = db.execute("SELECT id FROM threads WHERE disruption_id = ?", (did,)).fetchone()
            tid = row[0] if row else None
            db.execute("UPDATE runs SET plan_id = ?, thread_id = COALESCE(thread_id, ?), profile = COALESCE(profile, "
                       "'planner'), world = COALESCE(world, 'live') WHERE id = ?", (pid, tid, rid))
            db.execute("UPDATE actions SET plan_id = ? WHERE run_id = ? AND plan_id IS NULL", (pid, rid))
            if tid is not None:
                db.execute("INSERT INTO messages (thread_id, author, role, blocks_json, run_id, created_at) "
                           "VALUES (?, ?, 'agent', ?, ?, ?)", (tid, f"agent:{model}",
                                                               _dumps([{"type": "plan", "ref": pid}]), rid,
                                                               finished or now()))


def _nullable_run_disruption(db: sqlite3.Connection) -> None:
    """Runs of general threads have no disruption; SQLite cannot drop NOT NULL, so the table is rebuilt once."""
    info = {row[1]: row for row in db.execute("PRAGMA table_info(runs)")}
    if not info["disruption_id"][3]:
        return
    [sql] = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'").fetchone()
    db.execute("ALTER TABLE runs RENAME TO runs_before_agent")
    db.execute(sql.replace("disruption_id INTEGER NOT NULL", "disruption_id INTEGER", 1))
    columns = ", ".join(info)
    db.execute(f"INSERT INTO runs ({columns}) SELECT {columns} FROM runs_before_agent")
    db.execute("DROP TABLE runs_before_agent")
    db.execute("CREATE INDEX IF NOT EXISTS runs_disruption ON runs (disruption_id)")


class AgentStore:
    """Reads and writes of the agent tables, on the operations database's connection and lock."""

    def __init__(self, db: sqlite3.Connection, lock: threading.RLock) -> None:
        self._db, self._lock = db, lock

    def _one(self, query: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(query, args).fetchone()

    def _all(self, query: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(query, args).fetchall()

    def _write(self, query: str, args: tuple = ()) -> int:
        with self._lock, self._db:
            return self._db.execute(query, args).lastrowid

    # threads -------------------------------------------------------------------------------------------------

    def create_thread(self, title: str, disruption_id: int | None = None) -> int:
        t = now()
        return self._write("INSERT INTO threads (disruption_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                           (disruption_id, title, t, t))

    def thread(self, tid: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM threads WHERE id = ?", (tid,))
        return dict(row) if row else None

    def thread_for(self, did: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM threads WHERE disruption_id = ?", (did,))
        return dict(row) if row else None

    def threads(self, status: str | None = None, disruption_id: int | None = None,
                needs: bool | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM threads WHERE 1 = 1", []
        if status is not None:
            query += " AND status = ?"
            args.append(status)
        if disruption_id is not None:
            query += " AND disruption_id = ?"
            args.append(disruption_id)
        if needs is not None:
            query += " AND needs IS NOT NULL" if needs else " AND needs IS NULL"
        return [dict(r) for r in self._all(query + " ORDER BY updated_at DESC, id DESC", tuple(args))]

    def update_thread(self, tid: int, **changes: Any) -> None:
        allowed = {"disruption_id", "title", "status", "needs"}
        assert set(changes) <= allowed, changes
        sets = "".join(f"{k} = ?, " for k in changes)
        self._write(f"UPDATE threads SET {sets}updated_at = ? WHERE id = ?", (*changes.values(), now(), tid))

    def bind_disruption(self, did: int, tid: int | None, title: str) -> int:
        """Give a disruption its thread: the one it has, else the general thread `tid` when that is still general,
        else a new one. Returns the thread id."""
        with self._lock:
            if (found := self.thread_for(did)) is not None:
                return found["id"]
            thread = self.thread(tid) if tid is not None else None
            if thread is not None and thread["disruption_id"] is None:
                self.update_thread(tid, disruption_id=did, title=title or thread["title"])
                return tid
            return self.create_thread(title or f"Disruption {did}", did)

    # messages ------------------------------------------------------------------------------------------------

    def add_message(self, tid: int, author: str, role: str, blocks: list[dict[str, Any]], *, status: str = "done",
                    profile: str | None = None, run_id: int | None = None) -> int:
        assert all(b["type"] in BLOCK_TYPES for b in blocks), blocks
        t = now()
        with self._lock, self._db:
            mid = self._db.execute("INSERT INTO messages (thread_id, author, role, blocks_json, status, profile, run_id,"
                                   " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                   (tid, author, role, _dumps(blocks), status, profile, run_id, t)).lastrowid
            self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (t, tid))
        return mid

    def message(self, mid: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM messages WHERE id = ?", (mid,))
        return _message(row) if row else None

    def messages(self, tid: int, last: int | None = None) -> list[dict[str, Any]]:
        if last is None:
            rows = self._all("SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (tid,))
        else:
            rows = self._all("SELECT * FROM (SELECT * FROM messages WHERE thread_id = ? ORDER BY id DESC LIMIT ?) "
                             "ORDER BY id", (tid, last))
        return [_message(r) for r in rows]

    def thread_of_block(self, kind: str, ref: int) -> int | None:
        """The thread whose messages show the block {type: kind, ref}, if any (the first one)."""
        row = self._one("SELECT m.thread_id FROM messages m, json_each(m.blocks_json) b WHERE "
                        "json_extract(b.value, '$.type') = ? AND json_extract(b.value, '$.ref') = ? ORDER BY m.id "
                        "LIMIT 1", (kind, ref))
        return row[0] if row else None

    def next_queued(self, tid: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM messages WHERE thread_id = ? AND status = 'queued' ORDER BY id LIMIT 1", (tid,))
        return _message(row) if row else None

    def queued_threads(self) -> list[int]:
        return [r[0] for r in self._all("SELECT DISTINCT thread_id FROM messages WHERE status = 'queued' "
                                        "ORDER BY thread_id")]

    def finish_message(self, mid: int, run_id: int | None) -> None:
        self._write("UPDATE messages SET status = 'done', run_id = COALESCE(?, run_id) WHERE id = ?", (run_id, mid))

    # runs (the turn record lives in `runs`) ------------------------------------------------------------------

    def set_run_meta(self, rid: int, **meta: Any) -> None:
        assert set(meta) <= set(RUN_META), meta
        if "modes" in meta:
            meta["modes_json"] = _dumps(meta.pop("modes"))
        sets = ", ".join(f"{k} = ?" for k in meta)
        self._write(f"UPDATE runs SET {sets} WHERE id = ?", (*meta.values(), rid))

    def runs_of_thread(self, tid: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self._all("SELECT id, status, profile, steps_json, error FROM runs "
                                           "WHERE thread_id = ? ORDER BY id", (tid,))]

    # plans ---------------------------------------------------------------------------------------------------

    def add_plan(self, did: int, run_id: int | None, graph_version: int | None, actions: list[dict[str, Any]],
                 summary: str, outcome: dict[str, Any] | None, skill: float | None) -> dict[str, Any]:
        with self._lock, self._db:
            [version] = self._db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM plans WHERE disruption_id = ?",
                                         (did,)).fetchone()
            pid = self._db.execute("INSERT INTO plans (disruption_id, version, run_id, graph_version, actions_json, "
                                   "summary, outcome_json, skill, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (did, version, run_id, graph_version, _dumps(actions), summary,
                                    _dumps(outcome) if outcome is not None else None, skill, now())).lastrowid
        return self.plan(pid)

    def plan(self, pid: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM plans WHERE id = ?", (pid,))
        return _plan(row) if row else None

    def plans(self, did: int) -> list[dict[str, Any]]:
        return [_plan(r) for r in self._all("SELECT * FROM plans WHERE disruption_id = ? ORDER BY version", (did,))]

    def current_plan(self, did: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM plans WHERE disruption_id = ? ORDER BY version DESC LIMIT 1", (did,))
        return _plan(row) if row else None

    # events --------------------------------------------------------------------------------------------------

    def add_event(self, type_: str, payload: dict[str, Any], source: str, thread_id: int | None) -> int:
        return self._write("INSERT INTO events (type, payload_json, source, thread_id, created_at) VALUES (?, ?, ?, ?, ?)",
                           (type_, _dumps(payload), source, thread_id, now()))

    def set_event_run(self, eid: int, run_id: int) -> None:
        self._write("UPDATE events SET run_id = ? WHERE id = ?", (run_id, eid))

    def event(self, eid: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM events WHERE id = ?", (eid,))
        return _event(row) if row else None

    def events(self, thread_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if thread_id is None:
            rows = self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self._all("SELECT * FROM events WHERE thread_id = ? ORDER BY id DESC LIMIT ?", (thread_id, limit))
        return [_event(r) for r in rows]

    # edit blocks ---------------------------------------------------------------------------------------------

    def add_edit_block(self, tid: int, edits: list[dict[str, Any]], before: list[Any],
                       preview: dict[str, Any] | None, reason: str = "") -> int:
        return self._write("INSERT INTO edit_blocks (thread_id, edits_json, before_json, preview_json, reason, "
                           "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (tid, _dumps(edits), _dumps(before), _dumps(preview) if preview is not None else None,
                            reason, now()))

    def edit_block(self, eid: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM edit_blocks WHERE id = ?", (eid,))
        return _edit_block(row) if row else None

    def decide_edit_block(self, eid: int, status: str, who: str | None, journal_id: int | None = None) -> None:
        assert status in ("applied", "dismissed", "stale"), status
        self._write("UPDATE edit_blocks SET status = ?, decided_by = ?, decided_at = ?, journal_id = ? WHERE id = ?",
                    (status, who, now(), journal_id, eid))


def _message(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["blocks"] = json.loads(out.pop("blocks_json"))
    return out


def _plan(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["actions"] = json.loads(out.pop("actions_json"))
    out["outcome"] = json.loads(out.pop("outcome_json")) if row["outcome_json"] else None
    return out


def _event(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["payload"] = json.loads(out.pop("payload_json"))
    return out


def _edit_block(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["edits"] = json.loads(out.pop("edits_json"))
    out["before"] = json.loads(out.pop("before_json"))
    out["preview"] = json.loads(out.pop("preview_json")) if row["preview_json"] else None
    return out
