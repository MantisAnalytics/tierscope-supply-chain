"""The agent: threads (list, start, read, post), decisions on edit blocks, and events.

Turns run as background jobs; reading a thread shows its messages with every block resolved to the current state
of what it shows, the steps of its turns and what is still queued.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from scgraph.agent.edits import EditBlockError
from scgraph.api.app import AppState, app_state, author
from scgraph.editing.edits import EditBatchError, EditError
from scgraph.ops.store import NotFoundError, OpsError

router = APIRouter(prefix="/agent", tags=["agent"])


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, NotFoundError):
        return HTTPException(404, str(e))
    if isinstance(e, EditBlockError) and "changed since" in str(e):
        return HTTPException(409, str(e))
    return HTTPException(422, str(e))


ERRORS = (OpsError, EditBlockError, EditError, EditBatchError, ValueError)


@router.get("/threads")
def threads(status: str | None = None, disruption: int | None = None, needs: bool | None = None,
            st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    """Threads, most recently active first: filtered by status (active, archived), disruption, and whether they
    need a person; each with its last message as one line and, for a disruption's thread, the disruption's status.
    `needs` includes `task overdue` when the disruption has an overdue task and nothing else is waiting."""
    svc = st.ops.threads
    overdue = {t["disruption_id"] for t in st.ops.open_tasks() if t["overdue"]}
    statuses = {d["id"]: d["status"] for d in st.ops.store.disruptions(include_archived=True)}
    out = []
    for t in st.ops.store.agent.threads(status=status, disruption_id=disruption):
        t["needs"] = st.ops.needs(t, overdue)
        if needs is not None and bool(t["needs"]) != needs:
            continue
        last = st.ops.store.agent.messages(t["id"], last=1)
        out.append({**t, "disruption_status": statuses.get(t["disruption_id"]),
                    "last": {"author": last[0]["author"], "role": last[0]["role"], "created_at": last[0]["created_at"],
                             "text": " ".join(svc.summary(b) for b in last[0]["blocks"])[:200]} if last else None})
    return out


@router.post("/threads")
def start(body: dict[str, Any] = Body(...), st: AppState = Depends(app_state),
          who: str = Depends(author)) -> dict[str, Any]:
    """A general thread with its first message; the agent answers in the background."""
    try:
        return st.ops.threads.start(str(body.get("text", "")), who)
    except ERRORS as e:
        raise _fail(e) from None


@router.get("/threads/{tid}")
def read(tid: int, st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        return st.ops.threads.read(tid)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/threads/{tid}/messages")
def post(tid: int, body: dict[str, Any] = Body(...), st: AppState = Depends(app_state),
         who: str = Depends(author)) -> dict[str, Any]:
    """A message to the agent; queued behind a running turn, answered in order."""
    try:
        return st.ops.threads.post(tid, str(body.get("text", "")), who)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/edits/{bid}/apply")
def apply_edit(bid: int, st: AppState = Depends(app_state), who: str = Depends(author)) -> dict[str, Any]:
    """Apply an edit block through graph editing, with the caller as author and the thread as origin; 409 when a
    value it changes moved since the agent proposed it."""
    try:
        return st.ops.threads.apply_edit(bid, who)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/edits/{bid}/dismiss")
def dismiss_edit(bid: int, body: dict[str, Any] = Body(default={}), st: AppState = Depends(app_state),
                 who: str = Depends(author)) -> dict[str, Any]:
    try:
        st.ops.threads.dismiss_edit(bid, who, body.get("comment"))
    except ERRORS as e:
        raise _fail(e) from None
    return st.ops.store.agent.edit_block(bid)


@router.get("/events")
def events(thread: int | None = None, limit: int = Query(50, ge=1, le=500),
           st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    """Recorded events, newest first, optionally of one thread."""
    return st.ops.store.agent.events(thread_id=thread, limit=limit)
