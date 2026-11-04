"""`scgraph graph ...` and `scgraph exposure ...` commands."""

from __future__ import annotations

import argparse
from typing import Any

from scgraph.config import Config
from scgraph.exposure.compute import EXPOSED_TYPES, Exposure, ExposureError, Summary
from scgraph.exposure.oracle import CATEGORIES, check
from scgraph.graph.schema import SCHEMA
from scgraph.graph.store import GraphStore
from scgraph.mapping.execute import execute
from scgraph.mapping.spec import SpecError, load_spec
from scgraph.mapping.tables import TableError

LIST_LIMIT = 20


def add_commands(commands: argparse._SubParsersAction) -> None:
    graph = commands.add_parser("graph", help="graph store: load a file with a mapping spec, inspect")
    graph_commands = graph.add_subparsers(dest="graph_command", metavar="GRAPH_COMMAND", required=True)

    load = graph_commands.add_parser("load", help="execute a mapping spec on a file and load the facts")
    load.add_argument("file")
    load.add_argument("--spec", required=True, help="mapping spec JSON")
    load.add_argument("--reset", action="store_true", help="clear the graph database first")
    load.set_defaults(func=cmd_load)

    graph_commands.add_parser("stats", help="counts per type, flagged and referenced-only").set_defaults(
        func=cmd_stats)

    show = graph_commands.add_parser("show", help="one node with field provenance and edges")
    show.add_argument("type")
    show.add_argument("id")
    show.set_defaults(func=cmd_show)

    graph_commands.add_parser("schema", help="the canonical schema by domain").set_defaults(func=cmd_schema)

    exposure = commands.add_parser("exposure", help="Business Exposure, Business Impact, oracle check")
    exposure_commands = exposure.add_subparsers(dest="exposure_command", metavar="EXPOSURE_COMMAND", required=True)

    show = exposure_commands.add_parser("show", help="exposure and impacted assemblies with BOM paths")
    show.add_argument("type", choices=EXPOSED_TYPES)
    show.add_argument("id")
    show.set_defaults(func=cmd_exposure_show)

    top = exposure_commands.add_parser("top", help="rank entities of a type by exposure")
    top.add_argument("type", choices=EXPOSED_TYPES)
    top.add_argument("-n", type=int, default=10)
    top.set_defaults(func=cmd_exposure_top)

    exposure_commands.add_parser("check", help="compare with the oracle values; exit 1 on mismatch").set_defaults(
        func=cmd_exposure_check)


def _open(config: Config) -> GraphStore | None:
    if not config.graph_db_path.exists():
        print(f"no graph yet at {config.graph_db_path}; run `scgraph graph load FILE --spec SPEC`")
        return None
    return config.graph_store()


def _value(value: Any) -> str:
    text = repr(value) if not isinstance(value, str) else value
    return text if len(text) <= 80 else text[:77] + "..."


