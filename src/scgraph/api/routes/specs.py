"""Stored mapping specs: list, show, approve."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from scgraph.api.app import AppState, author, state
from scgraph.api.models import SpecDetail, SpecSummary
from scgraph.ingest.spec_review import approve
from scgraph.mapping.spec import SpecError

router = APIRouter(tags=["specs"])


def _columns(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for region in spec.get("regions", [])
            for c in (*region.get("key_columns", {}).values(), *region.get("columns", {}).values())]


def summary(path: Path, spec: dict[str, Any], threshold: float) -> dict[str, Any]:
    meta = spec.get("metadata", {})
    roles: dict[str, int] = {}
    below = 0
    for c in _columns(spec):
        roles[c.get("role", "?")] = roles.get(c.get("role", "?"), 0) + 1
        decided = "approved_by" in c or "rejected_by" in c
        below += c.get("role") != "ignore" and c.get("confidence", 1.0) < threshold and not decided
    return {"name": path.stem, "file": path.name, "status": meta.get("status", "?"),
            "source_file": meta.get("source_file"), "author": meta.get("author"), "model": meta.get("model"),
            "describes": meta.get("describes"), "region_kind": meta.get("region_kind"),
            "sheets": sorted({r.get("sheet") for r in spec.get("regions", []) if r.get("sheet")}),
            "approved_by": meta.get("approved_by"), "approved_at": meta.get("approved_at"),
            "columns_by_role": dict(sorted(roles.items())), "below_threshold": below}


def _path(st: AppState, name: str) -> Path:
    """Only files listed in the specs directory; a name never becomes a path of its own."""
    found = {p.stem: p for p in st.specs_dir.glob("*.json")}
    if name not in found:
        raise HTTPException(404, f"no mapping spec {name!r} in {st.specs_dir}")
    return found[name]


@router.get("/specs", response_model=list[SpecSummary])
def specs(st: AppState = Depends(state)) -> list[dict[str, Any]]:
    out = []
    for path in sorted(st.specs_dir.glob("*.json")):
        try:
            out.append(summary(path, json.loads(path.read_text()), st.store.threshold))
        except (OSError, ValueError):
            continue  # not a spec
    return out


@router.get("/specs/{name}", response_model=SpecDetail)
def spec(name: str, st: AppState = Depends(state)) -> dict[str, Any]:
    """The spec in full, with its summary."""
    path = _path(st, name)
    data = json.loads(path.read_text())
    return {"summary": summary(path, data, st.store.threshold), "spec": data}


@router.post("/specs/{name}/approve", response_model=SpecDetail)
def approve_spec(name: str, st: AppState = Depends(state), who: str = Depends(author)) -> dict[str, Any]:
    path = _path(st, name)
    try:
        data = approve(path, who)
    except SpecError as e:
        raise HTTPException(422, f"spec does not validate: {e}") from None
    return {"summary": summary(path, data, st.store.threshold), "spec": data}
