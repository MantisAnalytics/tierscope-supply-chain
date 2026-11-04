"""Semi-structured text cells -> fields: a model-written declarative parser, model fallback for leftovers.

Model output is data (separators, key maps, regexes), never code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.schema import FLAG_CATEGORY, SCHEMA, key_id
from scgraph.ingest.classify import FLAG_TARGET, valid_target
from scgraph.ingest.prompts import load_prompt
from scgraph.ingest.step import StepResult, call_tool
from scgraph.llm import LLMClient, Response

MAX_CELL = 2000
SAMPLE_CELLS = 20
BATCH = 20
MIN_SAMPLE_COVERAGE = 0.5


@dataclass
class Parser:
    kind: str  # kv | regex
    item_sep: str = "|"
    kv_sep: str = ":"
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)  # key text -> {target, list_sep?}
    ignore_keys: list[str] = field(default_factory=list)
    patterns: list[dict[str, Any]] = field(default_factory=list)  # {pattern, targets, required}

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "kv":
            return {"kind": "kv", "item_sep": self.item_sep, "kv_sep": self.kv_sep, "keys": self.keys,
                    "ignore_keys": self.ignore_keys}
        return {"kind": "regex", "patterns": self.patterns}

    @property
    def target_specs(self) -> list[dict[str, Any]]:
        if self.kind == "kv":
            return list(self.keys.values())
        return [t for p in self.patterns for t in p["targets"].values()]

    @property
    def targets(self) -> set[str]:
        return {t["target"] for t in self.target_specs}


def parse_parser(data: dict[str, Any]) -> Parser:
    kind = data.get("kind")
    if kind == "kv":
        keys = {}
        for key, spec in (data.get("keys") or {}).items():
            spec = {"target": spec} if isinstance(spec, str) else dict(spec)
            if not valid_target(spec.get("target", "")):
                raise ValueError(f"key {key!r}: unknown target {spec.get('target')!r}")
            keys[key.strip()] = spec
        if not keys:
            raise ValueError("kv parser needs at least one key")
        if not data.get("item_sep") or not data.get("kv_sep"):
            raise ValueError("kv parser needs item_sep and kv_sep")
        parser = Parser("kv", data["item_sep"], data["kv_sep"], keys,
                        [k.strip() for k in data.get("ignore_keys") or []])
    elif kind == "regex":
        patterns = []
        for p in data.get("patterns") or []:
            try:
                compiled = re.compile(p["pattern"])
            except re.error as e:
                raise ValueError(f"pattern {p.get('pattern')!r}: {e}") from None
            targets = {}
            for group, spec in p["targets"].items():
                spec = {"target": spec} if isinstance(spec, str) else dict(spec)
                if group not in compiled.groupindex:
                    raise ValueError(f"pattern {p['pattern']!r} has no group {group!r}")
                if not valid_target(spec.get("target", "")):
                    raise ValueError(f"unknown target {spec.get('target')!r}")
                targets[group] = spec
            patterns.append({"pattern": p["pattern"], "targets": targets, "required": bool(p.get("required", True))})
        if not patterns:
            raise ValueError("regex parser needs at least one pattern")
        parser = Parser("regex", patterns=patterns)
    else:
        raise ValueError(f"parser kind must be 'kv' or 'regex', got {kind!r}")
    if any(t["target"] == FLAG_TARGET and not t.get("list_sep") for t in parser.target_specs):
        raise ValueError(f"{FLAG_TARGET} holds a list: give its list_sep")
    return parser


def apply_parser(parser: Parser, text: str) -> tuple[dict[str, list[str]], bool]:
    """Target -> values found, and whether the cell was parsed completely (nothing left unrecognised)."""
    text = text[:MAX_CELL]
    values: dict[str, list[str]] = {}
    if parser.kind == "kv":
        complete = True
        for item in (i.strip() for i in text.split(parser.item_sep)):
            if not item:
                continue
            key, sep, value = item.partition(parser.kv_sep)
            key, value = key.strip(), value.strip()
            if not sep:
                complete = False
            elif key in parser.keys:
                spec = parser.keys[key]
                parts = [v.strip() for v in value.split(spec["list_sep"])] if spec.get("list_sep") else [value]
                values.setdefault(spec["target"], []).extend(p for p in parts if p)
            elif key not in parser.ignore_keys:
                complete = False
        return values, complete and bool(values)
    matched_any, complete = False, True
    for p in parser.patterns:
        m = re.search(p["pattern"], text)
        if m is None:
            complete = complete and not p["required"]
            continue
        matched_any = True
        for group, spec in p["targets"].items():
            if m.group(group):
                found = m.group(group)
                parts = found.split(spec["list_sep"]) if spec.get("list_sep") else [found]
                values.setdefault(spec["target"], []).extend(v.strip() for v in parts if v.strip())
    return values, complete and matched_any


@dataclass
class Cell:
    region: str
    row: int
    text: str
    source: SourceLocation
    keys: dict[str, tuple]  # what the row produced: type -> key
    origin: dict[str, Any] | None = None  # the text column's spec origin; parsed values group with their column


def facts_for(cell: Cell, target: str, value: Any, *, method: str, confidence: float, run_id: str) -> list[Fact]:
    """Facts for one parsed value, attached to the entity or relation the cell's row produced."""
    common = {"method": method, "confidence": confidence, "run_id": run_id, "origin": cell.origin}
    if target == FLAG_TARGET:
        supplier = cell.keys.get("Supplier")
        if supplier is None:
            return []
        key = (supplier[0], FLAG_CATEGORY, str(value))
        return [
            Fact("entity", "RiskIndicator", key, cell.source, **common),
            Fact("field", "RiskIndicator", key, cell.source, field="value", value=True, **common),
            Fact("relation", "HAS_RISK", (supplier[0], key_id(key)), cell.source, **common),
        ]
    type_name, _, field_name = target.partition(".")
    key = cell.keys.get(type_name)
    if key is None:
        return []
    fdef = SCHEMA.field_def(type_name, field_name)
    if fdef.type != "str":
        return []  # parsed text only fills text fields
    return [Fact("field", type_name, key, cell.source, field=field_name, value=str(value), **common)]


