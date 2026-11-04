"""`scgraph llm smoke` and `scgraph llm spend`."""

from __future__ import annotations

import argparse

from scgraph.config import PROVIDERS, Config
from scgraph.llm.providers import Transport
from scgraph.llm.smoke import run_smoke
from scgraph.llm.store import Store


def add_commands(commands: argparse._SubParsersAction) -> None:
    llm = commands.add_parser("llm", help="LLM gateway: smoke test and spend report")
    llm_commands = llm.add_subparsers(dest="llm_command", metavar="LLM_COMMAND", required=True)

    smoke = llm_commands.add_parser("smoke", help="run one tool-call round trip against a provider (cache off)")
    smoke.add_argument("--provider", choices=PROVIDERS, required=True)
    smoke.set_defaults(func=cmd_smoke)

    spend = llm_commands.add_parser("spend", help="show ledger totals per provider and model")
    spend.add_argument("--run", metavar="RUN_ID", help="only this run")
    spend.set_defaults(func=cmd_spend)


def cmd_smoke(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    return run_smoke(config, args.provider, transport=transport)


def cmd_spend(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    if not config.db_path.exists():
        print(f"no ledger yet at {config.db_path}")
        return 0
    store = Store(config.db_path)
    rows = store.report(args.run)
    title = f"run {args.run}" if args.run else "all runs"
    print(f"LLM spend ({title}), ledger {config.db_path}")
    if not rows:
        print("  no entries")
        return 0
    header = f"{'provider':<8} {'model':<22} {'calls':>6} {'cached':>6} {'in tok':>10} {'out tok':>10} {'cost $':>10}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['provider']:<8} {r['model']:<22} {r['calls']:>6} {r['cache_hits']:>6} "
            f"{r['input_tokens']:>10} {r['output_tokens']:>10} {r['cost_usd']:>10.4f}"
        )
    print()
    for provider in sorted({r["provider"] for r in rows}):
        line = f"{provider}: ${sum(r['cost_usd'] for r in rows if r['provider'] == provider):.4f}"
        # A ledger can hold providers this build no longer supports; they have no budget to show.
        if not args.run and (budget := config.budgets_usd.get(provider)) is not None:
            line += f" of ${budget:.2f} budget"
        print(line)
    return 0