def cmd_load(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    try:
        spec = load_spec(args.spec)
        result = execute(spec, args.file)
    except (SpecError, TableError, OSError) as e:
        print(f"spec error: {e}")
        return 1
    store = config.graph_store()
    if args.reset:
        store.reset()
    report = store.load(result.facts, inputs=[args.file, args.spec], oracle=result.oracle,
                        parameters=result.parameters)
    print(f"run {report.run_id}: {args.file} with {args.spec} -> {config.graph_db_path}")
    for region, rows in result.rows.items():
        print(f"  region {region}: {rows} rows")
    counts = report.counts()
    print(f"facts loaded {counts['loaded']}, rejected {counts['rejected']}, fields flagged {counts['flagged']}, "
          f"oracle values {counts['oracle_values']}, parameters {counts['parameters']}")
    print(f"facts applied {counts['applied']}, proposed {counts['proposed']}"
          f" ({counts['proposals_created']} new proposals, {counts['proposals_updated']} updated),"
          f" unchanged {counts['unchanged']}, dropped {counts['dropped']}")
    sections = [
        ("rejected facts", [f"{f.type} {f.key}: {err}" for f, err in report.rejected]),
        ("transform failures", [f"{x.source.label()} [{x.column}] {x.raw!r}: {x.error}" for x in result.failures]),
        ("unmapped columns", [f"{u.region}: {u.sheet}!{u.column} {u.header or ''}" for u in result.unmapped]),
        ("unmapped sheets", [str(s) for s in result.unmapped_sheets]),
        ("flagged fields", [f"{t} {i}.{f}" for t, i, f in report.flagged]),
        ("referenced-only nodes", [f"{t} {i}" for t, i in report.referenced_only]),
    ]
    for title, lines in sections:
        if lines:
            print(f"{title} ({len(lines)}):")
            for line in lines[:LIST_LIMIT]:
                print(f"  {line}")
            if len(lines) > LIST_LIMIT:
                print(f"  ... {len(lines) - LIST_LIMIT} more")
    store.close()
    return 0


def cmd_stats(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    stats = store.stats()
    print(f"graph {config.graph_db_path}")
    print("nodes:")
    for name, n in sorted(stats["nodes"].items()):
        print(f"  {name:<16} {n:>6}")
    print("edges:")
    for name, n in sorted(stats["edges"].items()):
        occ = stats["occurrences"].get(name)
        print(f"  {name:<16} {n:>6}" + (f"  ({occ} occurrences)" if occ else ""))
    print(f"field values {stats['field_values']}, flagged fields {stats['flagged_fields']}, "
          f"referenced-only nodes {stats['referenced_only']}, unresolved mentions {stats['unresolved_mentions']}")
    print(f"oracle values {stats['oracle_values']}, parameters {stats['parameters']}, runs {stats['runs']}")
    pending = ", ".join(f"{kind} {n}" for kind, n in stats["proposals"].items()) or "none"
    print(f"pending proposals: {pending}")
    store.close()
    return 0


def cmd_show(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    if args.type not in SCHEMA.entities or not store.has_node(args.type, args.id):
        hidden = _hidden(store, args.type, args.id) if args.type in SCHEMA.entities else None
        print(hidden or f"no {args.type} {args.id!r} in the graph")
        return 1
    ref = dict(((t, i), r) for t, i, r in store.nodes(args.type))[(args.type, args.id)]
    print(f"{args.type} {args.id}" + ("  [referenced-only]" if ref else ""))
    for source, method, confidence, resolution in store.node_sources(args.type, args.id):
        print(f"  from {source.label()} ({method}, {confidence:g})" + _resolution(resolution))
    print("fields:")
    for name, values in store.field_values(args.type, args.id).items():
        for v in values:
            marker = "*" if v.active else " "
            lock = " locked" if v.locked else ""
            proposal = f" #{v.proposal_id}" if v.proposal_id else ""
            print(f" {marker}{name:<22} {_value(v.value):<40} conf {v.confidence:g}  {v.method:<7} {v.status:<7}"
                  f"{lock}{proposal} {v.source.label()}" + _resolution(v.resolution))
    print("edges:")
    occurrences = store.occurrences()
    for edge in store.edges_of(args.type, args.id):
        out = edge.src_type == args.type and edge.src_id == args.id
        other = f"{edge.dst_type} {edge.dst_id}" if out else f"{edge.src_type} {edge.src_id}"
        context = ", ".join(f"{k}={v}" for k, v in edge.identity().items() if v is not None)
        occ = occurrences.get(edge.id)
        extra = f"  ({len(occ)} occurrences)" if occ else ""
        print(f"  {'->' if out else '<-'} {edge.type} {other}" + (f" [{context}]" if context else "") + extra)
    store.close()
    return 0


def _hidden(store: GraphStore, type_name: str, node_id: str) -> str | None:
    """Why a known id is not in the graph: merged into another node, or deleted."""
    if (target := store.alias_of(type_name, node_id)) is not None:
        return f"{type_name} {node_id} was merged into {store.resolve_alias(type_name, target)}"
    if (marker := store.deletion("node", type_name, node_id)) is not None:
        reason = f": {marker['reason']}" if marker["reason"] else ""
        return f"{type_name} {node_id} was deleted by {marker['author']} at {marker['ts']}{reason}"
    return None


def _resolution(resolution: dict | None) -> str:
    if not resolution or resolution.get("method") == "id":
        return ""
    candidates = f", candidates {resolution['candidates']}" if resolution.get("candidates") else ""
    return (f"  [resolved {resolution.get('mention')!r} by {resolution['method']},"
            f" conf {resolution.get('confidence', 0):g}{candidates}]")


def cmd_schema(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    for domain, content in SCHEMA.describe().items():
        print(f"domain {domain}")
        for name, e in content["entities"].items():
            print(f"  entity {name} (key: {', '.join(e['identity'])})")
            print("    " + ", ".join(f"{f}:{t}" for f, t in e["fields"].items()))
        for name, r in content["relations"].items():
            identity = ", ".join(f"{k}:{v}" for k, v in r["identity"].items())
            print(f"  relation {name}: {r['from']} -> {r['to']}" + (f" (identity: {identity})" if identity else ""))
            if r["fields"]:
                occ = " per occurrence" if r["occurrence_fields"] else ""
                print("    " + ", ".join(f"{f}:{t}" for f, t in r["fields"].items()) + occ)
    return 0


def _eur(value: float | None) -> str:
    return "unavailable" if value is None else f"{value / 1e6:,.2f} M EUR"


def _summary_line(s: Summary) -> str:
    parts = [f"{s.business_exposure_pct:8.4f}%"]
    if s.adjusted_pct is not None:
        parts.append(f"adjusted {s.adjusted_pct:8.4f}%")
    if s.type == "Supplier":
        parts.append(f"impact {_eur(s.business_impact_eur)}")
    parts.append(f"assemblies {len(s.assemblies)}")
    return "  ".join(parts)


def cmd_exposure_show(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    exposure = Exposure.from_store(store)
    try:
        s = exposure.summary(args.type, args.id)
        paths = exposure.impacted_with_paths(args.type, args.id)
    except ExposureError as e:
        print(e)
        return 1
    print(f"{s.type} {s.id}")
    print(f"  Business Exposure   {s.business_exposure_pct:.4f}% of {exposure.total_revenue / 1e9:.2f} bn EUR")
    if s.adjusted_pct is not None:
        print(f"  adjusted (single source factor {exposure.single_source_factor(s.id):g})  {s.adjusted_pct:.4f}%")
    if s.type == "Supplier":
        months = "unavailable" if s.recovery_time_months is None else f"{s.recovery_time_months:g} months"
        print(f"  recovery time       {months}")
        print(f"  Business Impact     {_eur(s.business_impact_eur)}")
    if s.type != "Component":
        print(f"  components          {len(s.components)}")
    if not paths:
        print("  impacted assemblies: none")
    for assembly, bom_paths in paths.items():
        revenue = exposure.revenue.get(assembly, 0.0)
        print(f"  assembly {assembly} ({revenue / 1e6:,.1f} M EUR)")
        for path in bom_paths[:LIST_LIMIT]:
            print(f"    {' > '.join(path)}")
        if len(bom_paths) > LIST_LIMIT:
            print(f"    ... {len(bom_paths) - LIST_LIMIT} more paths")
    store.close()
    return 0


def cmd_exposure_top(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    ranking = Exposure.from_store(store).ranking(args.type)[: args.n]
    print(f"top {len(ranking)} {args.type} by Business Exposure")
    for rank, s in enumerate(ranking, 1):
        print(f"{rank:>3}. {s.id:<14} {_summary_line(s)}")
    store.close()
    return 0


def cmd_exposure_check(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    report = check(store)
    print(f"{'metric':<42}" + "".join(f"{c:>16}" for c in CATEGORIES))
    for (entity_type, metric), counts in report.by_metric().items():
        print(f"{entity_type + ' ' + metric:<42}" + "".join(f"{counts[c]:>16}" for c in CATEGORIES))
    for title, items in (("known anomalies", report.known_anomalies), ("MISMATCHES", report.mismatched)):
        if items:
            print(f"{title} ({len(items)}):")
        for c in items:
            print(f"  {c.entity_type} {c.entity_id} {c.metric}: computed {c.computed:.10g}, oracle {c.oracle:.10g}"
                  f" at {c.source.label()}" + (f" -- {c.anomaly}" if c.anomaly else ""))
    store.close()
    if report.mismatched:
        print(f"FAIL: {len(report.mismatched)} mismatches")
        return 1
    print("PASS: no mismatches")
    return 0
