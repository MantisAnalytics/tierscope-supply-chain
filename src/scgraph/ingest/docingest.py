"""Document ingest: tables through table ingest, text through quoted extraction and entity resolution."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scgraph.graph.facts import Fact, OracleValue, Parameter
from scgraph.graph.rows import LoadReport
from scgraph.graph.schema import SCHEMA, key_id
from scgraph.graph.store import GraphStore
from scgraph.ingest.documents import chunks, read_document
from scgraph.ingest.pipeline import DEFAULT_SPECS_DIR, IngestError, IngestReport, ingest
from scgraph.ingest.resolve import (CONTEXT_FIELDS, NAME_FIELDS, Pending, Resolution, Resolver, adjudicate,
                                    normalise_name, unresolved)
from scgraph.ingest.step import StepError
from scgraph.ingest.textextract import DocClass, RawFact, classify_document, extract_chunk, typed_value, verify
from scgraph.ingest.validate import graph_ids
from scgraph.llm import LLMClient, Response
from scgraph.mapping.tables import TableFile
from scgraph.mapping.transforms import TransformError


@dataclass
class DocumentReport:
    file: str
    run_id: str
    doc_class: DocClass | None = None
    tables: list[IngestReport] = field(default_factory=list)
    chunks: int = 0
    chunk_errors: list[str] = field(default_factory=list)
    extracted: int = 0
    rejected: list[str] = field(default_factory=list)  # quote not in the chunk
    invalid: list[str] = field(default_factory=list)  # not fitting the schema, or value not convertible
    flagged: list[str] = field(default_factory=list)  # value not in its quote
    resolutions: Counter = field(default_factory=Counter)  # method -> mentions
    unresolved: list[tuple[str, str, str]] = field(default_factory=list)  # type, mention, provisional id
    errors: list[str] = field(default_factory=list)
    # A disruption report: its text, for the caller to turn into a draft disruption (no facts are loaded), and
    # what the caller made of it ({id, name, thread_id, warnings} of the draft).
    disruption_text: str | None = None
    disruption: dict[str, Any] | None = None
    facts: list[Fact] = field(default_factory=list)
    load: LoadReport | None = None
    responses: list[Response] = field(default_factory=list)

    def all_responses(self) -> list[Response]:
        return self.responses + [r for t in self.tables for r in t.responses]

    def llm_totals(self) -> dict[str, Any]:
        responses = self.all_responses()
        return {
            "calls": len(responses),
            "cache_hits": sum(r.cache_hit for r in responses),
            "input_tokens": sum(r.usage.input_tokens for r in responses),
            "output_tokens": sum(r.usage.output_tokens for r in responses),
            "cost_usd": sum(r.cost_usd for r in responses),
        }


def ingest_document(path: str | Path, client: LLMClient | None, *, store: GraphStore | None = None,
                    specs_dir: Path = DEFAULT_SPECS_DIR, reuse_only: bool = False,
                    run_id: str | None = None, name: str | None = None, lock: AbstractContextManager | None = None,
                    batch_values: set[str] | None = None, context_facts: Iterable[Fact] = ()) -> DocumentReport:
    """Ingest a PDF or text document. `name` names it in reports and source locations (default: the file name).

    In a batch, `batch_values` are the cell texts of the other files (references to them count as resolved) and
    `context_facts` the facts of its table files, whose entities mentions resolve to. `lock` is held around every
    access to `store`; model calls run outside it.
    """
    path = Path(path)
    run_id = run_id or uuid.uuid4().hex[:12]
    guard = lock or nullcontext()
    doc = read_document(path)
    doc.name = name or doc.name
    report = DocumentReport(doc.name, run_id)
    oracle: list[OracleValue] = []
    parameters: list[Parameter] = []
    values = set(batch_values or ())
    if doc.tables and store is not None:
        with guard:
            values |= {i for ids in graph_ids(store).values() for i in ids}

    for table in doc.tables:
        source = TableFile.from_rows(doc.name, table.rows, label=table.name)
        try:
            result = ingest(source, client, store=None, specs_dir=specs_dir, reuse_only=reuse_only, run_id=run_id,
                            batch_values=values)
        except (IngestError, StepError) as e:
            report.errors.append(f"{table.name}: {e}")
            continue
        report.tables.append(result)
        report.facts += result.facts
        oracle += [o for r in result.results.values() for o in r.oracle]
        parameters += [p for r in result.results.values() for p in r.parameters]

    raws: list[RawFact] = []
    if doc.blocks and client is not None and not reuse_only:
        try:
            step = classify_document(client, doc)
            report.responses += step.responses
            report.doc_class = step.value
        except StepError as e:
            report.errors.append(f"classification: {e}")
        if report.doc_class is not None and report.doc_class.disruption_report:
            report.disruption_text = "\n\n".join(b.text for b in doc.blocks)
        elif report.doc_class is not None:
            for block in doc.blocks:
                for chunk in chunks(block):
                    report.chunks += 1
                    try:
                        step = extract_chunk(client, chunk, report.doc_class)
                    except StepError as e:
                        report.chunk_errors.append(f"{doc.location(block, chunk.start, chunk.start).label()}: {e}")
                        continue
                    report.responses += step.responses
                    report.invalid += step.value.invalid
                    for raw in step.value.facts:
                        raw.chunk = report.chunks - 1
                        report.extracted += 1
                        if not verify(raw, chunk, doc):
                            report.rejected.append(f"{raw.type} {raw.quote!r}")
                        else:
                            raws.append(raw)
    elif doc.blocks and reuse_only:
        report.errors.append("text not extracted: --reuse-only makes no model calls")

    if report.disruption_text is not None:
        report.facts = []  # a disruption report adds nothing to the graph
        return report
    with guard:
        resolver = Resolver(store)
    report.facts += _facts_from_text(raws, client if not reuse_only else None, resolver, report, run_id,
                                     list(context_facts))
    if store is not None:
        with guard:
            report.load = store.load(report.facts, inputs=[str(path) if doc.name == path.name else doc.name],
                                     oracle=oracle, parameters=parameters, run_id=run_id)
    return report


def _facts_from_text(raws: list[RawFact], client: LLMClient | None, resolver: Resolver,
                     report: DocumentReport, run_id: str, context_facts: list[Fact] | None = None) -> list[Fact]:
    for f in [*(context_facts or []), *report.facts]:  # entities the batch and this document's tables describe
        if f.kind == "entity":
            resolver.add(f.type, key_id(f.key))
        elif f.kind == "field" and f.type in SCHEMA.entities and f.field in (*NAME_FIELDS.values(), *CONTEXT_FIELDS):
            resolver.add(f.type, key_id(f.key), {f.field: f.value})
    # Entities the text introduces: the mention is the id, unless the graph has it already or it is the name
    # the text gives another introduced entity ("Supplier S1 is Acme Inc." also yields an entity fact "Acme Inc.").
    named = {(r.type, normalise_name(str(r.value))) for r in raws
             if r.kind == "field" and r.type in NAME_FIELDS and r.field == NAME_FIELDS[r.type]
             and normalise_name(str(r.value)) != normalise_name(r.mention)}
    for raw in raws:
        if raw.kind == "entity" and (raw.type, normalise_name(raw.mention)) not in named:
            found = resolver.exact(raw.type, raw.mention)
            resolver.add(raw.type, found.id if found else raw.mention)
    for raw in raws:
        if raw.kind == "field" and raw.type in SCHEMA.entities and raw.field in (*NAME_FIELDS.values(),
                                                                                 *CONTEXT_FIELDS):
            if found := resolver.exact(raw.type, raw.mention):
                resolver.add(raw.type, found.id, {raw.field: raw.value})

    mentions: dict[tuple[str, str], Resolution | None] = {}
    pending: dict[tuple[str, str], Pending] = {}
    for raw in raws:
        for type_name, mention in _mentions(raw):
            if (type_name, mention) in mentions:
                continue
            local = resolver.resolve_local(type_name, mention)
            mentions[(type_name, mention)] = local
            if local is None:
                pending[(type_name, mention)] = Pending(type_name, mention, raw.quote,
                                                        resolver.candidates(type_name, mention))
    if pending:
        try:
            decided, responses = adjudicate(client, list(pending.values()))
        except StepError as e:
            report.errors.append(f"resolution: {e}")
            decided, responses = [unresolved(p.type, p.mention, [c["id"] for c in p.candidates])
                                  for p in pending.values()], []
        report.responses += responses
        mentions.update(zip(pending, decided, strict=True))

    facts: list[Fact] = []
    created: set[tuple[str, str]] = set()
    for (type_name, mention), res in mentions.items():
        report.resolutions[res.method] += 1
        if res.unresolved and (type_name, res.id) not in created:
            created.add((type_name, res.id))
            report.unresolved.append((type_name, mention, res.id))
    for raw in raws:
        resolved = [(t, m, mentions[(t, m)]) for t, m in _mentions(raw)]
        known = [r for _, _, r in resolved if not r.unresolved]
        confidence = min([raw.confidence] + [r.confidence for r in known])
        record = _record([r for _, _, r in resolved])
        common = {"method": "llm", "confidence": round(confidence, 4), "run_id": run_id, "resolution": record,
                  "origin": _origin(raw, report)}
        if raw.flagged:
            report.flagged.append(raw.source.label())
        ids = [r.id for _, _, r in resolved]
        if raw.kind == "entity":
            facts.append(Fact("entity", raw.type, (ids[0],), raw.source, **common))
            continue
        if raw.type in SCHEMA.entities:
            key: tuple = (ids[0],)
        else:
            rel = SCHEMA.relation(raw.type)
            refs = iter(ids[2:])
            key = (ids[0], ids[1], *((next(refs) if f.ref else raw.identity[f.name]) if f.name in raw.identity
                                     else None for f in rel.identity))
        if raw.kind == "relation":
            facts.append(Fact("relation", raw.type, key, raw.source, **common))
            continue
        try:
            value = typed_value(raw)
        except TransformError as e:
            report.invalid.append(f"{raw.source.label()}: {e}")
            continue
        facts.append(Fact("field", raw.type, key, raw.source, field=raw.field, value=value, **common))
    for type_name, mention, node_id in report.unresolved:  # keep unresolved mentions visible as flagged nodes
        first = next(r for r in raws if (type_name, mention) in _mentions(r))
        record = {"method": "unresolved", "mention": mention, "chosen": node_id, "confidence": 0.0}
        common = {"method": "llm", "confidence": 0.0, "run_id": run_id, "resolution": record,
                  "origin": _origin(first, report)}
        facts.append(Fact("entity", type_name, (node_id,), first.source, **common))
        name_field = NAME_FIELDS.get(type_name)
        if name_field:
            facts.append(Fact("field", type_name, (node_id,), first.source, field=name_field, value=mention, **common))
    return facts


def _origin(raw: RawFact, report: DocumentReport) -> dict[str, Any]:
    """The document passage a text fact came from; facts sharing a quote are reviewed together."""
    return {"document": report.file, "chunk": raw.chunk, "quote": raw.quote}


def _mentions(raw: RawFact) -> list[tuple[str, str]]:
    """(expected type, mention) for every node reference of a raw fact, in key order."""
    if raw.type in SCHEMA.entities:
        return [(raw.type, raw.mention)]
    rel = SCHEMA.relation(raw.type)
    out = [(rel.src, raw.src), (rel.dst, raw.dst)]
    out += [(f.ref, raw.identity[f.name]) for f in rel.identity if f.name in raw.identity and f.ref]
    return out


def _record(resolutions: list[Resolution]) -> dict[str, Any]:
    if len(resolutions) == 1:
        return resolutions[0].record()
    weakest = min(resolutions, key=lambda r: (r.method == "id", r.confidence))
    return {"method": weakest.method, "confidence": weakest.confidence, "mentions": [r.record() for r in resolutions]}
