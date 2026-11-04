"""SQLite store for operations: disruptions, agent runs, proposed and accepted actions, and follow-up tasks.

Kept apart from the graph database: operations read the graph and never write it. The agent's threads, plan
versions, events and edit blocks live in the same database (`scgraph.agent.store`, as `OpsStore.agent`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scgraph.agent.store import AgentStore, migrate

SCHEMA = """
CREATE TABLE IF NOT EXISTS disruptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'active',          -- draft | active | resolved | archived (discarded)
    data_json TEXT NOT NULL,                       -- the Disruption
    references_json TEXT,                          -- cached reference policies, cleared on edit
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    author TEXT NOT NULL,
    confirmed_at TEXT,                             -- when a draft was confirmed as active
    resolved_at TEXT,
    resolved_by TEXT,
    resolution_note TEXT,
    resolution_json TEXT                           -- the outcome recorded at resolution, with its frozen report
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disruption_id INTEGER,                         -- NULL for turns of a general thread
    job_id TEXT,
    status TEXT NOT NULL,                          -- running | done | failed
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    steps_json TEXT NOT NULL DEFAULT '[]',
    plan_json TEXT,
    summary TEXT,
    outcome_json TEXT,
    skill REAL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disruption_id INTEGER NOT NULL,
    run_id INTEGER,                                -- NULL for actions a person added
    action_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',       -- proposed | accepted | rejected | superseded
    decided_by TEXT,
    decided_at TEXT,
    comment TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id INTEGER NOT NULL UNIQUE,
    disruption_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',           -- open | in_progress | done | cancelled
    owner TEXT,
    due_week INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    updated_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_disruption ON runs (disruption_id);
