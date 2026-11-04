"""Undo of journal entries: restore the rows an entry changed, if nothing later changed them again."""

from __future__ import annotations

import json
from pathlib import Path

from scgraph.graph.store import GraphStore

UNDOABLE = ("accept", "reject", "edit")


class UndoError(ValueError):
    def __init__(self, message: str, later: int | None = None) -> None:
        super().__init__(message)
        self.later = later  # the entry that blocks the undo


def blocking_entry(store: GraphStore, entry_id: int) -> int | None:
    """The first later entry, not undone, that changed a row this entry changed."""
    entry = store.journal_entry(entry_id)
    items = set(entry["items"])
    rows = store._db.execute("SELECT id, items_json FROM journal WHERE id > ? AND undone_by IS NULL"
                             " AND action != 'undo' ORDER BY id", (entry_id,)).fetchall()
    for later_id, later_items in rows:
        if items & {tuple(x) for x in json.loads(later_items)}:
            return later_id
    return None


def undo(store: GraphStore, entry_id: int, author: str) -> int:
    """Undo an accept, reject or edit; an undone accept or reject returns its proposal to pending.

    Returns the id of the `undo` journal entry.
    """
    entry = store.journal_entry(entry_id)
    if entry is None:
        raise UndoError(f"no journal entry #{entry_id}")
    if entry["action"] not in UNDOABLE:
        raise UndoError(f"entry #{entry_id} is an {entry['action']}; only {', '.join(UNDOABLE)} can be undone")
    if entry["undone_by"] is not None:
        raise UndoError(f"entry #{entry_id} was undone already by #{entry['undone_by']}")
    if (later := blocking_entry(store, entry_id)) is not None:
        raise UndoError(f"entry #{entry_id} cannot be undone: entry #{later} changed the same items later", later)
    inverse = entry["inverse"]
    with store.operation(author, "undo", f"undo #{entry_id}: {entry['summary']}") as op:
        for table, rowid, before in reversed(inverse.get("rows", [])):
            store.restore_row(table, rowid, before)
        for path, text in inverse.get("files", {}).items():
            file = Path(path)
            op.files.setdefault(path, file.read_text() if file.is_file() else None)
            if text is None:
                file.unlink(missing_ok=True)
            else:
                file.write_text(text)
        store._db.execute("UPDATE journal SET undone_by = ? WHERE id = ?", (op.journal_id, entry_id))
        return op.journal_id
