"""Operations as one service over the ops store, the graph snapshot, the simulator, the agent and the job runner.

The API and the command line both call this. Graph reads go through `snapshot()`, which the caller runs under the
API lock when there is one; everything after that works on the immutable snapshot. Agent runs are turns of the
disruption's thread (`threads`, a `scgraph.agent.threads.ThreadService`); creating or editing a disruption emits
the events that start the planner.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from scgraph.agent import triggers
from scgraph.config import Config
from scgraph.graph.store import GraphStore
from scgraph.llm import LLMClient
from scgraph.ops.company import Company, company_from_graph
from scgraph.ops.news import draft_from_news
from scgraph.ops.scenarios import TYPE_LABELS, Disruption, DisruptionError, library
from scgraph.ops.simulate import (ACTION_LABELS, REFERENCES_VERSION, RULES, ActionError, Simulator, check,
                                  describe_action, normalize, skill)
from scgraph.ops.store import NotFoundError, OpsError, OpsStore, now

ClientFactory = Callable[[], LLMClient | None]


class OpsService:
    def __init__(self, store: OpsStore, graph: GraphStore, client_factory: ClientFactory,
                 graph_lock: threading.Lock | None = None, *, config: Config | None = None,
                 submit: Callable[[str, str, Callable], str] | None = None) -> None:
        """`submit` is the job runner's submit; without one, agent turns run only when asked inline (command line)
        and events wait queued for the server."""
        from scgraph.agent.threads import ThreadService

        self.store, self.graph, self.client_factory = store, graph, client_factory
        self.graph_lock = graph_lock or threading.Lock()
        self.config = config or Config()
        self._cache: tuple[int, Company] | None = None
        store.fail_unfinished_runs()
        self.threads = ThreadService(self, self.config, submit)
        if submit is not None:
            self.threads.resume()

    # graph snapshot ------------------------------------------------------------------------------------------

    def snapshot(self, locked: bool = False) -> Company:
        """The company built from the current graph, cached per graph version."""
        if not locked:
            with self.graph_lock:
                return self.snapshot(locked=True)
        from scgraph.review.preview import _base

        version = self.graph.version()
        if self._cache is None or self._cache[0] != version:
            base = _base(self.graph)
            params = {n: p.value for n, p in self.graph.parameters().items()}
            self._cache = (version, company_from_graph(base.graph, params))
        return self._cache[1]

    # disruptions ---------------------------------------------------------------------------------------------

    def library(self, company: Company) -> list[dict[str, Any]]:
        out = []
        for d in library(company):
            o = Simulator(company, d).run([])
            out.append({**d.to_dict(), "type_label": TYPE_LABELS[d.type], "affected_components": len(
                Simulator(company, d).comps), "do_nothing_cost": o.total_cost, "lost_revenue": o.lost_revenue})
        return out

    def create(self, company: Company, data: dict[str, Any], author: str, status: str = "active") -> int:
        """Save a disruption with its thread; the planner starts on the `disruption.created` event."""
        d = Disruption.from_dict(data)
        d.validate(company)
        did = self.store.add_disruption(d.to_dict(), author, status=status)
        self.emit("disruption.created", did, d.source)
        return did

    def update(self, company: Company, did: int, data: dict[str, Any]) -> list[str]:
        """Edit a disruption; returns the changed fields, which a `disruption.updated` event lists (not for an
        archived disruption)."""
        row = self.store.writable(did)
        d = Disruption.from_dict(data)
        d.validate(company)
        changed = triggers.changed_fields(Disruption.from_dict(row).to_dict(), d.to_dict())
        self.store.update_disruption(did, data={**d.to_dict(), **({"warnings": row["warnings"]} if row.get(
            "warnings") and not changed else {})})
        if changed and row["status"] != "archived":
            self.emit("disruption.updated", did, "edit", changed=changed)
        return changed

    def emit(self, type_: str, did: int, source: str, **payload: Any) -> int | None:
        return triggers.emit(self.threads, type_, did, source, **payload)

    def disruption(self, did: int) -> Disruption:
        return Disruption.from_dict(self.store.disruption(did))

    def draft_from_news(self, company: Company, text: str, author: str = "system", source: str = "news",
                        document: str | None = None) -> dict[str, Any]:
        """A draft disruption from a news text (or a document recognised as a disruption report), saved at once
        with status `draft` and its warnings so the planner starts on it. A document is kept as its source."""
        client = self.client_factory()
        if client is None:
            raise DisruptionError("no model provider is configured")
        draft = draft_from_news(company, text, client)
        draft.disruption.source = source
        data = {**draft.disruption.to_dict(), "warnings": draft.warnings}
        if document is not None:
            data |= {"document": document, "source_text": text[:6000]}
        did = self.store.add_disruption(data, author, status="draft")
        tid = self.store.agent.thread_for(did)["id"]
        self.emit("disruption.created", did, source)
        return {"id": did, "thread_id": tid, "disruption": draft.disruption.to_dict(), "warnings": draft.warnings,
                "cost_usd": draft.cost_usd}

    def draft_from_document(self, text: str, name: str, author: str, locked: bool = False) -> dict[str, Any]:
        """The draft disruption of a document classified as a disruption report, as ingest reports list it."""
        out = self.draft_from_news(self.snapshot(locked=locked), text, author, "document", document=name)
        return {"id": out["id"], "name": out["disruption"]["name"], "thread_id": out["thread_id"],
                "warnings": out["warnings"]}

    def assessment(self, company: Company, d: Disruption) -> dict[str, Any]:
        sim = Simulator(company, d)
        nothing = sim.run([])
        comps = []
        for cid in sim.comps:
            comp = company.components[cid]
            sup = company.suppliers[comp.supplier]
            comps.append({"id": cid, "description": comp.description, "commodity": comp.commodity,
                          "criticality": comp.criticality, "single_source": comp.single_source,
                          "supplier": sup.id, "supplier_name": sup.name, "supplier_country": sup.country,
                          "weekly_value": comp.weekly_value, "assemblies": comp.assemblies,
                          "reason": sim.eff.reasons[cid], "weeks_short": nothing.short_components.get(cid, 0)})
        suppliers = sorted({c["supplier"] for c in comps})
        return {
            "affected_components": comps,
            "affected_suppliers": [{"id": s, "name": company.suppliers[s].name, "country": company.suppliers[s].country}
                                   for s in suppliers],
            "affected_sites": sorted(sim.eff.affected_sites),
            "assemblies": [{"id": a, "business_unit": company.assemblies[a].business_unit,
                            "weekly_revenue": company.assemblies[a].weekly_revenue,
                            "lost_revenue": nothing.lost_revenue_by_assembly.get(a, 0.0)} for a in sim.assemblies],
            "do_nothing": nothing.to_dict(),
            "margin": company.margin, "margin_from_graph": company.margin_from_graph,
        }

    def references(self, company: Company, did: int) -> dict[str, Any]:
        row = self.store.disruption(did)
        if row["references"] is None or row["references"].get("version") != REFERENCES_VERSION:
            refs = Simulator(company, Disruption.from_dict(row)).references()
            self.store.set_references(did, refs)
            return refs
        return row["references"]

    def detail(self, company: Company, did: int) -> dict[str, Any]:
        row = self.store.disruption(did)
        d = Disruption.from_dict(row)
        refs = self.references(company, did)
        sim = Simulator(company, d)
        actions = self.store.actions(did)
        accepted = [a["action"] for a in actions if a["status"] == "accepted"]
        accepted_outcome = sim.run(accepted).to_dict() if accepted else None
        runs = self.store.runs(did)
        latest = next((r for r in runs if r["status"] == "done"), None)
        nothing = refs["do_nothing"]["outcome"]["total_cost"]
        optimiser = refs["optimiser"]["outcome"]["total_cost"]
        for a in actions:
            a["label"] = ACTION_LABELS[a["action"]["type"]]
            a["text"] = describe_action(company, a["action"])
            a["valid"] = check(company, d, sim.eff, normalize(a["action"])) is None
        return {
            "disruption": {**row, "references": None, "type_label": TYPE_LABELS[d.type]},
            "assessment": self.assessment(company, d),
            "references": refs,
            "runs": [_run_summary(r) for r in runs],
            "latest_run": latest,
            "actions": actions,
            "accepted": {"plan": accepted, "outcome": accepted_outcome,
                         "skill": skill(nothing, optimiser, accepted_outcome["total_cost"]) if accepted_outcome else None},
            "tasks": self.store.tasks(did=did),
            "rules": RULES,
            "thread": self.store.agent.thread_for(did),
            "plan": self.threads.resolve({"type": "plan", "ref": plan["id"]})["plan"] if (
                plan := self.store.agent.current_plan(did)) else None,
        }

    # agent runs ----------------------------------------------------------------------------------------------

    def start_run(self, company: Company, did: int, submit: Callable[[str, str, Callable], str] | None = None,
                  author: str | None = None) -> dict[str, Any]:
        """Ask for a plan: a planner turn in the disruption's thread, as a background job."""
        self.disruption(did)
        if self.threads.submit is None:
            self.threads.submit = submit
        result = self.threads.request_plan(did, author or self.config.author)
        return {"run_id": result["run_id"], "job_id": result["job_id"]}

    def run_now(self, company: Company, did: int, author: str | None = None) -> dict[str, Any]:
        """Run the planner in this thread of execution (command line); the run is a turn of the disruption's
        thread."""
        self.disruption(did)
        result = self.threads.request_plan(did, author or self.config.author, inline=True)
        return self.store.run(result["run_id"])

    # resolving -----------------------------------------------------------------------------------------------

    def resolve(self, company: Company, did: int, note: str, author: str) -> dict[str, Any]:
        """Close a draft or active disruption: record the outcome at this moment (accepted actions and their
        simulated cost, doing nothing, the references, tasks, plan version, model cost) with the report written
        now, executive summary included, so reading it later re-simulates nothing and calls no model. Open tasks
        stay open; the thread is archived."""
        from scgraph.ops import reports

        row = self.store.writable(did)
        if row["status"] not in ("draft", "active"):
            raise OpsError(f"disruption {did} is {row['status']}; only a draft or active disruption is resolved")
        note = note.strip()
        detail = self.detail(company, did)
        nothing = detail["references"]["do_nothing"]["outcome"]
        accepted = detail["accepted"]
        plan_cost = accepted["outcome"]["total_cost"] if accepted["outcome"] else nothing["total_cost"]
        plan = self.store.agent.current_plan(did)
        resolved = {"at": now(), "by": author, "note": note}
        resolution = {
            "accepted": [{"id": a["id"], "text": a["text"], "action": a["action"], "reason": a["reason"]}
                         for a in detail["actions"] if a["status"] == "accepted"],
            "rejected": sum(a["status"] == "rejected" for a in detail["actions"]),
            "outcome": accepted["outcome"], "skill": accepted["skill"], "do_nothing": nothing,
            "references": detail["references"],
            "tasks": [{k: t[k] for k in ("id", "title", "status", "owner", "due_week")} for t in detail["tasks"]],
            "plan_version": plan["version"] if plan else None,
            "do_nothing_cost": nothing["total_cost"], "plan_cost": plan_cost,
            "saved": nothing["total_cost"] - plan_cost,
            "model_cost_usd": sum(r["cost_usd"] for r in self.store.runs(did)),
            "report": reports.disruption_report(company, detail, self.client_factory(), resolved=resolved),
        }
        tid = self.threads.thread_of(did)
        self.threads.decision(tid, author, "disruption", did, "resolved", note or None, row["name"])
        self.store.resolve(did, author, note, resolution)
        return {"id": did, "status": "resolved", "thread_id": tid}

    def resolved(self) -> list[dict[str, Any]]:
        """Resolved disruptions, most recently resolved first, with the figures recorded at resolution."""
        out = []
        for row in self.store.disruptions(statuses=("resolved",)):
            r = self.store.resolution(row["id"]) or {}
            thread = self.store.agent.thread_for(row["id"])
            out.append({"id": row["id"], "name": row["name"], "type": row["type"], "type_label": TYPE_LABELS[row["type"]],
                        "resolved_at": row["resolved_at"], "resolved_by": row["resolved_by"],
                        "resolution_note": row["resolution_note"], "thread_id": thread["id"] if thread else None,
                        **{k: r.get(k) for k in ("do_nothing_cost", "plan_cost", "saved", "model_cost_usd",
                                                 "plan_version")},
                        "accepted_actions": len(r.get("accepted") or [])})
        return sorted(out, key=lambda x: x["resolved_at"] or "", reverse=True)

    # follow-up clock -----------------------------------------------------------------------------------------

    def elapsed_week(self, row: dict[str, Any], at: datetime | None = None) -> int | None:
        """Whole weeks since the disruption was confirmed (created, when it never was a draft); None for a draft,
        whose clock has not started."""
        if row["status"] == "draft":
            return None
        start = row.get("confirmed_at") or row["created_at"]
        at = at or datetime.now(UTC)
        return max(0, (at - datetime.fromisoformat(start)).days // 7)

    def open_tasks(self, at: datetime | None = None) -> list[dict[str, Any]]:
        """Open and in-progress tasks of every draft, active or resolved disruption, with the disruption, its thread
        and whether the task is overdue (its due week passed on the disruption's clock)."""
        rows = {d["id"]: d for d in self.store.disruptions(statuses=("draft", "active", "resolved"))}
        out = []
        for t in self.store.tasks():
            row = rows.get(t["disruption_id"])
            if row is None or t["status"] not in ("open", "in_progress"):
                continue
            week = self.elapsed_week(row, at)
            thread = self.store.agent.thread_for(row["id"])
            out.append({**t, "disruption_name": row["name"], "disruption_status": row["status"],
                        "thread_id": thread["id"] if thread else None, "elapsed_week": week,
                        "overdue": week is not None and t["due_week"] is not None and t["due_week"] < week})
        return sorted(out, key=lambda t: (not t["overdue"], t["due_week"] if t["due_week"] is not None else 1 << 30))

    def needs(self, thread: dict[str, Any], overdue: set[int]) -> str | None:
        """Why a thread needs a person: its stored reason, else `task overdue` when one of its disruption's tasks
        is (`overdue`: the disruption ids with an overdue task)."""
        if thread["needs"] or thread["status"] != "active":
            return thread["needs"]
        return "task overdue" if thread["disruption_id"] in overdue else None

    # decisions and tasks -------------------------------------------------------------------------------------

    def decide(self, company: Company, aid: int, accept: bool, author: str, comment: str | None = None,
               owner: str | None = None) -> dict[str, Any]:
        self.store.writable(self.store.action(aid)["disruption_id"])
        action = self.store.decide(aid, accept, author, comment)
        if accept:
            self._task(company, action, owner or author)
        return action

    def add_manual(self, company: Company, did: int, raw: dict[str, Any], reason: str, author: str,
                   owner: str | None = None) -> dict[str, Any]:
        self.store.writable(did)
        d = self.disruption(did)
        action = normalize(raw)
        sim = Simulator(company, d)
        problem = check(company, d, sim.eff, action)
        if problem:
            raise ActionError(problem)
        if any(a["status"] == "accepted" and _same(a["action"], action) for a in self.store.actions(did)):
            raise ActionError("the same action is already accepted")
        aid = self.store.add_manual(did, action, reason, author)
        stored = self.store.action(aid)
        self._task(company, stored, owner or author)
        return stored

    def _task(self, company: Company, action: dict[str, Any], owner: str) -> None:
        a = action["action"]
        due = a.get("start_week", 0)
        self.store.create_task(action["id"], action["disruption_id"], describe_action(company, a), owner, due)


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("type", "component", "supplier", "assemblies")
    return all(a.get(k) == b.get(k) for k in keys)


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run[k] for k in ("id", "status", "provider", "model", "started_at", "finished_at", "skill", "cost_usd",
                                "input_tokens", "output_tokens", "error")} | {"steps": len(run["steps"])}


__all__ = ["DisruptionError", "NotFoundError", "OpsError", "OpsService"]
