"""Edits, evidence files, the journal and undo."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from scgraph.api.app import AppState, author, state
from scgraph.api.models import EditResult, EditsBody, EvidenceOut, JournalEntry, UndoResult, journal_entry
from scgraph.editing.edits import EditBatchError
from scgraph.editing.evidence import MAX_BYTES, EvidenceError
from scgraph.editing.journal import UndoError, undo
from scgraph.editing.service import apply_edits

router = APIRouter(tags=["editing"])


@router.post("/edits", response_model=EditResult)
def edits(body: EditsBody, st: AppState = Depends(state), who: str = Depends(author)) -> dict[str, Any]:
    """Apply edits all or none; a validation error names every failing edit."""
    evidence = {k: v for k, v in (("id", body.evidence_id), ("comment", body.comment)) if v is not None}
    try:
        result = apply_edits(st.store, body.edits, who, evidence=evidence, evidence_store=st.evidence)
    except EditBatchError as e:
        raise HTTPException(422, {"message": "no edit applied",
                                  "errors": [{"index": i, "message": m} for i, m in e.errors]}) from None
    return {"journal": journal_entry(st.store.journal_entry(result.journal_id)), "edits": result.edits,
            "proposals_created": result.report.proposals_created if result.report else []}


@router.post("/evidence", response_model=EvidenceOut)
async def add_evidence(file: UploadFile = File(...), st: AppState = Depends(state)) -> dict[str, Any]:
    data = await file.read(MAX_BYTES + 1)
    try:
        evidence_id = st.evidence.add(file.filename or "evidence", data)
    except EvidenceError as e:
        raise HTTPException(413, str(e)) from None
    return {"id": evidence_id, "name": st.evidence.name(evidence_id), "bytes": len(data)}


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, st: AppState = Depends(state)) -> FileResponse:
    path = st.evidence.path(evidence_id)
    if path is None:
        raise HTTPException(404, f"no evidence file {evidence_id!r}")
    return FileResponse(path, filename=path.name)


@router.get("/journal", response_model=list[JournalEntry])
def journal(before: int | None = None, limit: int = Query(50, ge=1, le=500),
            st: AppState = Depends(state)) -> list[dict[str, Any]]:
    return st.store.journal(before=before, limit=limit)


@router.post("/journal/{entry_id}/undo", response_model=UndoResult)
def undo_entry(entry_id: int, st: AppState = Depends(state), who: str = Depends(author)) -> dict[str, Any]:
    try:
        undo_id = undo(st.store, entry_id, who)
    except UndoError as e:
        if e.later is not None:
            raise HTTPException(409, {"message": str(e), "later": e.later}) from None
        raise HTTPException(404 if str(e).startswith("no journal entry") else 422, str(e)) from None
    return {"journal": journal_entry(st.store.journal_entry(undo_id))}
