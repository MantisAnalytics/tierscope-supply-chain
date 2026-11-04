"""Cassettes: committed JSON files of recorded responses that tests replay without network or keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scgraph.llm.errors import CacheMissError
from scgraph.llm.types import Request, Response

MODES = ("replay", "record")
RERECORD_HINT = "SCGRAPH_CASSETTE=record uv run pytest -m cassette"


class Cassette:
    """Responses keyed by `request_key`.

    `replay`: hits come back as cache hits, misses raise. `record`: every request goes live and is added;
    the file is rewritten with only the entries used in this run.
    """

    def __init__(self, path: str | Path, mode: str = "replay") -> None:
        if mode not in MODES:
            raise ValueError(f"cassette mode must be one of {MODES}, got {mode!r}")
        self.path = Path(path)
        self.mode = mode
        self._entries: dict[str, dict[str, Any]] = {}
        if mode == "replay" and self.path.is_file():
            self._entries = json.loads(self.path.read_text())
        self._used: dict[str, dict[str, Any]] = {}

    def recorded(self, key: str) -> Response | None:
        """In record mode, a response already recorded in this run, so identical requests stay consistent."""
        entry = self._used.get(key) if self.mode == "record" else None
        return Response.from_dict(entry["response"]) if entry else None

    def get(self, key: str) -> Response:
        entry = self._entries.get(key)
        if entry is None:
            raise CacheMissError(
                f"cassette {self.path}: no recorded response for request {key[:12]}; re-record with `{RERECORD_HINT}`"
            )
        self._used[key] = entry
        return Response.from_dict(entry["response"])

    def put(self, key: str, provider: str, request: Request, response: Response) -> None:
        entry = {"provider": provider, "request": request.to_dict(), "response": response.to_dict()}
        self._entries[key] = entry
        self._used[key] = entry
        self.save()

    def save(self) -> None:
        if self.mode != "record":
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(dict(sorted(self._used.items())), indent=1, sort_keys=True, ensure_ascii=False)
        self.path.write_text(text + "\n")
