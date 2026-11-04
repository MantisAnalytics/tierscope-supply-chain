"""Human review of generated specs: show, approve, and per-column decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scgraph.mapping.spec import parse_spec


def approve(path: Path, author: str | None = None) -> dict[str, Any]:
    """Status `approved`; every column, key and parameter confidence becomes 1.0 for later executions."""
    spec = json.loads(path.read_text())
    spec.setdefault("metadata", {})
    spec["metadata"]["status"] = "approved"
    spec["metadata"]["approved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    if author:
        spec["metadata"]["approved_by"] = author
    for region in spec.get("regions", []):
        region["confidence"] = 1.0
        for column in (*region.get("columns", {}).values(), *region.get("key_columns", {}).values()):
            column["confidence"] = 1.0
    for p in spec.get("parameters", []):
        p["confidence"] = 1.0
    parse_spec(spec)
    path.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
    return spec


def decide_mapping(path: Path, region_id: str, column: str | None, *, accept: bool, author: str,
                   reason: str | None = None, threshold: float = 0.9) -> dict[str, Any]:
    """Record a person's decision on one spec column (or, with `column` None, a region's keys).

    Accept: confidence 1.0 and an approval record. Reject: the column's role becomes `ignore` with the reason, so
    later ingests skip it. Either way the spec becomes `approved` once no column below the threshold waits for a
    decision.
    """
    spec = json.loads(path.read_text())
    region = next((r for r in spec.get("regions", []) if r.get("id") == region_id), None)
    if region is None:
        raise ValueError(f"{path.name}: no region {region_id!r}")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if column is None:
        targets = list(region.get("key_columns", {}).values())
    elif column in region.get("columns", {}):
        targets = [region["columns"][column]]
    else:
        raise ValueError(f"{path.name}: region {region_id} has no column {column!r}")
    if accept:
        if column is None:
            region["confidence"] = 1.0
            region["approved_by"], region["approved_at"] = author, now
        for c in targets:
            c.update(confidence=1.0, approved_by=author, approved_at=now)
    elif column is None:
        region.update(rejected_by=author, rejected_at=now, reason=reason)
    else:
        targets[0].update(role="ignore", rejected_by=author, rejected_at=now, reason=reason)
    if not draft_columns(spec, threshold):
        spec.setdefault("metadata", {}).update(status="approved", approved_at=now, approved_by=author)
    parse_spec(spec)
    path.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
    return spec


def draft_columns(spec: dict[str, Any], threshold: float) -> list[str]:
    """Columns (and region keys) below the threshold that no person decided on yet."""
    out = []
    def decided(item: dict[str, Any]) -> bool:
        return "approved_by" in item or "rejected_by" in item

    for region in spec.get("regions", []):
        if region.get("confidence", 1.0) < threshold and not decided(region):
            out.append(f"{region['id']}: keys")
        for header, c in (*region.get("key_columns", {}).items(), *region.get("columns", {}).items()):
            if c.get("role") in ("fact", "key") and c.get("confidence", 1.0) < threshold and not decided(c):
                out.append(f"{region['id']}: {header}")
    return out


def describe(spec: dict[str, Any]) -> list[str]:
    meta = spec.get("metadata", {})
    lines = [f"status {meta.get('status', '?')}, author {meta.get('author', '?')}"
             + (f" ({meta['model']})" if meta.get("model") else "") + f", from {meta.get('source_file', '?')}"]
    for sheet, reason in spec.get("ignore_sheets", {}).items():
        lines.append(f"sheet {sheet}: ignored ({reason})")
    for region in spec.get("regions", []):
        rows = region.get("rows", {})
        lines.append(f"region {region['id']} on {region['sheet']!r}, header row {region.get('header_row')}, rows {rows}"
                     f", confidence {region.get('confidence', 1.0):g}")
        for p in region["produces"]:
            lines.append(f"  produces {json.dumps(p, ensure_ascii=False)}")
        for header, c in (*region.get("key_columns", {}).items(), *region["columns"].items()):
            what = c.get("target") or c.get("metric") or ""
            model = f" (model {c['model_confidence']:g})" if "model_confidence" in c else ""
            lines.append(f"  {header!r}: {c['role']} {what}  conf {c.get('confidence', 1.0):g}{model}"
                         + (f"  transform {json.dumps(c['transform'])}" if c.get("transform") else "")
                         + (f"\n      {c['rationale']}" if c.get("rationale") else ""))
    for entry in spec.get("semi_structured", []):
        lines.append(f"semi-structured {entry['column']!r} -> {', '.join(entry['targets'])}"
                     + (f", coverage {entry['coverage']:.0%}" if "coverage" in entry else ""))
        if entry.get("parser"):
            lines.append(f"  parser {json.dumps(entry['parser'], ensure_ascii=False)}")
    for p in spec.get("parameters", []):
        label = p.get("label", {})
        lines.append(f"parameter {p['name']} = {p['sheet']}!{p['cell']}"
                     + (f" (label {label.get('cell')} {label.get('equals')!r})" if label else "")
                     + f"  conf {p.get('confidence', 1.0):g}")
    return lines
