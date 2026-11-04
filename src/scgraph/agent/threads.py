"""Threads: conversations with the agent, one turn at a time per thread, rendered compactly to the model.

Posting a message or delivering an event queues an input and kicks the thread. A kicked thread runs one
background job that takes the oldest queued input, runs its profile (`chat` for people, `planner` for events and
plan requests), appends the agent's message and repeats until nothing is queued; inputs that arrive meanwhile wait
their turn. Decisions (actions, edit blocks, drafts) are posted as decision blocks, so the next turn sees them.

The model gets the last messages of the thread, not replayed tool calls: text as text, other blocks as one-line
summaries. The current state (plan and decisions) is stated in the turn's context section, so trimming the
history loses no decision.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from scgraph.agent import edits as agent_edits
from scgraph.agent.gate import Gate, eur
from scgraph.agent.plans import bare, staleness
from scgraph.agent.profiles import PLANNER, PROFILES, run_turn
from scgraph.agent.tools import Turn
from scgraph.agent.world import LiveWorld
from scgraph.config import Config
from scgraph.llm import LLMClient
from scgraph.llm.providers import KEY_VARS
from scgraph.ops.simulate import action_key, describe_action
from scgraph.ops.store import NotFoundError, OpsError

if TYPE_CHECKING:
    from scgraph.ops.service import OpsService

HISTORY = 20  # messages rendered to the model
AUTO_PREFIX = "auto-"  # ledger run ids of event-started turns
Submit = Callable[[str, str, Callable], str]  # the job runner's submit(kind, title, fn) -> job id


class ThreadError(OpsError):
    pass


class ThreadService:
    def __init__(self, ops: OpsService, config: Config | None = None, submit: Submit | None = None) -> None:
        self.ops, self.store, self.agent = ops, ops.store, ops.store.agent
        self.config = config or Config()
        self.submit = submit
        self._busy: dict[int, str] = {}  # thread -> job running its queue
        self._lock = threading.Lock()

    def world(self) -> LiveWorld:
        return LiveWorld(self.ops, Gate(spend_limit_eur=self.config.agent_spend_limit_eur))

    # threads and messages ------------------------------------------------------------------------------------

    def thread_of(self, did: int) -> int:
        """The disruption's thread (created when missing)."""
        name = self.store.disruption(did)["name"]
        return self.agent.bind_disruption(did, None, name)

    def start(self, text: str, author: str, *, inline: bool = False) -> dict[str, Any]:
        """A general thread opened by a person's first message; the agent answers in it."""
        text = text.strip()
        if not text:
            raise ThreadError("write a message first")
        tid = self.agent.create_thread(text[:80])
        return {"thread_id": tid, **self.post(tid, text, author, inline=inline)}

    def post(self, tid: int, text: str, author: str, *, inline: bool = False) -> dict[str, Any]:
        """A person's message, queued for the chat profile; the thread no longer needs a person."""
        thread = self._open_thread(tid)
        if not text.strip():
            raise ThreadError("write a message first")
        mid = self.agent.add_message(tid, author, "person", [{"type": "text", "text": text.strip()}],
                                     status="queued", profile="chat")
        if thread["needs"]:
            self.agent.update_thread(tid, needs=None)
        return {"message_id": mid, "job_id": self.kick(tid, inline=inline)}

    def request_plan(self, did: int, author: str, *, inline: bool = False) -> dict[str, Any]:
        """A person asks for a (new) plan: a planner input whose run exists at once, so callers can follow it."""
        self.store.writable(did)
        client = self._client()
        tid = self.thread_of(did)
        rid = self.store.start_run(did, client.provider, client.default_model)
        self.agent.set_run_meta(rid, thread_id=tid, profile=PLANNER.name, prompt_version=PLANNER.version, world="live")
        self.agent.add_message(tid, author, "person", [{"type": "text", "text": "Plan this disruption."}],
                               status="queued", profile=PLANNER.name, run_id=rid)
        self.agent.update_thread(tid, needs=None)
        job_id = self.kick(tid, inline=inline)
        if job_id:
            self.store.set_run_job(rid, job_id)
        return {"run_id": rid, "job_id": job_id, "thread_id": tid}

    def deliver_event(self, eid: int, tid: int) -> None:
        """An event in its thread: queued for the planner when triggers are on, else only shown."""
        blocks = [{"type": "event", "ref": eid}]
        if not self.config.agent_triggers:
            self.agent.add_message(tid, "system", "system", blocks)
            return
        self.agent.add_message(tid, "system", "system", blocks, status="queued", profile=PLANNER.name)
        self.kick(tid)

    def kick(self, tid: int, *, inline: bool = False) -> str | None:
        """Make sure the thread's queue is being worked; returns the job id (None when nothing runs it).

        Without a job runner the queue is worked only when `inline` (command line); events then wait for the
        server, which kicks queued threads on start."""
        with self._lock:
            if tid in self._busy:
                return self._busy[tid]
            if self.submit is None and not inline:
                return None
            self._busy[tid] = "inline"
        title = f"Agent: {self.agent.thread(tid)['title']}"
        if self.submit is None or inline:
            self._work(tid, None)
            return "inline"
        job_id = self.submit("agent", title, lambda job: self._work(tid, job))
        with self._lock:
            if self._busy.get(tid) == "inline":
                self._busy[tid] = job_id
        return job_id

    def resume(self) -> list[int]:
        """On server start: kick threads with queued inputs. Queued events alone wait while triggers are off or
        the auto-run budget is spent."""
        kicked = []
        for tid in self.agent.queued_threads():
            queued = [m for m in self.agent.messages(tid) if m["status"] == "queued"]
            people = any(m["role"] == "person" for m in queued)
            if people or (self.config.agent_triggers and self.auto_budget_left() > 0):
                self.kick(tid)
                kicked.append(tid)
        return kicked

    def _work(self, tid: int, job) -> dict[str, Any]:
        done = 0
        while True:
            with self._lock:
                message = self.agent.next_queued(tid)
                if message is None:
                    self._busy.pop(tid, None)
                    return {"thread_id": tid, "inputs": done}
            try:
                self._answer(message, job)
            except Exception as e:  # noqa: BLE001 - a broken turn must not block the queue
                self.agent.add_message(tid, "system", "system", [{"type": "text", "text": f"The agent failed: "
                                                                  f"{type(e).__name__}: {e}"}])
                self.agent.update_thread(tid, needs="agent failed")
                self.agent.finish_message(message["id"], None)
            done += 1

    # one turn ------------------------------------------------------------------------------------------------

    def _answer(self, message: dict[str, Any], job) -> None:
        tid = message["thread_id"]
        thread = self._thread(tid)
        did = thread["disruption_id"]
        profile = PROFILES[message["profile"] or "chat"]
        event = next((b["ref"] for b in message["blocks"] if b["type"] == "event"), None)
        if profile.name == "planner" and did is None:
            return self._close(message, None, "There is no disruption in this thread to plan.", None)
        if event is not None and any(m["status"] == "queued" and m["profile"] == PLANNER.name and m["id"] > message["id"]
                                     for m in self.agent.messages(tid)):
            self.agent.finish_message(message["id"], None)  # a newer planner input covers it
            return None
        client = self.ops.client_factory()
        if client is None:
            return self._close(message, None, f"No model provider is configured (set "
                                              f"{KEY_VARS[self.config.provider]}).", "agent failed")
        if event is not None:
            client.run_id = f"{AUTO_PREFIX}{uuid.uuid4().hex[:12]}"
            left = self.auto_budget_left(client)
            if left <= 0:
                return self._close(message, None, f"Planning was skipped: the budget for automatic runs "
                                                  f"(USD {self.config.budget_auto_usd:.2f}) is spent. Ask for a plan "
                                                  "to start it by hand.", "budget")
        rid = message["run_id"]
        if rid is None or self.store.run(rid)["status"] != "running":
            rid = self.store.start_run(did, client.provider, client.default_model)
        world = self.world()
        self.agent.set_run_meta(rid, thread_id=tid, profile=profile.name, prompt_version=profile.version,
                                world=world.name, modes=world.gate.modes, seed=client.run_seed,
                                trigger_event_id=event)
        if event is not None:
            self.agent.set_event_run(event, rid)
        if job is not None:
            self.store.set_run_job(rid, job.id)

        def on_step(step: dict[str, Any]) -> None:
            self.store.append_step(rid, step)
            if job is not None:
                job.report(thread=tid, run=rid, steps=len(self.store.run(rid)["steps"]),
                           last=step.get("name") or step["kind"])

        turn = Turn(world, f"agent:{client.default_model}", did, tid, rid,
                    plan=self.plan_context(did) if did is not None else None)
        try:
            result = run_turn(profile, turn, client, self.render(tid, message["id"]), on_step=on_step)
        except Exception as e:
            self.store.finish_run(rid, status="failed", error=f"{type(e).__name__}: {e}")
            raise
        plan = next((self.agent.plan(b["ref"]) for b in reversed(result.blocks) if b["type"] == "plan"), None)
        if plan is not None:
            self.agent.set_run_meta(rid, plan_id=plan["id"])
        self.store.finish_run(rid, status=result.status, plan=plan["actions"] if plan else None,
                              summary=plan["summary"] if plan else None, outcome=plan["outcome"] if plan else None,
                              skill=plan["skill"] if plan else None, input_tokens=result.input_tokens,
                              output_tokens=result.output_tokens, cost_usd=result.cost_usd, error=result.error)
        blocks = list(result.blocks)
        if result.text:
            blocks.append({"type": "text", "text": result.text})
        if result.status != "done":
            blocks.append({"type": "text", "text": f"I could not finish: {result.error}"})
        elif not blocks:
            blocks.append({"type": "text", "text": "(no answer)"})
        needs = "agent failed" if result.status != "done" else ("plan ready" if plan is not None else None)
        self.agent.add_message(tid, f"agent:{client.default_model}", "agent", blocks, run_id=rid)
        self.agent.finish_message(message["id"], rid)
        self.agent.update_thread(tid, needs=needs)

    def _close(self, message: dict[str, Any], rid: int | None, text: str, needs: str | None) -> None:
        if rid is None and message["run_id"] is not None:
            self.store.finish_run(message["run_id"], status="failed", error=text)
        self.agent.add_message(message["thread_id"], "system", "system", [{"type": "text", "text": text}])
        self.agent.finish_message(message["id"], rid)
        self.agent.update_thread(message["thread_id"], needs=needs)

    def auto_budget_left(self, client: LLMClient | None = None) -> float:
        client = client or self.ops.client_factory()
        if client is None:
            return self.config.budget_auto_usd
        return self.config.budget_auto_usd - client.store.spend_of_runs(AUTO_PREFIX)

    def _client(self) -> LLMClient:
        client = self.ops.client_factory()
        if client is None:
            raise OpsError(f"no model provider is configured (set {KEY_VARS[self.config.provider]})")
        return client

    def _thread(self, tid: int) -> dict[str, Any]:
        thread = self.agent.thread(tid)
        if thread is None:
            raise NotFoundError(f"no thread {tid}")
        return thread

    def _open_thread(self, tid: int) -> dict[str, Any]:
        """The thread, refused when its disruption is resolved (its thread is then read-only)."""
        thread = self._thread(tid)
        if thread["disruption_id"] is not None:
            self.store.writable(thread["disruption_id"])
        return thread

    # what the model sees -------------------------------------------------------------------------------------

    def render(self, tid: int, upto: int | None = None) -> list[dict[str, Any]]:
        """The last messages up to and including `upto` as model messages; consecutive roles merged, starting with
        the person (the model answers the last one)."""
        everything = self.agent.messages(tid)
        # an input that waited in the queue belongs right before its answer, not where it was posted
        answers = {m["run_id"]: m["id"] for m in everything if m["role"] == "agent" and m["run_id"] is not None}

        def position(m: dict[str, Any]) -> float:
            if m["id"] == upto:
                return float("inf")
            answered = m["profile"] is not None and m["run_id"] in answers
            return answers[m["run_id"]] - 0.5 if answered else m["id"]

        messages = sorted((m for m in everything if m["status"] == "done" or m["id"] == upto), key=position)
        messages = messages[-HISTORY:]
        out: list[dict[str, Any]] = []
        for m in messages:
            role = "assistant" if m["role"] == "agent" else "user"
            who = "" if m["role"] == "agent" else ("[system] " if m["role"] == "system" else f"[{m['author']}] ")
            text = who + "\n".join(self.summary(b) for b in m["blocks"])
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n\n" + text
            else:
                out.append({"role": role, "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def summary(self, block: dict[str, Any]) -> str:
        """One line for a block (text blocks stay as they are)."""
        kind, ref = block["type"], block.get("ref")
        if kind == "text":
            return block["text"]
        if kind == "plan":
            plan = self.agent.plan(ref)
            if plan is None:
                return f"[plan {ref}: removed]"
            actions = self.plan_actions(plan)
            counts = {s: sum(a["status"] == s for a in actions) for s in ("accepted", "rejected", "proposed")}
            comments = "; ".join(f'"{a["comment"]}"' for a in actions if a["status"] == "rejected" and a["comment"])
            total = eur((plan["outcome"] or {}).get("total_cost", 0.0))
            return (f"[plan v{plan['version']}: {len(actions)} actions, {counts['accepted']} accepted, "
                    f"{counts['rejected']} rejected{' ' + comments if comments else ''}, {counts['proposed']} "
                    f"proposed; total {total}{'; applied' if block.get('applied') else ''}]")
        if kind == "decision":
            comment = f': "{block["comment"]}"' if block.get("comment") else ""
            return f"[decision: {block['decision']} {block['kind']} {ref}{' (' + block['what'] + ')' if block.get('what') else ''}{comment}]"
        if kind == "event":
            event = self.agent.event(ref)
            if event is None:
                return f"[event {ref}]"
            changed = event["payload"].get("changed")
            return (f"[event {event['type']} from {event['source']}"
                    + (f"; changed: {', '.join(changed)}" if changed else "") + "]")
        if kind == "edit":
            eb = self.agent.edit_block(ref)
            if eb is None:
                return f"[edit block {ref}]"
            described = "; ".join(_describe(e) for e in eb["edits"])
            return f"[edit block {ref}: {described}; {eb['status']}" + (f" by {eb['decided_by']}]" if eb["decided_by"]
                                                                         else "]")
        if kind == "draft":
            try:
                row = self.store.disruption(ref)
            except NotFoundError:
                return f"[draft disruption {ref}: removed]"
            return f"[draft disruption {ref} {row['name']!r}: {row['status']}]"
        return f"[{kind} {ref}]"

    def plan_actions(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """A plan version's actions with their current status and decision comment."""
        did = plan["disruption_id"]
        rows = self.store.actions(did)
        mine = {action_key(a["action"]): a for a in rows if a["plan_id"] == plan["id"]}
        accepted = {action_key(a["action"]): a for a in rows if a["status"] == "accepted"}
        co = self.ops.snapshot()
        out = []
        for a in plan["actions"]:
            action = bare(a)
            key = action_key(action)
            row = mine.get(key) or (accepted.get(key) if a.get("kept") else None)
            out.append({"action": action, "text": describe_action(co, action), "reason": a.get("reason", ""),
                        "kept": bool(a.get("kept")), "action_id": row["id"] if row else None,
                        "status": row["status"] if row else ("accepted" if a.get("kept") else "proposed"),
                        "comment": row["comment"] if row else None})
        return out

    def plan_context(self, did: int) -> dict[str, Any] | None:
        """The current plan version with decisions, and every rejected action with its comment, for the context."""
        plan = self.agent.current_plan(did)
        if plan is None:
            return None
        co = self.ops.snapshot()
        stale = staleness(plan, co, self.ops.disruption(did), self.world().graph_version())
        actions = self.plan_actions(plan)
        listed = {a["action_id"] for a in actions}
        actions += [{"text": describe_action(co, r["action"]), "status": "rejected", "reason": r["reason"],
                     "comment": r["comment"]} for r in self.store.actions(did, ("rejected",)) if r["id"] not in listed]
        return {"version": plan["version"], "summary": plan["summary"], "stale": stale is not None, "actions": actions}

    # decisions -----------------------------------------------------------------------------------------------

    def decision(self, tid: int, author: str, kind: str, ref: int, decision: str, comment: str | None = None,
                 what: str | None = None) -> None:
        block = {"type": "decision", "kind": kind, "ref": ref, "decision": decision}
        if comment:
            block["comment"] = comment
        if what:
            block["what"] = what
        self.agent.add_message(tid, author, "person", [block])
        self.agent.update_thread(tid, needs=None)

    def decide_action(self, aid: int, accept: bool, author: str, comment: str | None = None,
                      owner: str | None = None) -> dict[str, Any]:
        co = self.ops.snapshot()
        action = self.ops.decide(co, aid, accept, author, comment, owner)
        self.decision(self.thread_of(action["disruption_id"]), author, "action", aid,
                      "accepted" if accept else "rejected", comment, describe_action(co, action["action"]))
        return action

    def apply_edit(self, bid: int, author: str) -> dict[str, Any]:
        block = self._edit_block(bid)
        result = agent_edits.apply_block(self.ops.graph, self.agent, bid, author, self.ops.graph_lock)
        self.decision(block["thread_id"], author, "edit", bid, "applied", what=f"journal #{result['journal_id']}")
        return result

    def dismiss_edit(self, bid: int, author: str, comment: str | None = None) -> None:
        block = self._edit_block(bid)
        if block["status"] != "proposed":
            raise ThreadError(f"edit block {bid} is already {block['status']}")
        self.agent.decide_edit_block(bid, "dismissed", author)
        self.decision(block["thread_id"], author, "edit", bid, "dismissed", comment)

    def _edit_block(self, bid: int) -> dict[str, Any]:
        block = self.agent.edit_block(bid)
        if block is None:
            raise NotFoundError(f"no edit block {bid}")
        self._open_thread(block["thread_id"])
        return block

    def save_draft(self, did: int, author: str) -> dict[str, Any]:
        """Confirm a draft as active. A draft the agent wrote in a general thread binds that thread to it and is
        planned now, as a new disruption."""
        row = self.store.disruption(did)
        if row["status"] != "draft":
            raise ThreadError(f"disruption {did} is {row['status']}, not a draft")
        had_thread = self.agent.thread_for(did) is not None
        tid = self.agent.bind_disruption(did, self.agent.thread_of_block("draft", did), row["name"])
        self.store.update_disruption(did, status="active")
        self.decision(tid, author, "draft", did, "saved", what=row["name"])
        if not had_thread:
            self.ops.emit("disruption.created", did, row.get("source") or "chat")
        return {"id": did, "status": "active", "thread_id": tid}

    def discard_draft(self, did: int, author: str, comment: str | None = None) -> dict[str, Any]:
        row = self.store.disruption(did)
        if row["status"] != "draft":
            raise ThreadError(f"disruption {did} is {row['status']}, not a draft")
        tid = (self.agent.thread_for(did) or {}).get("id") or self.agent.thread_of_block("draft", did)
        self.store.update_disruption(did, status="archived")
        if tid is not None:
            self.decision(tid, author, "draft", did, "discarded", comment, row["name"])
        return {"id": did, "status": "archived", "thread_id": tid}

    # reading a thread ----------------------------------------------------------------------------------------

    def read(self, tid: int) -> dict[str, Any]:
        """A thread with its messages, every block resolved to the current state of what it shows, and the steps
        of its turns."""
        thread = self._thread(tid)
        messages = self.agent.messages(tid)
        for m in messages:
            m["blocks"] = [self.resolve(b) for b in m["blocks"]]
        runs = [{"id": r["id"], "status": r["status"], "profile": r["profile"], "error": r["error"],
                 "steps": json.loads(r["steps_json"])} for r in self.agent.runs_of_thread(tid)]
        return {"thread": thread, "messages": messages, "runs": runs,
                "queue": [m["id"] for m in messages if m["status"] == "queued"],
                "running": tid in self._busy}

    def resolve(self, block: dict[str, Any]) -> dict[str, Any]:
        kind, ref = block["type"], block.get("ref")
        out = dict(block)
        if kind == "plan" and (plan := self.agent.plan(ref)) is not None:
            co = self.ops.snapshot()
            stale = staleness(plan, co, self.ops.disruption(plan["disruption_id"]), self.world().graph_version())
            current = self.agent.current_plan(plan["disruption_id"])
            cost = (plan["outcome"] or {}).get("action_cost", 0.0)
            out["plan"] = {**{k: plan[k] for k in ("id", "disruption_id", "version", "run_id", "graph_version",
                                                   "summary", "skill", "created_at")},
                           "actions": self.plan_actions(plan), "outcome": plan["outcome"], "stale": stale,
                           "current": current is not None and current["id"] == plan["id"],
                           "do_nothing_cost": self.ops.references(co, plan["disruption_id"])["do_nothing"]["outcome"]
                           ["total_cost"],
                           "spend_limit_eur": self.config.agent_spend_limit_eur,
                           "over_limit": cost > self.config.agent_spend_limit_eur}
        elif kind == "edit" and (eb := self.agent.edit_block(ref)) is not None:
            out["edit"] = {**eb, "changes": [{"edit": _describe(e), "old": b.get("value", b.get("exists")),
                                              "new": e.get("value", e.get("fields") or e.get("op"))}
                                             for e, b in zip(eb["edits"], eb["before"], strict=False)]}
        elif kind == "draft":
            try:
                out["disruption"] = self.store.disruption(ref)
                out["disruption"]["references"] = None
            except NotFoundError:
                out["disruption"] = None
        elif kind == "event":
            out["event"] = self.agent.event(ref)
        elif kind == "decision" and block["kind"] == "action":
            try:
                out["action"] = self.store.action(ref)
            except NotFoundError:
                out["action"] = None
        return out


def _describe(raw: dict[str, Any]) -> str:
    from scgraph.editing.edits import EditError, parse_edit

    try:
        return parse_edit(raw).describe()
    except EditError:
        return str(raw)
