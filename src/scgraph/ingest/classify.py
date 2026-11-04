"""Region classification: one model call per workbook for the sheets no stored spec covers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from scgraph.graph.schema import SCHEMA
from scgraph.ingest.profile import Profiler
from scgraph.ingest.prompts import load_prompt
from scgraph.ingest.step import StepResult, call_tool
from scgraph.llm import LLMClient

KINDS = ("data", "parameters", "derived_report", "instructions", "ignore")
FACT_KINDS = ("data", "parameters")
FLAG_TARGET = "RiskIndicator.flag"


@dataclass
class SemiStructured:
    column: str  # header text
    targets: list[str]


@dataclass
class RegionPlan:
    id: str
    sheet: str | None
    kind: str
    start_row: int
    header_row: int | None = None
    end_row: int | None = None
    stop_at_blank: str | None = None
    describes: str | None = None
    domains: list[str] = field(default_factory=list)
    semi_structured: list[SemiStructured] = field(default_factory=list)
    reason: str = ""


def valid_target(target: str) -> bool:
    if target == FLAG_TARGET:
        return True
    type_name, _, field_name = target.partition(".")
    owner = SCHEMA.entities.get(type_name) or SCHEMA.relations.get(type_name)
    return owner is not None and owner.field(field_name) is not None


SUBMIT_REGIONS = {
    "name": "submit_regions",
    "description": "Submit every table region of every sheet with its kind and what it describes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "regions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "short snake_case id, unique in the workbook"},
                        "sheet": {"type": "string"},
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "header_row": {"type": ["integer", "null"]},
                        "start_row": {"type": "integer", "description": "first data row"},
                        "end_row": {"type": ["integer", "null"], "description": "last data row, if the block ends early"},
                        "stop_at_blank": {"type": ["string", "null"],
                                          "description": "header of the key column; reading stops at its first blank"},
                        "describes": {"type": ["string", "null"], "enum": [*SCHEMA.entities, *SCHEMA.relations, None],
                                      "description": "the one schema type each row describes (data regions)"},
                        "domains": {"type": "array", "items": {"type": "string", "enum": SCHEMA.domains}},
                        "semi_structured": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"column": {"type": "string", "description": "header text"},
                                               "targets": {"type": "array", "items": {"type": "string"}}},
                                "required": ["column", "targets"],
                            },
                        },
                        "reason": {"type": "string", "description": "one short sentence"},
                    },
                    "required": ["id", "sheet", "kind", "start_row", "reason"],
                },
            }
        },
        "required": ["regions"],
    },
}


def classify(client: LLMClient, profiler: Profiler, sheets: list[str | None]) -> StepResult[list[RegionPlan]]:
    file_kind = profiler.table.kind
    user = {
        "file": profiler.path.name,
        "kind": file_kind,
        "sheets": [_sheet_view(profiler, s) for s in sheets],
    }
    return call_tool(client, system=load_prompt("table_classify"), user=user, tool=SUBMIT_REGIONS,
                     parse=lambda data: parse_regions(data, sheets, file_kind))


HINT_SAMPLE_CHARS = 160
HINT_SAMPLES = 3


def _varied_samples(values: list[str]) -> list[str]:
    """Up to 3 values with different shapes (the `key:` labels of their items), most informative first."""
    by_shape: dict[tuple[str, ...], str] = {}
    for v in values:
        shape = tuple(item.split(":", 1)[0].strip() for item in re.split(r"[|;]", v) if ":" in item)
        by_shape.setdefault(shape, v)
    ranked = sorted(by_shape.items(), key=lambda kv: -len(kv[0]))
    return [v[:HINT_SAMPLE_CHARS] for _, v in ranked[:HINT_SAMPLES]]


def _sheet_view(profiler: Profiler, sheet: str | None) -> dict[str, Any]:
    p = profiler.sheet(sheet)
    view = {"sheet": sheet if sheet is not None else profiler.path.stem, "last_row": p.rows, "columns": p.columns,
            "formula_cells": p.formula_cells, "formula_columns": p.formula_columns, "preview": p.preview}
    if p.rows > len(p.preview):
        view["column_hints"] = _column_hints(profiler, sheet)
    return view


def _column_hints(profiler: Profiler, sheet: str | None) -> list[dict[str, Any]]:
    """Whole-column statistics under the first row, for columns the preview rows leave empty or cut short."""
    grid = profiler.table._grid(sheet)
    first = next((i for i, r in enumerate(grid, 1) if any(v not in (None, "") for v in r)), 1)
    _, columns = profiler.region(sheet, header_row=first, start=first + 1)
    hints = []
    for c in columns:
        if c.fill_rate == 0:
            continue
        hint: dict[str, Any] = {"column": c.column, "header": c.header, "type": c.type, "fill_rate": round(c.fill_rate, 2)}
        if c.delimiter_score:
            hint["delimiter_score"] = c.delimiter_score
        if c.type == "long_text" or c.delimiter_score:
            values = [str(r.cells.get(c.column)) for r in profiler.table.region(sheet, header_row=first,
                                                                                start=first + 1).rows
                      if r.cells.get(c.column) not in (None, "")]
            hint["samples"] = _varied_samples(values)
        hints.append(hint)
    return hints


def parse_regions(data: dict[str, Any], sheets: list[str | None], file_kind: str = "xlsx") -> list[RegionPlan]:
    names = {s for s in sheets if s is not None}
    plans, errors, seen = [], [], set()
    for i, r in enumerate(data["regions"]):
        where = f"regions[{i}]"
        sheet = r.get("sheet")
        if file_kind == "csv":
            sheet = None
        elif sheet not in names:
            errors.append(f"{where}: unknown sheet {sheet!r}")
            continue
        rid = re.sub(r"[^a-z0-9_]+", "_", str(r.get("id", "")).casefold()).strip("_") or f"region{i}"
        if rid in seen:
            errors.append(f"{where}: duplicate id {rid!r}")
        seen.add(rid)
        kind = r.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: kind must be one of {KINDS}")
            continue
        describes = r.get("describes") or None
        if kind == "data" and describes not in (*SCHEMA.entities, *SCHEMA.relations):
            errors.append(f"{where}: data region must describe a schema type, got {describes!r}")
        domains = [d for d in r.get("domains") or [] if d in SCHEMA.domains]
        if kind in FACT_KINDS and not domains:
            domains = list(SCHEMA.domains)
        semi = []
        for s in r.get("semi_structured") or []:
            bad = [t for t in s["targets"] if not valid_target(t)]
            if bad:
                errors.append(f"{where}: semi-structured column {s['column']!r} has unknown targets {bad}")
            semi.append(SemiStructured(s["column"], list(s["targets"])))
        plans.append(RegionPlan(
            id=rid, sheet=sheet, kind=kind, start_row=int(r["start_row"]),
            header_row=r.get("header_row"), end_row=r.get("end_row"), stop_at_blank=r.get("stop_at_blank") or None,
            describes=describes, domains=domains, semi_structured=semi, reason=r.get("reason", ""),
        ))
    missing = sorted(names - {p.sheet for p in plans})
    if missing:
        errors.append(f"sheets without any region: {missing}")
    if errors:
        raise ValueError("; ".join(errors))
    return plans
