"""Layout signatures: reuse a stored generated spec when a region's headers and column types match."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scgraph.ingest.profile import ColumnProfile, Profiler
from scgraph.mapping.spec import MappingSpec, parse_spec
from scgraph.mapping.tables import TableError

TYPE_GROUPS = {
    "int": "number", "float": "number", "percent": "number",
    "text": "text", "long_text": "text", "id": "text",
    "bool": "bool", "date": "date",
}


def normalise_header(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip().casefold().rstrip("*").strip()


def compatible(stored: str, found: str) -> bool:
    """Empty columns fit anything; otherwise the type groups must agree."""
    if "empty" in (stored, found):
        return True
    return TYPE_GROUPS.get(stored, stored) == TYPE_GROUPS.get(found, found)


def layout_of(columns: list[ColumnProfile]) -> dict[str, Any]:
    return {"headers": [normalise_header(c.header) for c in columns], "types": [c.type for c in columns]}


@dataclass
class StoredSpec:
    path: Path
    data: dict[str, Any]

    @property
    def layout(self) -> dict[str, Any]:
        return self.data.get("layout", {})

    @property
    def sheet(self) -> str | None:
        return self.layout.get("sheet")

    def spec(self) -> MappingSpec:
        return parse_spec(self.data)


def load_stored(directory: Path) -> list[StoredSpec]:
    if not directory.is_dir():
        return []
    return [StoredSpec(p, json.loads(p.read_text())) for p in sorted(directory.glob("*.json"))]


def matches(stored: StoredSpec, profiler: Profiler, sheet: str | None) -> bool:
    """Does `sheet` of the profiled file have the stored spec's layout?"""
    layout = stored.layout
    if not layout:
        return False
    if layout.get("kind") in ("ignore", "parameters"):
        if profiler.table.kind != "xlsx" or sheet != stored.sheet:
            return False
        if layout["kind"] == "ignore":
            return _first_row(profiler, sheet) == layout.get("first_row")
        return all(normalise_header(str(profiler.table.cell(sheet, cell) or "")) == normalise_header(label)
                   for cell, label in layout.get("labels", {}).items())
    [region] = stored.data["regions"]
    try:
        _, columns = profiler.region(sheet, header_row=region.get("header_row"),
                                     start=region.get("rows", {}).get("start", 2),
                                     end=region.get("rows", {}).get("end"),
                                     stop_at_blank=region.get("rows", {}).get("stop_at_blank"))
    except TableError:
        return False
    found = layout_of(columns)
    if found["headers"] != layout["headers"]:
        return False
    return all(compatible(s, f) for s, f in zip(layout["types"], found["types"], strict=True))


def _first_row(profiler: Profiler, sheet: str | None) -> list[str]:
    preview = profiler.sheet(sheet).preview
    return [normalise_header(c.split("=", 1)[1]) for c in preview[0]] if preview else []


def ignore_layout(profiler: Profiler, sheet: str) -> dict[str, Any]:
    return {"kind": "ignore", "sheet": sheet, "first_row": _first_row(profiler, sheet)}


def find_reusable(stored: list[StoredSpec], profiler: Profiler, sheet: str | None) -> list[StoredSpec] | None:
    """Stored specs covering `sheet`, if every one of them matches; None when the sheet needs the model.

    Specs are grouped by the file they were generated from (`<stem>__...`). For an xlsx sheet the group's
    specs for that sheet name must all match. A csv matches any single stored region with the same layout.
    """
    if profiler.table.kind == "csv":
        for s in stored:
            if s.layout.get("kind") == "region" and matches(s, profiler, None):
                return [s]
        return None
    groups: dict[str, list[StoredSpec]] = {}
    for s in stored:
        if s.sheet == sheet:
            groups.setdefault(s.path.name.split("__", 1)[0], []).append(s)
    for _, specs in sorted(groups.items()):
        if all(matches(s, profiler, sheet) for s in specs):
            return specs
    return None
