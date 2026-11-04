"""`scgraph ingest FILE...`, `scgraph ingest spec show|approve PATH`, `scgraph ingest compare FILE --reference SPEC`."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scgraph.config import Config
from scgraph.ingest.batch import ARCHIVE_SUFFIXES, Batch, BatchError, expand, remove, run_batch
from scgraph.ingest.compare import compare
from scgraph.ingest.docingest import DocumentReport, ingest_document
from scgraph.ingest.documents import TEXT_SUFFIXES
from scgraph.ingest.pipeline import DEFAULT_SPECS_DIR, IngestError, IngestReport, ingest
from scgraph.ingest.spec_review import approve, describe
from scgraph.ingest.step import StepError
from scgraph.llm import LLMClient, LLMError
from scgraph.llm.providers import Transport
from scgraph.mapping.spec import SpecError
from scgraph.mapping.tables import TableError

LIST_LIMIT = 10
USAGE = """usage:
  scgraph ingest FILE... [--reuse-only] [--no-load] [--specs-dir DIR]
  scgraph ingest spec show PATH
  scgraph ingest spec approve PATH
  scgraph ingest compare FILE --reference SPEC [--reference-file FILE] [--slice-assembly ID] [--specs-dir DIR]

FILE: .xlsx, .xlsm, .csv or .tsv (table ingest), .pdf, .txt or .md (document ingest), or a .zip of such files.
Several files or a zip run as one batch: tables first, then documents, a report per file and the totals."""


def add_commands(commands: argparse._SubParsersAction) -> None:
    ingest_parser = commands.add_parser("ingest", help="LLM-mapped ingest of a table file, spec review, comparison",
                                        usage=USAGE, add_help=False)
    ingest_parser.add_argument("words", nargs=argparse.REMAINDER)
    ingest_parser.set_defaults(func=cmd_ingest)


def cmd_ingest(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    words = args.words
    if not words or words[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if words else 2
    if words[0] == "spec":
        p = argparse.ArgumentParser(prog="scgraph ingest spec")
        p.add_argument("action", choices=("show", "approve"))
        p.add_argument("path", type=Path)
        sub = p.parse_args(words[1:])
        return _spec(sub.action, sub.path, config.author)
    if words[0] == "compare":
        p = argparse.ArgumentParser(prog="scgraph ingest compare")
        p.add_argument("file", type=Path)
        p.add_argument("--reference", type=Path, required=True)
        p.add_argument("--reference-file", type=Path, help="file the reference spec runs on (default FILE)")
        p.add_argument("--slice-assembly", help="compare only this assembly's slice of the reference graph")
        p.add_argument("--specs-dir", type=Path, default=DEFAULT_SPECS_DIR)
        sub = p.parse_args(words[1:])
        return _compare(sub, config, transport)
    p = argparse.ArgumentParser(prog="scgraph ingest")
    p.add_argument("files", type=Path, nargs="+", metavar="FILE")
    p.add_argument("--reuse-only", action="store_true", help="fail instead of calling the model")
    p.add_argument("--no-load", action="store_true", help="generate and validate specs without loading")
    p.add_argument("--specs-dir", type=Path, default=DEFAULT_SPECS_DIR)
    sub = p.parse_args(words)
    if len(sub.files) == 1 and sub.files[0].suffix.lower() not in ARCHIVE_SUFFIXES:
        sub.file = sub.files[0]
        return _ingest(sub, config, transport)
    return _batch(sub, config, transport)


def _batch(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    missing = [str(f) for f in args.files if not f.is_file()]
    if missing:
        print(f"ingest failed: no such file: {', '.join(missing)}", file=sys.stderr)
        return 1
    workdir = Path(tempfile.mkdtemp(prefix="scgraph-batch-"))
    try:
        batch = expand([(f.name, f) for f in args.files], workdir)
    except BatchError as e:
        remove(workdir)
        print(f"ingest failed: {e}", file=sys.stderr)
        return 1
    client = None if args.reuse_only else LLMClient(config, transport=transport)
    store = None if args.no_load else config.graph_store()
    print(f"batch of {sum(f.kind != 'skipped' for f in batch.files)} files, specs in {args.specs_dir}")
    for f in batch.files:
        print(f"  {f.status:<8} {f.name}" + (f" ({f.reason})" if f.reason else ""), flush=True)
    printed: set[str] = set()

    def show(b: Batch) -> None:
        for f in b.files:
            if f.status in ("done", "failed") and f.name not in printed:
                printed.add(f.name)
                print(f"\n== {f.name}: {f.status}")
                if f.status == "failed":
                    print(f"  ERROR: {f.error}")
                elif isinstance(f.report, DocumentReport):
                    print_document_report(f.report, args.specs_dir)
                elif f.report is not None:
                    print_report(f.report, args.specs_dir)
            elif f.status == "running" and b.current == f.name:
                print(f"\n.. {f.name}", flush=True)

    try:
        run_batch(batch, client, store=store, specs_dir=args.specs_dir, reuse_only=args.reuse_only, on_change=show,
                  on_disruption=None if store is None else _drafts(config, transport, store))
    finally:
        remove(workdir)
        if store is not None:
            store.close()
    t = batch.totals()
    print(f"\nbatch: {t['done']} done, {t['failed']} failed, {t['skipped']} skipped;"
          f" facts applied {t['facts_applied']}, proposed {t['facts_proposed']}"
          f" ({t['proposals_created']} new proposals), summary rows excluded {t['excluded_rows']};"
          f" LLM {t['llm_calls']} calls, ${t['cost_usd']:.4f}")
    return 1 if t["failed"] and not t["done"] else 0


def _ingest(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    client = None if args.reuse_only else LLMClient(config, transport=transport)
    store = None if args.no_load else config.graph_store()
    is_document = args.file.suffix.lower() in (".pdf", *TEXT_SUFFIXES)
    try:
        if is_document:
            document = ingest_document(args.file, client, store=store, specs_dir=args.specs_dir,
                                       reuse_only=args.reuse_only)
            if document.disruption_text is not None and store is not None:
                document.disruption = _drafts(config, transport, store)(document.disruption_text, document.file)
        else:
            report = ingest(args.file, client, store=store, specs_dir=args.specs_dir, reuse_only=args.reuse_only)
    except (IngestError, StepError, LLMError, SpecError, TableError, OSError, ValueError) as e:
        print(f"ingest failed: {e}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    if is_document:
        print_document_report(document, args.specs_dir)
    else:
        print_report(report, args.specs_dir)
    return 0


def _drafts(config: Config, transport: Transport | None, store) -> Callable[[str, str], dict]:
    """Turns a disruption report into a draft disruption; the server plans it when it next runs."""
    from scgraph.ops.service import OpsService
    from scgraph.ops.store import OpsStore

    def draft(text: str, name: str) -> dict:
        service = OpsService(OpsStore(config.ops_db_path), store, lambda: LLMClient(config, transport=transport),
                             config=config)
        return service.draft_from_document(text, name, config.author)

    return draft


def print_document_report(report: DocumentReport, specs_dir: Path) -> None:
    print(f"document {report.file}, run {report.run_id}")
    if report.disruption_text is not None:
        d = report.disruption
        print(f"  disruption report: draft disruption {d['id']} {d['name']!r} (thread {d['thread_id']})" if d else
              "  disruption report: no draft created (not loaded)")
        for warning in (d or {}).get("warnings", [])[:LIST_LIMIT]:
            print(f"    warning: {warning}")
    for table in report.tables:
        sources = sorted({r.source for r in table.regions}) or ["no regions"]
        print(f"  table {table.file}: {', '.join(sources)}, {len(table.facts)} facts")
    if report.doc_class:
        print(f"  text: domains {', '.join(report.doc_class.domains)}; types {', '.join(report.doc_class.types)}")
    print(f"  chunks {report.chunks} ({len(report.chunk_errors)} failed), facts extracted {report.extracted},"
          f" rejected {len(report.rejected)}, invalid {len(report.invalid)}, flagged {len(report.flagged)}")
    print("  resolution: " + (", ".join(f"{m} {n}" for m, n in sorted(report.resolutions.items())) or "none"))
    for type_name, mention, node_id in report.unresolved[:LIST_LIMIT]:
        print(f"    unresolved {type_name} {mention!r} -> {node_id}")
    for title, items in (("rejected", report.rejected), ("flagged", report.flagged), ("invalid", report.invalid),
                         ("failed chunks", report.chunk_errors), ("errors", report.errors)):
        for item in items[:LIST_LIMIT]:
            print(f"  {title}: {item}")
    if report.load:
        counts = report.load.counts()
        print(f"loaded {counts['loaded']} facts, rejected {counts['rejected']}, flagged fields {counts['flagged']}")
        print(f"facts applied {counts['applied']}, proposed {counts['proposed']}"
              f" ({counts['proposals_created']} new proposals, {counts['proposals_updated']} updated),"
              f" unchanged {counts['unchanged']}, dropped {counts['dropped']}")
    else:
        print(f"not loaded ({len(report.facts)} facts)")
    t = report.llm_totals()
    print(f"LLM: {t['calls']} calls, {t['cache_hits']} cache hits, {t['input_tokens']} in / {t['output_tokens']} out"
          f" tokens, ${t['cost_usd']:.4f}")


def print_report(report: IngestReport, specs_dir: Path) -> None:
    print(f"ingest {report.file}, run {report.run_id}, specs in {specs_dir}"
          + (f", read as {report.encoding}" if report.encoding else ""))
    for sheet, reason in report.ignored.items():
        print(f"  sheet {sheet}: no facts ({reason})")
    for r in report.regions:
        rows = report.results[r.name].rows if r.name in report.results else {}
        size = f": {sum(rows.values())} rows" if r.spec.get("regions") else ""
        print(f"  region {r.name} [{r.kind}, {r.source}] on {r.sheet!r}{size}"
              + (f", {r.repair_rounds} repair round(s)" if r.repair_rounds else ""))
        for region in r.spec.get("regions", []):
            for header, c in (*region.get("key_columns", {}).items(), *region["columns"].items()):
                what = c.get("target") or c.get("metric") or ""
                print(f"    {header:<44} {c['role']:<8} {what:<38} {c.get('confidence', 1.0):.2f}")
        for p in r.spec.get("parameters", []):
            print(f"    parameter {p['name']} = {p['sheet']}!{p['cell']}")
        for row in r.excluded_rows:
            print(f"    excluded row {row['row']} {row['label']!r}: {row['reason']}")
        for problem in r.problems:
            print(f"    FLAGGED: {problem}")
    for t in report.text_fields:
        print(f"  text column {t.column!r} ({t.region}): parser coverage {t.coverage:.0%} ({t.parsed} of {t.cells}),"
              f" {t.llm_loaded} cells by the model, {len(t.unparsed)} unparsed, {t.rejected_quotes} quotes rejected")
        for label in t.unparsed[:LIST_LIMIT]:
            print(f"    unparsed {label}")
    for error in report.errors:
        print(f"  ERROR: {error}")
    if report.load:
        counts = report.load.counts()
        print(f"loaded {counts['loaded']} facts, rejected {counts['rejected']}, flagged fields {counts['flagged']},"
              f" referenced-only {counts['referenced_only']}, oracle values {counts['oracle_values']},"
              f" parameters {counts['parameters']}")
        print(f"facts applied {counts['applied']}, proposed {counts['proposed']}"
              f" ({counts['proposals_created']} new proposals, {counts['proposals_updated']} updated),"
              f" unchanged {counts['unchanged']}, dropped {counts['dropped']}")
    else:
        print(f"not loaded ({len(report.facts)} facts)")
    t = report.llm_totals()
    print(f"LLM: {t['calls']} calls, {t['cache_hits']} cache hits, {t['input_tokens']} in / {t['output_tokens']} out"
          f" tokens, ${t['cost_usd']:.4f}")


def _spec(action: str, path: Path, author: str) -> int:
    if not path.is_file():
        print(f"no spec at {path}", file=sys.stderr)
        return 1
    spec: dict[str, Any] = approve(path, author) if action == "approve" else json.loads(path.read_text())
    if action == "approve":
        print(f"approved {path}")
    for line in describe(spec):
        print(line)
    return 0


def _compare(args: argparse.Namespace, config: Config, transport: Transport | None) -> int:
    file, reference, specs_dir = args.file, args.reference, args.specs_dir
    client = LLMClient(config, transport=transport) if file.suffix.lower() in (".pdf", *TEXT_SUFFIXES) else None
    try:
        c = compare(file, reference, specs_dir, reference_file=args.reference_file,
                    slice_assembly=args.slice_assembly, client=client)
    except (IngestError, SpecError, TableError, StepError, LLMError, OSError, ValueError) as e:
        print(f"compare failed: {e}", file=sys.stderr)
        return 1
    scope = f", slice of assembly {args.slice_assembly}" if args.slice_assembly else ""
    print(f"{file.name} (specs {specs_dir}) vs {reference} on {(args.reference_file or file).name}{scope}")
    for name, a in (("node keys", c.nodes), ("edges", c.edges), ("field values", c.fields)):
        print(f"  {name:<13} {a.matched}/{a.total} ({a.share:.1%}), missing {len(a.missing)}, extra {len(a.extra)}")
        for item in a.missing[:LIST_LIMIT]:
            print(f"    missing {item}")
    print(f"  role differences {len(c.role_differences)}, reference-derived columns not derived {len(c.derived_missing)}")
    for sheet, header, ref_role, gen_role in c.role_differences:
        print(f"    {sheet}: {header!r} reference {ref_role}, generated {gen_role}")
    return 0
