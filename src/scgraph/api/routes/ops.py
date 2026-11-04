"""Operations: disruptions, agent runs, decisions on actions, follow-up tasks and reports.

Graph reads take the app lock only while the company snapshot is built; agent runs are background jobs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from scgraph.api.app import AppState, app_state, author
from scgraph.ops import reports, shockbench
from scgraph.ops.bench import load_summaries
from scgraph.ops.episode_bench import RESULTS_DIR as EPISODE_RESULTS
from scgraph.ops.company import CONTINENTS
from scgraph.ops.scenarios import TYPE_LABELS, TYPES, Disruption, DisruptionError
from scgraph.ops.simulate import ACTION_LABELS, ACTION_TYPES, RULES, ActionError, Simulator, skill
from scgraph.ops.store import NotFoundError, OpsError

router = APIRouter(prefix="/ops", tags=["operations"])


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, NotFoundError):
        return HTTPException(404, str(e))
    return HTTPException(422, str(e))


ERRORS = (DisruptionError, ActionError, OpsError, ValueError, TypeError, KeyError)


@router.get("/meta")
def meta(st: AppState = Depends(app_state)) -> dict[str, Any]:
    """What forms need: types, actions and their rules, the graph's countries, commodities, suppliers, sites,
    assemblies, ShockBench episodes, and the configured model."""
    co = st.ops.snapshot()
    client = st.ops.client_factory()
    return {
        "types": [{"id": t, "label": TYPE_LABELS[t]} for t in TYPES],
        "actions": [{"id": a, "label": ACTION_LABELS[a]} for a in ACTION_TYPES],
        "rules": RULES,
        "countries": co.countries(), "continents": list(CONTINENTS), "commodities": co.commodities(),
        "suppliers": [{"id": s.id, "name": s.name, "country": s.country} for s in
                      sorted(co.suppliers.values(), key=lambda s: s.name.casefold())],
        "sites": [{"id": s.id, "name": s.name, "country": s.country} for s in co.sites.values()],
        "assemblies": [{"id": a.id, "business_unit": a.business_unit, "revenue": a.revenue}
                       for a in sorted(co.assemblies.values(), key=lambda a: -a.revenue)],
        "shockbench_episodes": shockbench.episodes(),
        "model": {"provider": client.provider, "model": client.default_model} if client else None,
        "margin": co.margin,
    }


@router.get("/library")
def library(st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    return st.ops.library(st.ops.snapshot())


@router.get("/shockbench/{episode}")
def shockbench_curve(episode: int) -> dict[str, Any]:
    curve = shockbench.curve(episode)
    if curve is None:
        raise HTTPException(404, f"no shortage in ShockBench episode {episode}")
    return {"episode": episode, "curve": curve}


@router.get("/summary")
def summary(st: AppState = Depends(app_state)) -> dict[str, Any]:
    """For the Overview and navigation: active disruptions with their do-nothing cost, the number of drafts waiting
    for confirmation, and task counts. Drafts do not count as active."""
    co = st.ops.snapshot()
    active = []
    for row in st.ops.store.disruptions(statuses=("active",)):
        d = Disruption.from_dict(row)
        nothing = Simulator(co, d).run([])
        accepted = [a["action"] for a in st.ops.store.actions(row["id"], ("accepted",))]
        plan = Simulator(co, d).run(accepted) if accepted else None
        active.append({"id": row["id"], "name": d.name, "type": d.type, "type_label": TYPE_LABELS[d.type],
                       "start_week": d.start_week, "duration_weeks": d.duration_weeks,
                       "do_nothing_cost": nothing.total_cost, "lost_revenue": nothing.lost_revenue,
                       "plan_cost": plan.total_cost if plan else None, "accepted_actions": len(accepted),
                       **_thread(st, row["id"])})
    tasks = st.ops.open_tasks()
    overdue = {t["disruption_id"] for t in tasks if t["overdue"]}
    waiting = [t for t in st.ops.store.agent.threads(status="active")
               if t["disruption_id"] is not None and st.ops.needs(t, overdue)]
    return {"active": active, "drafts": len(st.ops.store.disruptions(statuses=("draft",))),
            "open_tasks": sum(t["status"] == "open" for t in tasks),
            "in_progress_tasks": sum(t["status"] == "in_progress" for t in tasks),
            "overdue_tasks": sum(t["overdue"] for t in tasks), "tasks": tasks, "agent_needs": len(waiting)}


@router.get("/disruptions")
def disruptions(archived: bool = False, st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    co = st.ops.snapshot()
    out = []
    for row in st.ops.store.disruptions(include_archived=archived):
        d = Disruption.from_dict(row)
        nothing = Simulator(co, d).run([])
        runs = st.ops.store.runs(row["id"])
        actions = st.ops.store.actions(row["id"])
        out.append({"id": row["id"], "status": row["status"], "name": d.name, "type": d.type,
                    "type_label": TYPE_LABELS[d.type], "start_week": d.start_week, "duration_weeks": d.duration_weeks,
                    "source": d.source, "created_at": row["created_at"], "do_nothing_cost": nothing.total_cost,
                    "lost_revenue": nothing.lost_revenue, "first_stop_week": nothing.first_stop_week,
                    "runs": len(runs), "latest_run": ({"status": runs[0]["status"], "skill": runs[0]["skill"],
                                                       "model": runs[0]["model"]} if runs else None),
                    "proposed": sum(a["status"] == "proposed" for a in actions),
                    "accepted": sum(a["status"] == "accepted" for a in actions),
                    "warnings": row.get("warnings") or [], **_thread(st, row["id"])})
    return out


def _thread(st: AppState, did: int) -> dict[str, Any]:
    thread = st.ops.store.agent.thread_for(did)
    return {"thread_id": thread["id"] if thread else None, "needs": thread["needs"] if thread else None}


@router.post("/disruptions")
def create(body: dict[str, Any] = Body(...), st: AppState = Depends(app_state),
           who: str = Depends(author)) -> dict[str, Any]:
    try:
        did = st.ops.create(st.ops.snapshot(), body, who)
    except ERRORS as e:
        raise _fail(e) from None
    return {"id": did}


@router.post("/disruptions/from-news")
def from_news(body: dict[str, Any] = Body(...), st: AppState = Depends(app_state),
              who: str = Depends(author)) -> dict[str, Any]:
    """A draft disruption from a pasted text, saved at once with status `draft` (its `id` and `thread_id` are in
    the answer) and planned by the agent; confirm or discard it afterwards."""
    try:
        return st.ops.draft_from_news(st.ops.snapshot(), str(body.get("text", "")), who)
    except ERRORS as e:
        raise _fail(e) from None
    except Exception as e:  # noqa: BLE001 - provider errors
        raise HTTPException(422, f"the model call failed: {e}") from None


@router.post("/assess")
def assess(body: dict[str, Any] = Body(...), st: AppState = Depends(app_state)) -> dict[str, Any]:
    """Impact of an unsaved disruption (form preview)."""
    co = st.ops.snapshot()
    try:
        d = Disruption.from_dict(body)
        d.validate(co)
    except ERRORS as e:
        raise _fail(e) from None
    return st.ops.assessment(co, d)


@router.get("/disruptions/{did}")
def detail(did: int, st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        return st.ops.detail(st.ops.snapshot(), did)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/disruptions/{did}")
def update(did: int, body: dict[str, Any] = Body(...), st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        st.ops.update(st.ops.snapshot(), did, body)
    except ERRORS as e:
        raise _fail(e) from None
    return {"id": did}


@router.post("/disruptions/{did}/status")
def set_status(did: int, body: dict[str, Any] = Body(...), st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        st.ops.store.update_disruption(did, status=str(body.get("status")))
    except ERRORS as e:
        raise _fail(e) from None
    return {"id": did, "status": body.get("status")}


@router.post("/disruptions/{did}/confirm")
def confirm(did: int, st: AppState = Depends(app_state), who: str = Depends(author)) -> dict[str, Any]:
    """Confirm a draft disruption as active; its plan versions are kept."""
    try:
        return st.ops.threads.save_draft(did, who)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/disruptions/{did}/discard")
def discard(did: int, body: dict[str, Any] = Body(default={}), st: AppState = Depends(app_state),
            who: str = Depends(author)) -> dict[str, Any]:
    """Discard a draft disruption: it is archived with its thread."""
    try:
        return st.ops.threads.discard_draft(did, who, body.get("comment"))
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/disruptions/{did}/resolve")
def resolve(did: int, body: dict[str, Any] = Body(default={}), st: AppState = Depends(app_state),
            who: str = Depends(author)) -> dict[str, Any]:
    """Resolve a draft or active disruption with a closing note: its outcome and report are recorded now, its
    thread is archived and it moves to Reports. Open tasks stay open."""
    try:
        return st.ops.resolve(st.ops.snapshot(), did, str(body.get("note") or ""), who)
    except ERRORS as e:
        raise _fail(e) from None


@router.get("/resolved")
def resolved(st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    """Resolved disruptions, most recently resolved first, with the figures recorded at resolution."""
    return st.ops.resolved()


@router.post("/disruptions/{did}/delete")
def delete(did: int, st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        st.ops.store.delete_disruption(did)
    except ERRORS as e:
        raise _fail(e) from None
    return {"deleted": did}


@router.post("/disruptions/{did}/simulate")
def simulate(did: int, body: dict[str, Any] = Body(...), st: AppState = Depends(app_state)) -> dict[str, Any]:
    """Outcome of a candidate plan (what-if), with its skill against the references."""
    co = st.ops.snapshot()
    try:
        d = st.ops.disruption(did)
        refs = st.ops.references(co, did)
        o = Simulator(co, d).run(list(body.get("actions") or []))
    except ERRORS as e:
        raise _fail(e) from None
    return {"outcome": o.to_dict(), "skill": skill(refs["do_nothing"]["outcome"]["total_cost"],
                                                   refs["optimiser"]["outcome"]["total_cost"], o.total_cost)}


@router.post("/disruptions/{did}/runs")
def start_run(did: int, st: AppState = Depends(app_state), who: str = Depends(author)) -> dict[str, Any]:
    """Ask the agent for a plan: a planner turn in the disruption's thread, as a background job."""
    try:
        return st.ops.start_run(st.ops.snapshot(), did, st.jobs.submit, who)
    except ERRORS as e:
        raise _fail(e) from None