CREATE INDEX IF NOT EXISTS actions_disruption ON actions (disruption_id);
"""
DISRUPTION_STATUSES = ("draft", "active", "resolved", "archived")
# Columns added to `disruptions` after it was first created; ALTER TABLE ADD COLUMN when missing.
DISRUPTION_COLUMNS = ("confirmed_at TEXT", "resolved_at TEXT", "resolved_by TEXT", "resolution_note TEXT",
                      "resolution_json TEXT")
ACTION_STATUSES = ("proposed", "accepted", "rejected", "superseded")
TASK_STATUSES = ("open", "in_progress", "done", "cancelled")


class OpsError(ValueError):
    pass


class NotFoundError(OpsError):
    pass


class ResolvedError(OpsError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class OpsStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            existing = {row[1] for row in self._db.execute("PRAGMA table_info(disruptions)")}
            with self._db:
                for column in DISRUPTION_COLUMNS:
                    if column.split()[0] not in existing:
                        self._db.execute(f"ALTER TABLE disruptions ADD COLUMN {column}")
            migrate(self._db)
        self.agent = AgentStore(self._db, self._lock)

    def close(self) -> None:
        self._db.close()

    def _one(self, query: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(query, args).fetchone()

    def _all(self, query: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(query, args).fetchall()

    def _write(self, query: str, args: tuple = ()) -> int:
        with self._lock, self._db:
            return self._db.execute(query, args).lastrowid

    # disruptions ---------------------------------------------------------------------------------------------

    def add_disruption(self, data: dict[str, Any], author: str, status: str = "active", thread: bool = True) -> int:
        """A new disruption and, in the same transaction, its thread. A draft the agent wrote in a general thread
        (`thread=False`) gets its thread when a person saves it (`AgentStore.bind_disruption`)."""
        if status not in DISRUPTION_STATUSES[:2]:
            raise OpsError("a new disruption is a draft or active")
        t = now()
        with self._lock, self._db:
            did = self._db.execute("INSERT INTO disruptions (status, data_json, created_at, updated_at, author) "
                                   "VALUES (?, ?, ?, ?, ?)", (status, json.dumps(data, ensure_ascii=False), t, t,
                                                              author)).lastrowid
            if thread:
                self._db.execute("INSERT INTO threads (disruption_id, title, created_at, updated_at) "
                                 "VALUES (?, ?, ?, ?)", (did, data.get("name") or f"Disruption {did}", t, t))
        return did

    def disruption(self, did: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM disruptions WHERE id = ?", (did,))
        if row is None:
            raise NotFoundError(f"no disruption {did}")
        return _disruption(row)

    def disruptions(self, include_archived: bool = False, statuses: tuple[str, ...] | None = None
                    ) -> list[dict[str, Any]]:
        """Drafts and active disruptions by default; `statuses` picks exactly."""
        statuses = statuses or (DISRUPTION_STATUSES if include_archived else ("draft", "active"))
        marks = ", ".join("?" * len(statuses))
        return [_disruption(r) for r in self._all(f"SELECT * FROM disruptions WHERE status IN ({marks}) "
                                                  "ORDER BY id DESC", tuple(statuses))]

    def writable(self, did: int) -> dict[str, Any]:
        """The disruption, refused when it is resolved: a resolved disruption is not edited or planned again."""
        row = self.disruption(did)
        if row["status"] == "resolved":
            raise ResolvedError(f"disruption {did} is resolved")
        return row

    def update_disruption(self, did: int, data: dict[str, Any] | None = None, status: str | None = None) -> None:
        """Change a disruption's data or status (not to or from `resolved`: see `resolve`). Confirming a draft
        records `confirmed_at`, from which its tasks' weeks count."""
        row = self.writable(did)
        if data is not None:
            self._write("UPDATE disruptions SET data_json = ?, references_json = NULL, updated_at = ? WHERE id = ?",
                        (json.dumps(data, ensure_ascii=False), now(), did))
        if status is not None:
            if status not in DISRUPTION_STATUSES or status == "resolved":
                raise OpsError("status must be one of draft, active, archived (resolve a disruption with its note)")
            if status == "active" and row["status"] == "draft":
                self._write("UPDATE disruptions SET confirmed_at = ? WHERE id = ?", (now(), did))
            self._write("UPDATE disruptions SET status = ?, updated_at = ? WHERE id = ?", (status, now(), did))
            self._write("UPDATE threads SET status = ?, updated_at = ? WHERE disruption_id = ?",
                        ("archived" if status == "archived" else "active", now(), did))

    def resolve(self, did: int, author: str, note: str, resolution: dict[str, Any]) -> None:
        """Record the resolution and archive the disruption's thread."""
        t = now()
        with self._lock, self._db:
            self._db.execute("UPDATE disruptions SET status = 'resolved', resolved_at = ?, resolved_by = ?, "
                             "resolution_note = ?, resolution_json = ?, updated_at = ? WHERE id = ?",
                             (t, author, note, json.dumps(resolution, ensure_ascii=False), t, did))
            self._db.execute("UPDATE threads SET status = 'archived', needs = NULL, updated_at = ? "
                             "WHERE disruption_id = ?", (t, did))

    def resolution(self, did: int) -> dict[str, Any] | None:
        row = self._one("SELECT resolution_json FROM disruptions WHERE id = ?", (did,))
        return json.loads(row[0]) if row and row[0] else None

    def set_references(self, did: int, refs: dict[str, Any] | None) -> None:
        self._write("UPDATE disruptions SET references_json = ? WHERE id = ? AND status != 'resolved'",
                    (json.dumps(refs) if refs is not None else None, did))

    def delete_disruption(self, did: int) -> None:
        self.disruption(did)
        with self._lock, self._db:
            for row in self._db.execute("SELECT id FROM threads WHERE disruption_id = ?", (did,)).fetchall():
                for table in ("messages", "events", "edit_blocks"):
                    self._db.execute(f"DELETE FROM {table} WHERE thread_id = ?", (row[0],))
            for table in ("tasks", "actions", "runs", "plans", "threads"):
                self._db.execute(f"DELETE FROM {table} WHERE disruption_id = ?", (did,))
            self._db.execute("DELETE FROM disruptions WHERE id = ?", (did,))

    # runs ----------------------------------------------------------------------------------------------------

    def start_run(self, did: int | None, provider: str, model: str, job_id: str | None = None) -> int:
        return self._write("INSERT INTO runs (disruption_id, job_id, status, provider, model, started_at) "
                           "VALUES (?, ?, 'running', ?, ?, ?)", (did, job_id, provider, model, now()))

    def set_run_job(self, rid: int, job_id: str) -> None:
        self._write("UPDATE runs SET job_id = ? WHERE id = ?", (job_id, rid))

    def append_step(self, rid: int, step: dict[str, Any]) -> None:
        with self._lock:
            run = self.run(rid)
            steps = run["steps"] + [step]
            self._write("UPDATE runs SET steps_json = ? WHERE id = ?", (json.dumps(steps, ensure_ascii=False), rid))

    def finish_run(self, rid: int, *, status: str, plan: list[dict[str, Any]] | None = None, summary: str | None = None,
                   outcome: dict[str, Any] | None = None, skill: float | None = None, input_tokens: int = 0,
                   output_tokens: int = 0, cost_usd: float = 0.0, error: str | None = None) -> None:
        self._write("UPDATE runs SET status = ?, finished_at = ?, plan_json = ?, summary = ?, outcome_json = ?, "
                    "skill = ?, input_tokens = ?, output_tokens = ?, cost_usd = ?, error = ? WHERE id = ?",
                    (status, now(), json.dumps(plan) if plan is not None else None, summary,
                     json.dumps(outcome) if outcome is not None else None, skill, input_tokens, output_tokens,
                     cost_usd, error, rid))

    def run(self, rid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM runs WHERE id = ?", (rid,))
        if row is None:
            raise NotFoundError(f"no run {rid}")
        return _run(row)

    def runs(self, did: int | None = None) -> list[dict[str, Any]]:
        if did is None:
            return [_run(r) for r in self._all("SELECT * FROM runs ORDER BY id DESC")]
        return [_run(r) for r in self._all("SELECT * FROM runs WHERE disruption_id = ? ORDER BY id DESC", (did,))]

    def fail_unfinished_runs(self) -> None:
        self._write("UPDATE runs SET status = 'failed', error = 'the server stopped during the run', finished_at = ? "
                    "WHERE status = 'running'", (now(),))

    # actions -------------------------------------------------------------------------------------------------

    def propose(self, did: int, run_id: int | None, items: list[tuple[dict[str, Any], str]],
                plan_id: int | None = None) -> list[int]:
        """New proposed actions; still-undecided proposals of earlier runs are superseded."""
        t = now()
        with self._lock, self._db:
            self._db.execute("UPDATE actions SET status = 'superseded' WHERE disruption_id = ? AND status = 'proposed'",
                             (did,))
            return [self._db.execute("INSERT INTO actions (disruption_id, run_id, plan_id, action_json, reason, "
                                     "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                     (did, run_id, plan_id, json.dumps(a), reason, t)).lastrowid
                    for a, reason in items]

    def add_manual(self, did: int, action: dict[str, Any], reason: str, author: str) -> int:
        t = now()
        return self._write("INSERT INTO actions (disruption_id, run_id, action_json, reason, status, decided_by, "
                           "decided_at, created_at) VALUES (?, NULL, ?, ?, 'accepted', ?, ?, ?)",
                           (did, json.dumps(action), reason, author, t, t))

    def action(self, aid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM actions WHERE id = ?", (aid,))
        if row is None:
            raise NotFoundError(f"no action {aid}")
        return _action(row)

    def actions(self, did: int, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM actions WHERE disruption_id = ? ORDER BY id", (did,))
        out = [_action(r) for r in rows]
        return [a for a in out if statuses is None or a["status"] in statuses]

    def decide(self, aid: int, accept: bool, author: str, comment: str | None = None) -> dict[str, Any]:
        action = self.action(aid)
        if action["status"] != "proposed":
            raise OpsError(f"action {aid} is already {action['status']}")
        self._write("UPDATE actions SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                    ("accepted" if accept else "rejected", author, now(), comment, aid))
        return self.action(aid)

    def withdraw(self, aid: int, author: str, comment: str | None = None) -> None:
        """Take back an accepted action: it is rejected and its task cancelled."""
        action = self.action(aid)
        if action["status"] != "accepted":
            raise OpsError(f"action {aid} is {action['status']}, not accepted")
        self._write("UPDATE actions SET status = 'rejected', decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                    (author, now(), comment, aid))
        self._write("UPDATE tasks SET status = 'cancelled', updated_by = ?, updated_at = ? WHERE action_id = ?",
                    (author, now(), aid))

    # tasks ---------------------------------------------------------------------------------------------------

    def create_task(self, action_id: int, did: int, title: str, owner: str | None, due_week: int | None) -> int:
        t = now()
        return self._write("INSERT INTO tasks (action_id, disruption_id, title, owner, due_week, created_at, "
                           "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (action_id, did, title, owner, due_week, t, t))

    def task(self, tid: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM tasks WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"no task {tid}")
        return dict(row)

    def tasks(self, did: int | None = None, status: str | None = None, owner: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM tasks WHERE 1 = 1", []
        for column, value in (("disruption_id", did), ("status", status), ("owner", owner)):
            if value is not None:
                query += f" AND {column} = ?"
                args.append(value)
        return [dict(r) for r in self._all(query + " ORDER BY id DESC", tuple(args))]

    def update_task(self, tid: int, author: str, **changes: Any) -> dict[str, Any]:
        self.task(tid)
        allowed = {"status", "owner", "due_week", "notes"}
        unknown = set(changes) - allowed
        if unknown:
            raise OpsError(f"cannot change {', '.join(sorted(unknown))}")
        if "status" in changes and changes["status"] not in TASK_STATUSES:
            raise OpsError(f"status must be one of {', '.join(TASK_STATUSES)}")
        if not changes:
            return self.task(tid)
        sets = ", ".join(f"{k} = ?" for k in changes)
        self._write(f"UPDATE tasks SET {sets}, updated_by = ?, updated_at = ? WHERE id = ?",
                    (*changes.values(), author, now(), tid))
        return self.task(tid)


def _disruption(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "status": row["status"], **json.loads(row["data_json"]),
            "references": json.loads(row["references_json"]) if row["references_json"] else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"], "author": row["author"],
            "confirmed_at": row["confirmed_at"], "resolved_at": row["resolved_at"], "resolved_by": row["resolved_by"],
            "resolution_note": row["resolution_note"]}


def _run(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["steps"] = json.loads(out.pop("steps_json"))
    out["plan"] = json.loads(out.pop("plan_json")) if row["plan_json"] else None
    out["outcome"] = json.loads(out.pop("outcome_json")) if row["outcome_json"] else None
    out["modes"] = json.loads(out.pop("modes_json")) if row["modes_json"] else None
    return out


def _action(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["action"] = json.loads(out.pop("action_json"))
    return out
