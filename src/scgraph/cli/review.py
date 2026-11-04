"""`scgraph proposals ...`, `scgraph review threshold`, `scgraph edit ...`, `scgraph evidence add`, `scgraph journal`,
`scgraph serve`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from scgraph.config import Config
from scgraph.editing.edits import OPS, EditBatchError
from scgraph.editing.evidence import EvidenceError, EvidenceStore
from scgraph.editing.journal import UndoError, undo
from scgraph.editing.service import apply_edits
from scgraph.review.preview import by_impact, preview
from scgraph.review.service import ReviewError, accept, describe, reject

LIST_LIMIT = 10
EDIT_USAGE = """usage:
  scgraph edit create_node TYPE ID [--field NAME=VALUE ...]
  scgraph edit set_field TYPE KEY FIELD VALUE
  scgraph edit add_edge TYPE KEY [--field NAME=VALUE ...]
  scgraph edit delete TYPE KEY [--field NAME] [--reason TEXT]
  scgraph edit merge TYPE FROM INTO
  scgraph edit --file EDITS.json
evidence for every edit: [--evidence ID] [--page N] [--quote TEXT] [--url URL] [--comment TEXT]
KEY: parts separated by commas, an empty part is null (SUPPLIES: SUPPLIER,COMPONENT,SITE).
VALUE: JSON (9, 9.5, true, "text"), anything else is text."""


def add_commands(commands: argparse._SubParsersAction) -> None:
    proposals = commands.add_parser("proposals", help="review queue: list, show, accept, reject proposals")
    sub = proposals.add_subparsers(dest="proposals_command", metavar="PROPOSALS_COMMAND", required=True)

    ls = sub.add_parser("list", help="proposals with kind, summary, facts, confidence and impact")
    ls.add_argument("--status", default="pending", choices=("pending", "accepted", "rejected", "all"))
    ls.add_argument("--kind", choices=("mapping", "statement", "resolution", "conflict", "restore"))
    ls.add_argument("--sort", default="time", choices=("time", "impact"))
    ls.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="one proposal with its facts and impact preview")
    show.add_argument("id", type=int)
    show.set_defaults(func=cmd_show)

    for name, func in (("accept", cmd_accept), ("reject", cmd_reject)):
        p = sub.add_parser(name, help=f"{name} a proposal as a whole")
        p.add_argument("id", type=int)
        p.add_argument("--comment")
        _author(p)
        if name == "accept":
            p.add_argument("--target-id", help="resolution: the existing node the mention means")
            p.add_argument("--new-node", nargs="?", const=True, metavar="ID",
                           help="resolution: create a node for the mention (id defaults to the mention text)")
        p.set_defaults(func=func)

    review = commands.add_parser("review", help="review settings")
    review_sub = review.add_subparsers(dest="review_command", metavar="REVIEW_COMMAND", required=True)
    review_sub.add_parser("threshold", help="the confidence at or above which facts apply without review"
                          ).set_defaults(func=cmd_threshold)

    edit = commands.add_parser("edit", help="change the graph by hand", usage=EDIT_USAGE)
    edit.add_argument("words", nargs="*")
    edit.add_argument("--file", type=Path, help="a JSON list of edits, applied all or none")
    edit.add_argument("--field", action="append", default=[], metavar="NAME[=VALUE]")
    edit.add_argument("--reason")
    for name in ("evidence", "quote", "url", "comment"):
        edit.add_argument(f"--{name}")
    edit.add_argument("--page", type=int)
    _author(edit)
    edit.set_defaults(func=cmd_edit)

    evidence = commands.add_parser("evidence", help="evidence files for edits")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", metavar="EVIDENCE_COMMAND", required=True)
    add = evidence_sub.add_parser("add", help="store a file; prints its id")
    add.add_argument("file", type=Path)
    add.set_defaults(func=cmd_evidence_add)

    journal = commands.add_parser("journal", help="the change journal, newest first; undo an entry")
    journal.add_argument("--limit", type=int, default=20)
    journal.add_argument("--before", type=int, metavar="ID", help="entries older than this id")
    journal.add_argument("--undo", type=int, metavar="ID", help="undo an accept, reject or edit")
    _author(journal)
    journal.set_defaults(func=cmd_journal)

    serve = commands.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--specs-dir", type=Path, default=None, help="stored mapping specs (default mappings/generated)")
    serve.set_defaults(func=cmd_serve)


def _author(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--author", help="who makes the change (default SCGRAPH_AUTHOR or the OS user)")


def _open(config: Config):
    if not config.graph_db_path.exists():
        print(f"no graph yet at {config.graph_db_path}; run `scgraph ingest FILE`")
        return None
    return config.graph_store()


def _confidence(conf: list) -> str:
    low, high = conf
    if low is None:
        return "-"
    return f"{low:.2f}" if low == high else f"{low:.2f}-{high:.2f}"


def _impact(impact: dict | None) -> str:
    if impact is None:
        return "impact ?"
    if impact["no_effect"]:
        return "no exposure effect"
    return f"impact {impact['max_impact_eur'] / 1e6:,.2f} M EUR, {impact['max_exposure_pp']:.2f} pp"


def cmd_list(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    status = None if args.status == "all" else args.status
    proposals = store.proposals(status=status, kind=args.kind)
    if args.sort == "impact":
        proposals = by_impact(store, proposals)
    print(f"{len(proposals)} proposal(s)" + (f" {status}" if status else ""))
    for p in proposals:
        extra = "" if p["status"] == "pending" else f" [{p['status']} by {p['decided_by']}]"
        print(f"#{p['id']:<4} {p['kind']:<10} {p['facts']:>5} facts  conf {_confidence(p['confidence']):<9}"
              f" {_impact(p['impact']):<40} {p['summary']}{extra}")
    store.close()
    return 0


def _show_fact(f: dict[str, Any]) -> str:
    what = f"{f['item']}.{f['field']} = {f['value']!r}" if f["field"] else f"{f['kind']} {f['item']}"
    current = f"  (now {f['current']!r})" if f.get("current") is not None else ""
    return f"{what}{current}  conf {f['confidence']:g} {f['method']}  from {f['source']}"


def cmd_show(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    try:
        p = describe(store, args.id)
    except ReviewError as e:
        print(e)
        return 1
    print(f"proposal #{p['id']} {p['kind']} [{p['status']}]: {p['summary']}")
    print(f"  {p['count']} facts, confidence {_confidence(p['confidence'])}, targets {', '.join(p['targets'])}")
    if p["decided_by"]:
        print(f"  {p['status']} by {p['decided_by']} at {p['decided_at']}" + (f": {p['comment']}" if p["comment"]
                                                                                else ""))
    if spec := p.get("spec"):
        if spec.get("missing"):
            print(f"  spec {spec['path']} (not found)")
        else:
            print(f"  spec {spec['path']}, region {spec['region']}, column {spec['column']!r}"
                  f" -> {spec.get('target') or 'keys'}, confidence {spec.get('confidence')}")
            if spec.get("rationale"):
                print(f"    rationale: {spec['rationale']}")
    if p["kind"] == "resolution":
        print(f"  mention {p['mention']!r}; candidates: {', '.join(p['candidates']) or 'none'}")
    if p.get("deletion"):
        d = p["deletion"]
        print(f"  deleted by {d['author']} at {d['ts']}" + (f": {d['reason']}" if d["reason"] else ""))
    for note in p["notes"]:
        print(f"  note: {note}")
    if p["kind"] == "conflict":
        print("  changes:")
        for c in p["changes"]:
            print(f"    {c['item']}.{c['field']}: {c['current']!r} -> {c['proposed']!r}  from {c['source']}")
    else:
        label = "facts" if p["count"] <= len(p["facts"]) else f"{len(p['facts'])} of {p['count']} facts"
        print(f"  {label}:")
        for f in p["facts"]:
            print(f"    {_show_fact(f)}")
    if p["status"] == "pending":
        impact = preview(store, p["id"])
        print(f"  {_impact(impact)}" + (f", {impact['changed']} entities change" if impact["changed"] else ""))
        for e in impact["effects"][:LIST_LIMIT]:
            line = (f"    {e['type']} {e['id']}: exposure {e['exposure_pct_before']:.4f}%"
                    f" -> {e['exposure_pct_after']:.4f}%")
            if "impact_eur_before" in e:
                line += f", impact {_eur(e['impact_eur_before'])} -> {_eur(e['impact_eur_after'])}"
            print(line)
    store.close()
    return 0


def _eur(value: float | None) -> str:
    return "unavailable" if value is None else f"{value / 1e6:,.2f} M EUR"


def cmd_accept(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    return _decide(args, config, accept, target_id=args.target_id, new_node=args.new_node)


def cmd_reject(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    return _decide(args, config, reject)


def _decide(args: argparse.Namespace, config: Config, action, **kw) -> int:
    if not (store := _open(config)):
        return 1
    try:
        decision = action(store, args.id, args.author or config.author, comment=args.comment, **kw)
    except ReviewError as e:
        print(e, file=sys.stderr)
        store.close()
        return 1
    print(f"journal #{decision.journal_id}: {decision.summary}")
    for warning in decision.warnings:
        print(f"  warning: {warning}")
    if decision.report and decision.report.proposals_created:
        print(f"  new proposals: {', '.join(f'#{i}' for i in decision.report.proposals_created)}")
    store.close()
    return 0


def cmd_threshold(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    source = "SCGRAPH_REVIEW_THRESHOLD" if os.environ.get("SCGRAPH_REVIEW_THRESHOLD") else "default"
    print(f"review threshold {config.review_threshold:g} ({source}): new facts at or above it apply without review")
    return 0


def _value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _key(text: str) -> list[str | None]:
    return [part or None for part in text.split(",")]


def _fields(items: list[str]) -> dict[str, Any]:
    out = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--field {item!r}: give NAME=VALUE")
        out[name] = _value(value)
    return out


def _edits_from_words(args: argparse.Namespace) -> list[dict[str, Any]]:
    words = args.words
    if not words or words[0] not in OPS:
        raise ValueError(f"first word is the op: {', '.join(OPS)}")
    op, rest = words[0], words[1:]
    need = {"create_node": 2, "set_field": 4, "add_edge": 2, "delete": 2, "merge": 3}[op]
    if len(rest) != need:
        raise ValueError(f"{op} takes {need} arguments, got {len(rest)}")
    if op == "merge":
        return [{"op": op, "type": rest[0], "from": rest[1], "into": rest[2]}]
    edit: dict[str, Any] = {"op": op, "type": rest[0], "key": _key(rest[1])}
    if op == "set_field":
        edit.update(field=rest[2], value=_value(rest[3]))
    elif op == "delete":
        edit.update(field=args.field[0] if args.field else None, reason=args.reason)
    else:
        edit["fields"] = _fields(args.field)
    return [edit]


def cmd_edit(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    try:
        if args.file:
            edits = json.loads(args.file.read_text())
            edits = edits if isinstance(edits, list) else edits.get("edits", [])
        else:
            edits = _edits_from_words(args)
    except (ValueError, OSError) as e:
        print(f"edit: {e}\n{EDIT_USAGE}", file=sys.stderr)
        return 2
    evidence = {k: v for k, v in (("id", args.evidence), ("page", args.page), ("quote", args.quote),
                                  ("url", args.url), ("comment", args.comment)) if v is not None}
    if not (store := _open(config)):
        return 1
    try:
        result = apply_edits(store, edits, args.author or config.author, evidence=evidence,
                             evidence_store=EvidenceStore(config.evidence_dir))
    except EditBatchError as e:
        print(f"no edit applied; {len(e.errors)} invalid:", file=sys.stderr)
        for i, message in e.errors:
            print(f"  edit {i + 1}: {message}", file=sys.stderr)
        store.close()
        return 1
    print(f"journal #{result.journal_id}: {len(result.edits)} edit(s) applied")
    for line in result.edits:
        print(f"  {line}")
    if result.report and result.report.proposals_created:
        print(f"  new proposals: {', '.join(f'#{i}' for i in result.report.proposals_created)}")
    store.close()
    return 0


def cmd_evidence_add(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    try:
        evidence_id = EvidenceStore(config.evidence_dir).add_file(args.file)
    except EvidenceError as e:
        print(e, file=sys.stderr)
        return 1
    print(evidence_id)
    return 0


def cmd_journal(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    if not (store := _open(config)):
        return 1
    if args.undo is not None:
        try:
            entry = undo(store, args.undo, args.author or config.author)
        except UndoError as e:
            print(e, file=sys.stderr)
            store.close()
            return 1
        print(f"journal #{entry}: undid #{args.undo}")
        store.close()
        return 0
    for e in store.journal(before=args.before, limit=args.limit):
        undone = f"  [undone by #{e['undone_by']}]" if e["undone_by"] else ""
        origin = f"  [from {e['origin']}]" if e["origin"] else ""
        print(f"#{e['id']:<5} {e['ts']}  {e['actor']:<12} {e['action']:<7} {e['summary']}{origin}{undone}")
    store.close()
    return 0


def cmd_serve(args: argparse.Namespace, config: Config, _transport: Any) -> int:
    import uvicorn

    from scgraph.api.app import create_app
    from scgraph.ingest.pipeline import DEFAULT_SPECS_DIR

    print(f"serving {config.graph_db_path} on http://{args.host}:{args.port} (app) and http://{args.host}:{args.port}/api")
    uvicorn.run(create_app(config, specs_dir=args.specs_dir or DEFAULT_SPECS_DIR), host=args.host, port=args.port)
    return 0