@router.get("/runs/{rid}")
def run(rid: int, st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        return st.ops.store.run(rid)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/disruptions/{did}/actions")
def add_action(did: int, body: dict[str, Any] = Body(...), st: AppState = Depends(app_state),
               who: str = Depends(author)) -> dict[str, Any]:
    """A person adds an action from the catalogue; it is accepted at once and gets a task."""
    try:
        return st.ops.add_manual(st.ops.snapshot(), did, dict(body.get("action") or {}), str(body.get("reason", "")),
                                 who, body.get("owner") or None)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/actions/{aid}/accept")
def accept(aid: int, body: dict[str, Any] = Body(default={}), st: AppState = Depends(app_state),
           who: str = Depends(author)) -> dict[str, Any]:
    try:
        return st.ops.threads.decide_action(aid, True, who, body.get("comment"), body.get("owner") or None)
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/actions/{aid}/reject")
def reject(aid: int, body: dict[str, Any] = Body(default={}), st: AppState = Depends(app_state),
           who: str = Depends(author)) -> dict[str, Any]:
    try:
        return st.ops.threads.decide_action(aid, False, who, body.get("comment"))
    except ERRORS as e:
        raise _fail(e) from None


@router.post("/actions/{aid}/withdraw")
def withdraw(aid: int, body: dict[str, Any] = Body(default={}), st: AppState = Depends(app_state),
             who: str = Depends(author)) -> dict[str, Any]:
    try:
        st.ops.store.writable(st.ops.store.action(aid)["disruption_id"])
        st.ops.store.withdraw(aid, who, body.get("comment"))
        return st.ops.store.action(aid)
    except ERRORS as e:
        raise _fail(e) from None


@router.get("/tasks")
def tasks(status: str | None = None, disruption: int | None = None, owner: str | None = None,
          st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    names = {d["id"]: d["name"] for d in st.ops.store.disruptions(include_archived=True)}
    return [{**t, "disruption_name": names.get(t["disruption_id"])}
            for t in st.ops.store.tasks(did=disruption, status=status, owner=owner)]


@router.post("/tasks/{tid}")
def update_task(tid: int, body: dict[str, Any] = Body(...), st: AppState = Depends(app_state),
                who: str = Depends(author)) -> dict[str, Any]:
    try:
        return st.ops.store.update_task(tid, who, **body)
    except ERRORS as e:
        raise _fail(e) from None


def _report(st: AppState, kind: str, did: int | None) -> dict[str, Any]:
    co = st.ops.snapshot()
    if kind == "portfolio":
        with st.lock:
            pending = st.store.stats()["proposals"]
        active = [{**d, "references": st.ops.references(co, d["id"])}
                  for d in st.ops.store.disruptions(statuses=("active",))]
        return reports.portfolio_report(co, pending, active)
    if kind == "scorecard":
        return reports.scorecard(st.ops.store)
    if kind == "benchmark":
        return reports.benchmark_report(load_summaries(), load_summaries(EPISODE_RESULTS))
    if kind == "disruption" and did is not None:
        if (resolution := st.ops.store.resolution(did)) is not None:
            return resolution["report"]  # as written at resolution: no simulation, no model call
        return reports.disruption_report(co, st.ops.detail(co, did), st.ops.client_factory())
    raise HTTPException(404, f"no report {kind!r}")


@router.get("/reports")
def report_list(st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    out = [{"kind": "portfolio", "id": "portfolio", "title": "Portfolio risk report"},
           {"kind": "scorecard", "id": "scorecard", "title": "Agent scorecard"},
           {"kind": "benchmark", "id": "benchmark", "title": "Agent benchmark"}]
    for d in st.ops.store.disruptions(include_archived=True):
        out.append({"kind": "disruption", "id": d["id"], "title": d["name"], "status": d["status"],
                    "accepted": len(st.ops.store.actions(d["id"], ("accepted",))), "updated_at": d["updated_at"]})
    return out


@router.get("/reports/{kind}")
def report(kind: str, st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        return _report(st, kind, None)
    except ERRORS as e:
        raise _fail(e) from None


@router.get("/reports/{kind}/markdown", response_class=PlainTextResponse)
def report_markdown(kind: str, st: AppState = Depends(app_state)) -> PlainTextResponse:
    try:
        doc = _report(st, kind, None)
    except ERRORS as e:
        raise _fail(e) from None
    return _md(doc, kind)


@router.get("/reports/disruption/{did}")
def disruption_report(did: int, st: AppState = Depends(app_state)) -> dict[str, Any]:
    try:
        return _report(st, "disruption", did)
    except ERRORS as e:
        raise _fail(e) from None


@router.get("/reports/disruption/{did}/markdown", response_class=PlainTextResponse)
def disruption_markdown(did: int, st: AppState = Depends(app_state)) -> PlainTextResponse:
    try:
        doc = _report(st, "disruption", did)
    except ERRORS as e:
        raise _fail(e) from None
    return _md(doc, f"disruption-{did}")


def _md(doc: dict[str, Any], name: str) -> PlainTextResponse:
    return PlainTextResponse(reports.to_markdown(doc), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="tierscope-{name}.md"'})
