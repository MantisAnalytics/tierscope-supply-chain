"""`scgraph` command line entry point: one module per command group."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from scgraph.cli.agent import add_commands as add_agent_commands
from scgraph.cli.graph import add_commands as add_graph_commands
from scgraph.cli.ingest import add_commands as add_ingest_commands
from scgraph.cli.llm import add_commands as add_llm_commands
from scgraph.cli.ops import add_commands as add_ops_commands
from scgraph.cli.review import add_commands as add_review_commands
from scgraph.config import Config, ConfigError, load_dotenv
from scgraph.llm.providers import Transport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scgraph", description="Supply chain graph tools.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    add_llm_commands(commands)
    add_graph_commands(commands)
    add_ingest_commands(commands)
    add_review_commands(commands)
    add_ops_commands(commands)
    add_agent_commands(commands)
    return parser


def main(argv: list[str] | None = None, *, transport: Transport | None = None,
         load_env: Callable[[], None] = load_dotenv) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        if getattr(args, "command", None) != "ingest":
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
        args.words = extra + args.words  # `ingest` parses its own options
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    load_env()
    try:
        config = Config.from_env()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return args.func(args, config, transport)


if __name__ == "__main__":
    sys.exit(main())
