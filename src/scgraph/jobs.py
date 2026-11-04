"""Background jobs: a SQLite record per job and a small thread pool, for work that calls the model for minutes.

A job function gets a `Job` handle and reports progress through it; its return value becomes the job's result.
Jobs left queued or running by a previous process are marked failed at startup.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_kind ON jobs (kind, created_at);
"""
STATUSES = ("queued", "running", "done", "failed")
DEFAULT_JOBS_DB_PATH = ".cache/jobs.sqlite"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def create(self, kind: str, title: str, progress: dict[str, Any] | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._lock, self._db:
            self._db.execute("INSERT INTO jobs VALUES (?, ?, 'queued', ?, ?, ?, ?, NULL, NULL)",
                             (job_id, kind, title, now, now, json.dumps(progress or {})))
        return job_id

    def update(self, job_id: str, *, status: str | None = None, progress: dict[str, Any] | None = None,
               result: Any = None, error: str | None = None) -> None:
        sets, values = ["updated_at = ?"], [_now()]
        if status is not None:
            assert status in STATUSES, status
            sets.append("status = ?")
            values.append(status)
        if progress is not None:
            sets.append("progress_json = ?")
            values.append(json.dumps(progress, ensure_ascii=False, default=str))
        if result is not None:
            sets.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False, default=str))
        if error is not None:
            sets.append("error = ?")
            values.append(error)
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*values, job_id))

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row(row) if row else None

    def list(self, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM jobs", []
        if kind:
            query += " WHERE kind = ?"
            args.append(kind)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, limit)).fetchall()
        return [_row(r) for r in rows]

    def fail_unfinished(self, message: str = "the server stopped before the job finished") -> int:
        with self._lock, self._db:
            cur = self._db.execute("UPDATE jobs SET status = 'failed', error = ?, updated_at = ? "
                                   "WHERE status IN ('queued', 'running')", (message, _now()))
        return cur.rowcount

    def close(self) -> None:
        self._db.close()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "kind": row["kind"], "status": row["status"], "title": row["title"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "progress": json.loads(row["progress_json"]),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": row["error"],
    }


class Job:
    """What a running job function sees: its id and a way to publish progress."""

    def __init__(self, store: JobStore, job_id: str, progress: dict[str, Any]) -> None:
        self.store, self.id, self.progress = store, job_id, progress

    def report(self, **changes: Any) -> None:
        self.progress.update(changes)
        self.store.update(self.id, progress=self.progress)


class JobRunner:
    def __init__(self, store: JobStore, workers: int = 2) -> None:
        self.store = store
        self.store.fail_unfinished()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit(self, kind: str, title: str, fn: Callable[[Job], Any], progress: dict[str, Any] | None = None) -> str:
        progress = dict(progress or {})
        job_id = self.store.create(kind, title, progress)
        self._pool.submit(self._run, Job(self.store, job_id, progress), fn)
        return job_id

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        self.store.update(job.id, status="running")
        try:
            result = fn(job)
        except Exception as e:  # noqa: BLE001 - every failure is recorded on the job
            self.store.update(job.id, status="failed", progress=job.progress,
                              error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
            return
        self.store.update(job.id, status="done", progress=job.progress, result=result if result is not None else {})

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
