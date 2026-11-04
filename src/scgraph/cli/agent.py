"""`scgraph agent ...`: ask the agent, read a thread, list events. Turns run here, in the foreground."""

from __future__ import annotations

import argparse
import sys

from scgraph.config import Config
from scgraph.llm import LLMClient
from scgraph.llm.providers import KEY_VARS, Transport, api_key_for
from scgraph.ops.service import OpsService
from scgraph.ops.store import OpsError, OpsStore


def add_commands(commands: argparse._SubParsersAction) -> None:
    agent = commands.add_parser("agent", help="the agent: ask, threads, events")
    sub = agent.add_subparsers(dest="agent_command", metavar="AGENT_COMMAND", required=True)
    ask = sub.add_parser("ask", help="ask the agent (calls the model); a new general thread unless --thread")
    ask.add_argument("text")
    ask.add_argument("--thread", type=int, metavar="N", help="post in thread N instead")
    ask.set_defaults(func=cmd_ask)
    thread = sub.add_parser("thread", help="print a thread, or list threads without N")
    thread.add_argument("id", type=int, nargs="?")
    thread.set_defaults(func=cmd_thread)
    events = sub.add_parser("events", help="recorded events, newest first")
    events.add_argument("--limit", type=int, default=20)
    events.set_defaults(func=cmd_events)


def _service(config: Config, transport: Transport | None) -> OpsService:
    def client() -> LLMClient | None:
        if transport is None and not api_key_for(config.provider, config):
            return None
        return LLMClient(config, transport=transport)

    return OpsService(OpsStore(config.ops_db_path), config.graph_store(), client, config=config)


def cmd_ask(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    if svc.client_factory() is None:
        print(f"no model provider is configured (set {KEY_VARS[config.provider]})", file=sys.stderr)
        return 1
    try:
        if args.thread is None:
            tid = svc.threads.start(args.text, config.author, inline=True)["thread_id"]
        else:
            tid = args.thread
            svc.threads.post(tid, args.text, config.author, inline=True)
    except OpsError as e:
        print(e, file=sys.stderr)
        return 1
    answer = svc.store.agent.messages(tid, last=1)[0]
    for block in answer["blocks"]:
        print(svc.threads.summary(block))
    print(f"\n(thread {tid}; `scgraph agent thread {tid}` shows it)")
    return 0 if answer["role"] == "agent" and svc.store.agent.thread(tid)["needs"] != "agent failed" else 1


def cmd_thread(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    if args.id is None:
        rows = svc.store.agent.threads()
        if not rows:
            print("no threads; `scgraph agent ask \"...\"` starts one")
        for t in rows:
            bound = f"disruption {t['disruption_id']}" if t["disruption_id"] else "general"
            needs = f"  [needs: {t['needs']}]" if t["needs"] else ""
            print(f"{t['id']:>4}. {t['title']} ({bound}, {t['status']}, {t['updated_at']}){needs}")
        return 0
    thread = svc.store.agent.thread(args.id)
    if thread is None:
        print(f"no thread {args.id}", file=sys.stderr)
        return 1
    print(f"thread {thread['id']}: {thread['title']}" + (f" [needs: {thread['needs']}]" if thread["needs"] else ""))
    for m in svc.store.agent.messages(args.id):
        queued = " (queued)" if m["status"] == "queued" else ""
        print(f"\n{m['created_at']} {m['author']}{queued}:")
        for block in m["blocks"]:
            print("  " + svc.threads.summary(block).replace("\n", "\n  "))
    return 0


def cmd_events(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    rows = svc.store.agent.events(limit=args.limit)
    if not rows:
        print("no events")
    for e in rows:
        changed = f" changed {', '.join(e['payload']['changed'])}" if e["payload"].get("changed") else ""
        run = f", run {e['run_id']}" if e["run_id"] else ""
        print(f"{e['id']:>4}. {e['created_at']} {e['type']} disruption {e['payload'].get('disruption_id')} from "
              f"{e['source']}{changed} (thread {e['thread_id']}{run})")
    return 0


__all__ = ["add_commands"]
