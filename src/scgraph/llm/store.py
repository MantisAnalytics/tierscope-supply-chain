"""SQLite store for cached responses and the cost ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scgraph.llm.types import Request, Response

SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    cache_hit INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_provider ON ledger (provider);
CREATE INDEX IF NOT EXISTS ledger_run ON ledger (run_id);
"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(provider: str, request: Request) -> str:
    """SHA-256 of the full request. `request.model` must already be resolved."""
    payload = {"provider": provider, **request.to_dict()}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def get_response(self, key: str) -> Response | None:
        row = self._db.execute("SELECT response_json FROM responses WHERE key = ?", (key,)).fetchone()
        return Response.from_dict(json.loads(row[0])) if row else None

    def put_response(self, key: str, provider: str, request: Request, response: Response) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    provider,
                    request.model,
                    canonical_json(request.to_dict()),
                    canonical_json(response.to_dict()),
                    _now(),
                ),
            )

    def add_ledger(
        self,
        *,
        run_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cache_hit: bool,
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO ledger (ts, run_id, provider, model, input_tokens, output_tokens, cost_usd, cache_hit)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), run_id, provider, model, input_tokens, output_tokens, cost_usd, int(cache_hit)),
            )

    def spend(self, provider: str) -> float:
        row = self._db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE provider = ?", (provider,))
        return float(row.fetchone()[0])

    def spend_of_runs(self, prefix: str) -> float:
        """Spend of the ledger runs whose id starts with `prefix` (e.g. `auto-` for event-started agent turns)."""
        row = self._db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ledger WHERE run_id LIKE ? || '%'", (prefix,))
        return float(row.fetchone()[0])

    def report(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """Totals per provider and model, optionally for one run."""
        where, args = ("WHERE run_id = ?", (run_id,)) if run_id else ("", ())
        rows = self._db.execute(
            "SELECT provider, model, COUNT(*), SUM(cache_hit), SUM(input_tokens), SUM(output_tokens),"
            f" SUM(cost_usd) FROM ledger {where} GROUP BY provider, model ORDER BY provider, model",
            args,
        ).fetchall()
        keys = ("provider", "model", "calls", "cache_hits", "input_tokens", "output_tokens", "cost_usd")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def ledger_rows(self, run_id: str | None = None) -> list[dict[str, Any]]:
        where, args = ("WHERE run_id = ?", (run_id,)) if run_id else ("", ())
        cur = self._db.execute(f"SELECT * FROM ledger {where} ORDER BY id", args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
