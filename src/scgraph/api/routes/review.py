"""Review endpoints: the proposal queue, one proposal with its preview, accept and reject."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from scgraph.api.app import AppState, author, state
from scgraph.api.models import AcceptBody, Decision, Proposal, ProposalDetail, RejectBody, journal_entry
from scgraph.review.preview import by_impact, preview
from scgraph.review.service import DecidedError, ReviewError, accept, describe, reject

router = APIRouter(tags=["review"])


@router.get("/proposals", response_model=list[Proposal])
def proposals(status: str = "pending", kind: str | None = None, sort: str = "time",
              st: AppState = Depends(state)) -> list[dict[str, Any]]:
    if sort not in ("time", "impact"):
        raise HTTPException(422, "sort is time or impact")
    found = st.store.proposals(status=None if status == "all" else status, kind=kind)
    return by_impact(st.store, found) if sort == "impact" else found


@router.get("/proposals/{proposal_id}", response_model=ProposalDetail, response_model_exclude_unset=True)
def proposal(proposal_id: int, samples: int = Query(5, ge=1, le=100_000, description="facts to include"),
             st: AppState = Depends(state)) -> dict[str, Any]:
    try:
        out = describe(st.store, proposal_id, samples=samples)
    except ReviewError as e:
        raise HTTPException(404, str(e)) from None
    if out["status"] == "pending":
        out["impact"] = preview(st.store, proposal_id)
    return out


def _decided(st: AppState, decision) -> dict[str, Any]:
    return {"journal": journal_entry(st.store.journal_entry(decision.journal_id)),
            "proposal": st.store.proposal(decision.proposal_id), "warnings": decision.warnings,
            "proposals_created": decision.report.proposals_created if decision.report else []}


def _review_error(e: ReviewError) -> HTTPException:
    if isinstance(e, DecidedError):
        return HTTPException(409, str(e))
    return HTTPException(404 if str(e).startswith("no proposal") else 422, str(e))


@router.post("/proposals/{proposal_id}/accept", response_model=Decision)
def accept_proposal(proposal_id: int, body: AcceptBody | None = None, st: AppState = Depends(state),
                    who: str = Depends(author)) -> dict[str, Any]:
    body = body or AcceptBody()
    try:
        decision = accept(st.store, proposal_id, who, comment=body.comment, target_id=body.target_id,
                          new_node=body.new_node)
    except ReviewError as e:
        raise _review_error(e) from None
    return _decided(st, decision)


@router.post("/proposals/{proposal_id}/reject", response_model=Decision)
def reject_proposal(proposal_id: int, body: RejectBody | None = None, st: AppState = Depends(state),
                    who: str = Depends(author)) -> dict[str, Any]:
    try:
        decision = reject(st.store, proposal_id, who, comment=(body or RejectBody()).comment)
    except ReviewError as e:
        raise _review_error(e) from None
    return _decided(st, decision)
