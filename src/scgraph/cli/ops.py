"""`scgraph ops ...`: disruptions, their assessment, an agent plan and reports from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scgraph.config import Config
from scgraph.llm import LLMClient
from scgraph.llm.providers import Transport, api_key_for
from scgraph.ops.agent import eur
from scgraph.ops.bench import (RESULTS_DIR, BenchAborted, bench_meta, compare_markdown, git_commit, load_runs, load_summaries,
                               references, rescore, run_bench, summarize, summary_markdown, write_results)
from scgraph.ops.episode_bench import DEV_PER_LEVEL, TEST_PER_LEVEL
from scgraph.ops.episode_bench import RESULTS_DIR as EPISODE_RESULTS
from scgraph.ops.episode_bench import SUITE_PATH as EPISODE_SUITE
from scgraph.ops.reports import disruption_report, portfolio_report, scorecard, to_markdown
from scgraph.ops.scenarios import DisruptionError
from scgraph.ops.service import OpsService
from scgraph.ops.shock_episodes import DEFAULT_PATH as EPISODES_EXPORT
from scgraph.ops.simulate import describe_action
from scgraph.ops.store import OpsError, OpsStore
from scgraph.ops.suite import DEFAULT_PATH as SUITE_PATH
from scgraph.ops.suite import DEFAULT_SEED, SuiteError, cases_for, generate_suite, load_suite, save_suite


def add_commands(commands: argparse._SubParsersAction) -> None:
    ops = commands.add_parser("ops", help="operations: disruptions, agent plans, reports")
    sub = ops.add_subparsers(dest="ops_command", metavar="OPS_COMMAND", required=True)
    lib = sub.add_parser("library", help="ready disruptions for the current graph, with their do-nothing cost")
    lib.add_argument("--save", type=int, nargs="*", metavar="N", help="save these library entries (1-based)")
    lib.set_defaults(func=cmd_library)
    sub.add_parser("list", help="saved disruptions").set_defaults(func=cmd_list)
    assess = sub.add_parser("assess", help="impact and the reference plans of a saved disruption")
    assess.add_argument("id", type=int)
    assess.set_defaults(func=cmd_assess)
    plan = sub.add_parser("plan", help="run the agent on a saved disruption (calls the model)")
    plan.add_argument("id", type=int)
    plan.set_defaults(func=cmd_plan)
    report = sub.add_parser("report", help="print a report as Markdown: a disruption id, portfolio or scorecard")
    report.add_argument("which")
    report.set_defaults(func=cmd_report)
    suite = sub.add_parser("suite", help="generate the benchmark suite of disruptions from the current graph")
    suite.add_argument("--seed", type=int, default=DEFAULT_SEED)
    suite.add_argument("--out", type=Path, default=SUITE_PATH)
    suite.set_defaults(func=cmd_suite)
    bench = sub.add_parser("bench", help="run the agent on the benchmark suite (calls the model)")
    bench.add_argument("--suite", type=Path, default=SUITE_PATH)
    bench.add_argument("--split", choices=("dev", "test", "all"), default="test")
    bench.add_argument("--cases", nargs="*", metavar="ID", help="only these case ids")
    bench.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    bench.add_argument("--workers", type=int, default=4)
    bench.add_argument("--name", help="result set name (default: model, agent version, split)")
    bench.add_argument("--out", type=Path, default=RESULTS_DIR)
    bench.set_defaults(func=cmd_bench)
    rescore_p = sub.add_parser("bench-rescore", help="score a result set again against freshly computed references")
    rescore_p.add_argument("dir", type=Path)
    rescore_p.add_argument("--suite", type=Path, default=SUITE_PATH)
    rescore_p.set_defaults(func=cmd_bench_rescore)
    compare = sub.add_parser("bench-compare", help="compare benchmark result sets side by side")
    compare.add_argument("dirs", nargs="*", type=Path, help="result directories (default: all under the results dir)")
    compare.set_defaults(func=cmd_bench_compare)
    ep_suite = sub.add_parser("episodes-suite", help="map ShockBench episodes onto the graph and choose the episode "
                                                     "benchmark's dev and test episodes by harm level")
    ep_suite.add_argument("--export", type=Path, default=EPISODES_EXPORT)
    ep_suite.add_argument("--out", type=Path, default=EPISODE_SUITE)
    ep_suite.add_argument("--dev-per-level", type=int, default=DEV_PER_LEVEL)
    ep_suite.add_argument("--test-per-level", type=int, default=TEST_PER_LEVEL)
    ep_suite.add_argument("--workers", type=int, default=8, help="processes for the references")
    ep_suite.add_argument("--cache", type=Path, default=Path(".cache/episode_refs"),
                          help="keeps each episode's references as they finish, so an interrupted build resumes")
    ep_suite.add_argument("--refresh-replanner", action="store_true",
                          help="recompute only the informed re-planner of the suite at --out (after a change to the "
                               "review weeks or the views)")
    ep_suite.add_argument("--add-tune", type=int, metavar="N",
                          help="add a tune split of N more episodes per harm level to the suite at --out")
    ep_suite.set_defaults(func=cmd_episodes_suite)
    ep_bench = sub.add_parser("episodes-bench", help="run the planner through the episode benchmark (calls the model)")
    ep_bench.add_argument("--suite", type=Path, default=EPISODE_SUITE)
    ep_bench.add_argument("--split", choices=("dev", "tune", "test", "all"), default="test")
    ep_bench.add_argument("--episodes", nargs="*", metavar="ID", help="only these episode ids")
    ep_bench.add_argument("--limit", type=int, help="only the first N episodes of the split")
    ep_bench.add_argument("--runs", type=int, default=1, help="runs per episode (default 1)")
    ep_bench.add_argument("--workers", type=int, default=4)
    ep_bench.add_argument("--name", help="result set name (default: model, agent version, split)")
    ep_bench.add_argument("--out", type=Path, default=EPISODE_RESULTS)
    ep_bench.set_defaults(func=cmd_episodes_bench)


def _service(config: Config, transport: Transport | None) -> OpsService:
    def client() -> LLMClient | None:
        if transport is None and not api_key_for(config.provider, config):
            return None
        return LLMClient(config, transport=transport)

    return OpsService(OpsStore(config.ops_db_path), config.graph_store(), client)


def cmd_library(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    co = svc.snapshot()
    entries = svc.library(co)
    for i, d in enumerate(entries, start=1):
        print(f"{i}. {d['name']} [{d['type_label']}] week {d['start_week']} for {d['duration_weeks']} weeks: "
              f"{d['affected_components']} components, {eur(d['do_nothing_cost'])} if nothing is done")
    for n in args.save or []:
        if not 1 <= n <= len(entries):
            print(f"no library entry {n}", file=sys.stderr)
            return 1
        did = svc.create(co, entries[n - 1], config.author)
        print(f"saved {entries[n - 1]['name']} as disruption {did}")
    return 0


def cmd_list(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    rows = svc.store.disruptions()
    if not rows:
        print("no disruptions; `scgraph ops library --save 1` saves one")
    for d in rows:
        draft = " [draft]" if d["status"] == "draft" else ""
        print(f"{d['id']}. {d['name']}{draft} ({d['type']}, week {d['start_week']} for {d['duration_weeks']} weeks)")
    return 0


def cmd_assess(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    try:
        co = svc.snapshot()
        detail = svc.detail(co, args.id)
    except (OpsError, DisruptionError) as e:
        print(e, file=sys.stderr)
        return 1
    a, refs = detail["assessment"], detail["references"]
    print(detail["disruption"]["name"])
    for c in a["affected_components"]:
        print(f"  {c['id']} {c['description']} ({c['supplier_name']}, {c['supplier_country']}): {c['reason']}; "
              f"{c['weeks_short']} weeks short")
    for name in ("do_nothing", "playbook", "optimiser"):
        o = refs[name]["outcome"]
        print(f"  {name:<11} total {eur(o['total_cost']):>14}  lost revenue {eur(o['lost_revenue']):>14}  "
              f"actions {len(refs[name]['plan'])}  skill {refs[name].get('skill')}")
    return 0


def cmd_plan(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    try:
        co = svc.snapshot()
        run = svc.run_now(co, args.id)
    except (OpsError, DisruptionError) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"run {run['id']}: {run['status']} with {run['model']}, {len(run['steps'])} steps, "
          f"{run['input_tokens']}/{run['output_tokens']} tokens, ${run['cost_usd']:.4f}")
    if run["status"] != "done":
        print(run["error"], file=sys.stderr)
        return 1
    for a in run["plan"]:
        print(f"  - {describe_action(co, a)}: {a.get('reason', '')}")
    print(f"  total cost {eur(run['outcome']['total_cost'])}, skill {run['skill']}")
    print(f"  {run['summary']}")
    return 0


def cmd_report(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    co = svc.snapshot()
    try:
        if args.which == "portfolio":
            active = svc.store.disruptions(statuses=("active",))
            for d in active:
                d["references"] = svc.references(co, d["id"])
            doc = portfolio_report(co, config.graph_store().stats()["proposals"], active)
        elif args.which == "scorecard":
            doc = scorecard(svc.store)
        else:
            doc = disruption_report(co, svc.detail(co, int(args.which)), svc.client_factory())
    except (OpsError, DisruptionError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    sys.stdout.write(to_markdown(doc))
    return 0


def cmd_suite(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    co = _service(config, transport).snapshot()
    suite = generate_suite(co, args.seed)
    save_suite(suite, args.out)
    for c in suite["cases"]:
        d = c["disruption"]
        print(f"{c['id']:<22} {c['split']:<4} {d['name']}")
    missing = ", ".join(f"{k} ({v})" for k, v in suite["missing"].items())
    print(f"{len(suite['cases'])} cases written to {args.out}" + (f"; types short of cases: {missing}" if missing else ""))
    return 0


def cmd_bench(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    svc = _service(config, transport)
    co = svc.snapshot()
    try:
        suite = load_suite(args.suite)
        cases = cases_for(suite, co, args.split, args.cases)
    except SuiteError as e:
        print(e, file=sys.stderr)
        return 1
    if transport is None and not api_key_for(config.provider, config):
        print(f"no API key for provider {config.provider}", file=sys.stderr)
        return 1

    def client() -> LLMClient:
        return LLMClient(config, transport=transport, cache_mode="off")

    probe = client()
    from scgraph.ops.agent import AGENT_VERSION

    name = args.name or f"{probe.default_model}-{AGENT_VERSION}-{args.split}"
    meta = bench_meta(co, suite, args.split, args.runs, "full", probe, name)  # the commit as the run starts
    print(f"{name}: {len(cases)} cases x {args.runs} runs; computing references", file=sys.stderr)
    refs = references(co, cases)
    done = [0]

    def progress(r: dict) -> None:
        done[0] += 1
        print(f"  [{done[0]}/{len(cases) * args.runs}] {r['case']} run {r['run']}: skill {r['skill']:.2f}"
              + ("" if r["valid"] else f" (no plan: {r['error']})") + f", ${r['cost_usd']:.4f}", file=sys.stderr)

    try:
        records = run_bench(co, cases, client, runs=args.runs, workers=args.workers, variant="full",
                            on_record=progress, refs=refs)
    except BenchAborted as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    summary = summarize(records, meta)
    write_results(args.out / name, records, summary)
    sys.stdout.write(summary_markdown(summary))
    print(f"written to {args.out / name}", file=sys.stderr)
    return 0


def cmd_bench_rescore(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    co = _service(config, transport).snapshot()
    summary_path = args.dir / "summary.json"
    if not (args.dir / "runs.jsonl.gz").is_file() or not summary_path.is_file():
        print(f"no runs.jsonl.gz and summary.json in {args.dir}", file=sys.stderr)
        return 1
    try:
        suite = load_suite(args.suite)
        cases = cases_for(suite, co, "all")
    except SuiteError as e:
        print(e, file=sys.stderr)
        return 1
    records = load_runs(args.dir)
    wanted = {r["case"] for r in records}
    records = rescore(records, references(co, [c for c in cases if c.id in wanted]))
    old = json.loads(summary_path.read_text())
    meta = {k: v for k, v in old.items() if k not in ("overall", "by_type", "cases", "usd_total")}
    meta["rescored"] = f"{datetime.now(UTC).strftime('%Y-%m-%d')} at commit {git_commit()}"
    summary = summarize(records, meta)
    write_results(args.dir, records, summary)
    sys.stdout.write(summary_markdown(summary))
    return 0


def cmd_bench_compare(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    if args.dirs:
        summaries = []
        for d in args.dirs:
            path = d / "summary.json"
            if not path.is_file():
                print(f"no summary.json in {d}", file=sys.stderr)
                return 1
            summaries.append(json.loads(path.read_text()))
    else:
        summaries = load_summaries()
    sys.stdout.write(compare_markdown(summaries))
    return 0


def cmd_episodes_suite(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    from scgraph.ops import episode_bench as eb
    from scgraph.ops.shock_episodes import load_export

    co = _service(config, transport).snapshot()
    if args.add_tune:
        from scgraph.ops.shock_episodes import load_export
        suite = eb.add_tuning(co, load_export(args.export), eb.load_suite(args.out), per_level=args.add_tune,
                              workers=args.workers, progress=lambda m: print(m, file=sys.stderr))
        eb.save_suite(suite, args.out)
        print(f"{sum(e['split'] == 'tune' for e in suite['episodes'])} tune episodes in {args.out}")
        return 0
    if args.refresh_replanner:
        suite = eb.refresh_replanner(co, eb.load_suite(args.out), workers=args.workers,
                                     progress=lambda m: print(m, file=sys.stderr))
        eb.save_suite(suite, args.out)
        print(f"re-planner recomputed for {len(suite['episodes'])} episodes; suite version {suite['version']}")
        return 0
    if not args.export.is_file():
        print(f"no ShockBench export at {args.export}: run benchmark/scripts/export_episodes.py", file=sys.stderr)
        return 1
    suite = eb.build_suite(co, load_export(args.export), dev_per_level=args.dev_per_level,
                           test_per_level=args.test_per_level, workers=args.workers, cache_dir=args.cache,
                           progress=lambda m: print(m, file=sys.stderr))
    eb.save_suite(suite, args.out)
    counts = {s: sum(1 for e in suite["episodes"] if e["split"] == s) for s in ("dev", "test")}
    print(f"{counts['dev']} dev and {counts['test']} test episodes written to {args.out}"
          + (f"; levels short of episodes: {suite['missing']}" if suite["missing"] else ""))
    return 0


def cmd_episodes_bench(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    from scgraph.ops import episode_bench as eb

    co = _service(config, transport).snapshot()
    try:
        suite = eb.load_suite(args.suite)
        eb.check_graph(suite, co)
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    rows = [e for e in suite["episodes"] if args.split == "all" or e["split"] == args.split]
    if args.episodes:
        rows = [e for e in rows if e["id"] in set(args.episodes)]
    rows = rows[:args.limit] if args.limit else rows
    if transport is None and not api_key_for(config.provider, config):
        print(f"no API key for provider {config.provider}", file=sys.stderr)
        return 1

    def client() -> LLMClient:
        return LLMClient(config, transport=transport, cache_mode="off")

    probe = client()
    from scgraph.ops.agent import AGENT_VERSION

    name = args.name or f"{probe.default_model}-{AGENT_VERSION}-{args.split}"
    meta = eb.bench_meta(co, suite, args.split, args.runs, "full", probe, name)
    print(f"{name}: {len(rows)} episodes x {args.runs} runs", file=sys.stderr)
    done = [0]

    def progress(r: dict) -> None:
        done[0] += 1
        gain = r["do_nothing"] - r["optimiser"]
        skill = (r["do_nothing"] - r["cost"]) / gain if gain > 0 else float("nan")
        print(f"  [{done[0]}/{len(rows) * args.runs}] {r['episode']} (level {r['level']}): cost {eur(r['cost'])}, "
              f"skill {skill:.2f}, {len(r['turns'])} reviews ({r['failed_turns']} failed), ${r['cost_usd']:.4f}",
              file=sys.stderr)

    try:
        records = eb.run_bench(co, rows, eb.suite_priors(suite), client, runs=args.runs, workers=args.workers,
                               variant="full", on_record=progress)
    except eb.BenchAborted as e:
        print(f"stopped: {e}", file=sys.stderr)
        if not e.records:
            return 1
        name = f"{name}-partial"  # keep what finished, marked as partial
        records, meta = e.records, {**meta, "name": name, "partial": str(e)}
    summary = eb.summarize(records, meta)
    eb.write_results(args.out / name, records, summary)
    sys.stdout.write(eb.summary_markdown(summary))
    print(f"written to {args.out / name}", file=sys.stderr)
    return 0


__all__ = ["add_commands"]