@dataclass
class ColumnReport:
    region: str
    column: str
    cells: int = 0
    parsed: int = 0
    llm_loaded: int = 0
    rejected_quotes: int = 0
    unparsed: list[str] = field(default_factory=list)  # source labels

    @property
    def coverage(self) -> float:
        return self.parsed / self.cells if self.cells else 1.0


SUBMIT_PARSER = {
    "name": "submit_parser",
    "description": "Submit a declarative parser (kv or regex) for the column.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["kv", "regex"]},
            "item_sep": {"type": "string"},
            "kv_sep": {"type": "string"},
            "keys": {"type": "object", "description": "key text -> Type.field or {target, list_sep}"},
            "ignore_keys": {"type": "array", "items": {"type": "string"}},
            "patterns": {"type": "array", "items": {"type": "object"},
                         "description": "{pattern, targets: {group: Type.field}, required}"},
        },
        "required": ["kind"],
    },
}

SUBMIT_FIELDS = {
    "name": "submit_fields",
    "description": "Submit the fields found in each cell, each with a verbatim quote.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cells": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"target": {"type": "string"}, "value": {"type": "string"},
                                               "quote": {"type": "string"}, "confidence": {"type": "number"}},
                                "required": ["target", "value", "quote", "confidence"],
                            },
                        },
                    },
                    "required": ["id", "fields"],
                },
            }
        },
        "required": ["cells"],
    },
}


def generate_parser(client: LLMClient, column: str, targets: list[str], texts: list[str]) -> StepResult[Parser]:
    samples = list(dict.fromkeys(texts))[:SAMPLE_CELLS]

    def parse(data: dict[str, Any]) -> Parser:
        parser = parse_parser(data)
        if not parser.targets <= set(targets):
            raise ValueError(f"use only the targets {targets}; got {sorted(parser.targets - set(targets))}")
        covered = sum(apply_parser(parser, t)[1] for t in samples) / len(samples)
        if covered < MIN_SAMPLE_COVERAGE:
            raise ValueError(f"the parser fully parses only {covered:.0%} of the sample cells")
        return parser

    user = {"column": column, "targets": targets, "samples": samples}
    return call_tool(client, system=load_prompt("field_parser"), user=user, tool=SUBMIT_PARSER, parse=parse)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def extract_leftovers(client: LLMClient, cells: list[Cell], targets: list[str], *, run_id: str,
                      report: ColumnReport) -> tuple[list[Fact], list[Response]]:
    """Model fallback in batches; a value is kept only if its quote is in the cell and contains the value."""
    facts: list[Fact] = []
    responses: list[Response] = []
    for start in range(0, len(cells), BATCH):
        batch = cells[start:start + BATCH]

        def parse(data: dict[str, Any], size: int = len(batch)) -> list[dict[str, Any]]:
            out = []
            for c in data["cells"]:
                if not 0 <= int(c["id"]) < size:
                    raise ValueError(f"unknown cell id {c['id']}")
                for f in c["fields"]:
                    if not valid_target(f["target"]):
                        raise ValueError(f"unknown target {f['target']!r}")
                out.append(c)
            return out

        user = {"targets": targets, "cells": [{"id": i, "text": c.text[:MAX_CELL]} for i, c in enumerate(batch)]}
        step = call_tool(client, system=load_prompt("field_extract"), user=user, tool=SUBMIT_FIELDS, parse=parse)
        responses += step.responses
        loaded: set[int] = set()
        for entry in step.value:
            cell = batch[int(entry["id"])]
            for f in entry["fields"]:
                quote, value = _norm(f["quote"]), _norm(str(f["value"]))
                if not quote or quote not in _norm(cell.text) or value not in quote:
                    report.rejected_quotes += 1
                    continue
                confidence = max(0.0, min(1.0, float(f["confidence"])))
                new = facts_for(cell, f["target"], str(f["value"]).strip(), method="llm", confidence=confidence,
                                run_id=run_id)
                if new:
                    facts += new
                    loaded.add(int(entry["id"]))
        for i, cell in enumerate(batch):
            if i in loaded:
                report.llm_loaded += 1
            else:
                report.unparsed.append(cell.source.label())
    return facts, responses


def parse_column(parser: Parser, cells: list[Cell], *, column_confidence: float, run_id: str,
                 report: ColumnReport) -> tuple[list[Fact], list[Cell]]:
    """Apply the parser to every cell: facts from fully parsed cells (method `parser`), and the leftovers."""
    parsed, leftovers = [], []
    for cell in cells:
        values, complete = apply_parser(parser, cell.text)
        (parsed if complete else leftovers).append((cell, values))
    report.cells, report.parsed = len(cells), len(parsed)
    confidence = round(min(column_confidence, report.coverage), 4)
    facts = []
    for cell, values in parsed:
        for target, items in values.items():
            for value in items:
                facts += facts_for(cell, target, value, method="parser", confidence=confidence, run_id=run_id)
    return facts, [c for c, _ in leftovers]
